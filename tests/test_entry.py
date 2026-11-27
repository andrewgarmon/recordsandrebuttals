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

from conftest import RAW_SHEET_CSV


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


def test_club_data_requires_doc_id(entry):
    resp = call(entry, "https://x.test/api/club-data")
    assert resp.status == 500
    assert "SHEETS_DOC_ID" in json.loads(resp.body)["error"]
    assert entry._test.cache.store == {}  # errors are not cached


def test_club_data_parses_sheet_and_caches(entry):
    entry._test.routes["https://docs.google.com/"] = FakeResponse(RAW_SHEET_CSV)
    resp = call(entry, "https://x.test/api/club-data?bust=1", SHEETS_DOC_ID="DOC")
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["overview"]["album_count"] == 4
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert entry._test.calls == [
        "https://docs.google.com/spreadsheets/d/DOC/gviz/tq?tqx=out:csv&sheet=Albums"
    ]
    # Second request is served from the edge cache (query string ignored).
    again = call(entry, "https://x.test/api/club-data", SHEETS_DOC_ID="DOC")
    assert json.loads(again.body) == payload
    assert len(entry._test.calls) == 1


def test_club_data_uses_gid_when_set(entry):
    entry._test.routes["https://docs.google.com/"] = FakeResponse(RAW_SHEET_CSV)
    call(entry, "https://x.test/api/club-data", SHEETS_DOC_ID="DOC", SHEETS_GID="42")
    assert entry._test.calls == ["https://docs.google.com/spreadsheets/d/DOC/export?format=csv&gid=42"]


def test_club_data_sheet_failure_is_502(entry):
    entry._test.routes["https://docs.google.com/"] = FakeResponse("denied", status=403)
    assert call(entry, "https://x.test/api/club-data", SHEETS_DOC_ID="DOC").status == 502


def test_club_data_bad_sheet_shape_is_502(entry):
    entry._test.routes["https://docs.google.com/"] = FakeResponse("a,b\n1,2\n")
    resp = call(entry, "https://x.test/api/club-data", SHEETS_DOC_ID="DOC")
    assert resp.status == 502 and "Average" in json.loads(resp.body)["error"]


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
