"""Routing tests for entry.py with stand-ins for the Workers runtime.

The real `js` and `workers` modules only exist inside Cloudflare's Pyodide
runtime, so these fakes cover just what entry.py touches. They check our
routing, caching and error handling, not Cloudflare's APIs.
"""

import asyncio
import json
import sys
import types

import pytest



class FakeResponse:
    def __init__(self, body=None, status=None, status_text="", headers=None):
        if isinstance(body, FakeResponse):  # Response(js_response)
            body, status, headers = body.body, body.status, body.headers
        self.body = body
        self.status = status or 200
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.ok = 200 <= self.status < 300

    @property
    def js_object(self):
        return self

    def clone(self):
        return FakeResponse(self.body, self.status, headers=self.headers)

    async def text(self):
        return self.body if isinstance(self.body, str) else self.body.decode()

    async def bytes(self):
        return self.body if isinstance(self.body, bytes) else self.body.encode()


class FakeCache:
    def __init__(self):
        self.store = {}

    async def match(self, key):
        return self.store.get(key)

    async def put(self, key, resp):
        self.store[key] = resp


class FakeAssets:
    async def fetch(self, request):
        return FakeResponse("static:" + request.url)


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeKV:
    def __init__(self, data=None):
        self.data = data or {}
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return self.data.get(key)


class Env:
    def __init__(self, **kw):
        self.ASSETS = FakeAssets()
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def entry(monkeypatch):
    cache = FakeCache()
    routes = {}
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        for prefix, resp in routes.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse("nope", status=404)

    class WorkerEntrypoint:
        def __init__(self, ctx, env):
            self.ctx, self.env = ctx, env

    js = types.ModuleType("js")
    js.caches = types.SimpleNamespace(default=cache)
    workers = types.ModuleType("workers")
    workers.Response = FakeResponse
    workers.WorkerEntrypoint = WorkerEntrypoint
    workers.fetch = fake_fetch
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "workers", workers)
    sys.modules.pop("entry", None)
    import entry as module

    module._test = types.SimpleNamespace(cache=cache, routes=routes, calls=calls)
    yield module
    sys.modules.pop("entry", None)


def call(entry, url, **env):
    worker = entry.Default(None, Env(**env))
    return asyncio.run(worker.fetch(FakeRequest(url)))


def test_static_falls_through_to_assets(entry):
    resp = call(entry, "https://x.test/index.html")
    assert resp.body == "static:https://x.test/index.html"


def test_unknown_api_route_404(entry):
    assert call(entry, "https://x.test/api/nope").status == 404


def test_club_data_requires_kv_binding(entry):
    resp = call(entry, "https://x.test/api/club-data")
    assert resp.status == 500
    assert "CLUB_DATA" in json.loads(resp.body)["error"]
    assert entry._test.cache.store == {}  # errors are not cached


def test_club_data_empty_kv_is_503_and_not_cached(entry):
    resp = call(entry, "https://x.test/api/club-data", CLUB_DATA=FakeKV())
    assert resp.status == 503
    assert entry._test.cache.store == {}


def test_club_data_passes_kv_json_through_and_caches(entry):
    kv = FakeKV({"club-data": '{"overview": {"album_count": 4}}'})
    resp = call(entry, "https://x.test/api/club-data?bust=1", CLUB_DATA=kv)
    assert resp.status == 200
    assert json.loads(resp.body) == {"overview": {"album_count": 4}}
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["cache-control"] == "public, max-age=300"
    # Second request comes from the edge cache (query string ignored), not KV.
    again = call(entry, "https://x.test/api/club-data", CLUB_DATA=kv)
    assert again.body == resp.body
    assert kv.gets == 1
    assert entry._test.calls == []  # no outbound fetches


def test_album_art_requires_params(entry):
    assert call(entry, "https://x.test/api/album-art?artist=A").status == 400


def test_album_art_proxies_image_and_caches(entry):
    entry._test.routes["https://api.deezer.com/"] = FakeResponse(json.dumps({"data": [
        {"title": "OK Computer", "artist": {"name": "Radiohead"}, "cover_xl": "https://img.test/ok.jpg"}
    ]}))
    entry._test.routes["https://img.test/ok.jpg"] = FakeResponse(
        b"JPEGDATA", headers={"content-type": "image/jpeg"}
    )
    url = "https://x.test/api/album-art?artist=Radiohead&album=OK%20Computer"
    resp = call(entry, url)
    assert resp.status == 200 and resp.body == b"JPEGDATA"
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["x-art-source"] == "deezer"
    assert "https://x.test/api/album-art/radiohead__ok-computer" in entry._test.cache.store
    n = len(entry._test.calls)
    call(entry, url)
    assert len(entry._test.calls) == n


def test_album_art_miss_is_404_and_cached(entry):
    resp = call(entry, "https://x.test/api/album-art?artist=Nobody&album=Nothing")
    assert resp.status == 404
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert len(entry._test.cache.store) == 1
