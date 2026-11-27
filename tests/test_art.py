import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

import art
from art import (
    ArtResolver,
    DeezerProvider,
    ITunesProvider,
    LastFmProvider,
    MusicBrainzCaaProvider,
    cache_key,
    default_providers,
    get_cover,
    names_match,
)

PNG = b"\x89PNG fake image bytes"


def run(coro):
    return asyncio.run(coro)


class FakeHttp:
    """Maps URL prefixes to canned JSON / bytes and records requests."""

    def __init__(self, json_routes=None, byte_routes=None):
        self.json_routes = json_routes or {}
        self.byte_routes = byte_routes or {}
        self.calls: list[tuple[str, dict | None]] = []

    def _match(self, routes, url):
        for prefix, value in routes.items():
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        return None

    async def get_json(self, url, headers=None):
        self.calls.append((url, headers))
        return self._match(self.json_routes, url)

    async def get_bytes(self, url, headers=None):
        self.calls.append((url, headers))
        return self._match(self.byte_routes, url)


class FakeProvider:
    def __init__(self, name, url=None, exc=None):
        self.name, self.url, self.exc = name, url, exc
        self.called = False

    async def find_cover_url(self, artist, album):
        self.called = True
        if self.exc:
            raise self.exc
        return self.url


class MemoryCache:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def put(self, key, body, content_type):
        self.store[key] = (body, content_type)


# -- helpers ---------------------------------------------------------------


def test_names_match_is_loose():
    assert names_match("The Beatles", "Beatles")
    assert names_match("Simon & Garfunkel", "Simon and Garfunkel")
    assert names_match("Björk", "Bjork")
    assert names_match("OK Computer", "OK Computer (Remastered)")
    assert not names_match("Radiohead", "Muse")
    assert not names_match("Radiohead", None)


def test_cache_key():
    assert cache_key("Radiohead", "OK Computer") == "radiohead__ok-computer"
    assert cache_key("Björk", "Homogenic") == "bjork__homogenic"
    key = cache_key("坂本龍一", "千のナイフ")
    assert key.startswith("h") and "__h" in key
    assert key == cache_key("坂本龍一", "千のナイフ")


# -- providers -------------------------------------------------------------


def test_deezer_picks_matching_result():
    http = FakeHttp({"https://api.deezer.com/": {"data": [
        {"title": "Other", "artist": {"name": "Someone"}, "cover_xl": "https://x/wrong.jpg"},
        {"title": "OK Computer", "artist": {"name": "Radiohead"}, "cover_xl": "https://x/ok.jpg"},
    ]}})
    assert run(DeezerProvider(http).find_cover_url("Radiohead", "OK Computer")) == "https://x/ok.jpg"
    q = parse_qs(urlparse(http.calls[0][0]).query)["q"][0]
    assert q == 'artist:"Radiohead" album:"OK Computer"'


def test_deezer_rejects_non_matching_results():
    http = FakeHttp({"https://api.deezer.com/": {"data": [
        {"title": "Absolution", "artist": {"name": "Muse"}, "cover_xl": "https://x/muse.jpg"}
    ]}})
    assert run(DeezerProvider(http).find_cover_url("Radiohead", "OK Computer")) is None


def test_itunes_upsizes_artwork():
    http = FakeHttp({"https://itunes.apple.com/": {"results": [
        {"artistName": "Radiohead", "collectionName": "OK Computer",
         "artworkUrl100": "https://is1/a/100x100bb.jpg"}
    ]}})
    url = run(ITunesProvider(http).find_cover_url("Radiohead", "OK Computer"))
    assert url == "https://is1/a/600x600bb.jpg"


def test_lastfm_prefers_extralarge_and_skips_empty():
    http = FakeHttp({"https://ws.audioscrobbler.com/": {"album": {"image": [
        {"size": "large", "#text": "https://lf/large.png"},
        {"size": "extralarge", "#text": "https://lf/xl.png"},
    ]}}})
    assert run(LastFmProvider(http, "KEY").find_cover_url("A", "B")) == "https://lf/xl.png"
    params = parse_qs(urlparse(http.calls[0][0]).query)
    assert params["api_key"] == ["KEY"] and params["method"] == ["album.getinfo"]

    empty = FakeHttp({"https://ws.audioscrobbler.com/": {"album": {"image": [
        {"size": "extralarge", "#text": ""}, {"size": "mega", "#text": " "}
    ]}}})
    assert run(LastFmProvider(empty, "KEY").find_cover_url("A", "B")) is None


def test_lastfm_handles_error_payload():
    http = FakeHttp({"https://ws.audioscrobbler.com/": {"error": 6, "message": "Album not found"}})
    assert run(LastFmProvider(http, "KEY").find_cover_url("A", "B")) is None


def test_musicbrainz_builds_caa_url_and_sends_user_agent():
    http = FakeHttp({"https://musicbrainz.org/": {"release-groups": [
        {"id": "rg-123", "title": "OK Computer", "artist-credit": [{"name": "Radiohead"}]}
    ]}})
    url = run(MusicBrainzCaaProvider(http).find_cover_url("Radiohead", "OK Computer"))
    assert url == "https://coverartarchive.org/release-group/rg-123/front-500"
    assert http.calls[0][1]["User-Agent"] == art.USER_AGENT


def test_default_providers_order():
    http = FakeHttp()
    assert [p.name for p in default_providers(http)] == ["deezer", "itunes", "musicbrainz"]
    assert [p.name for p in default_providers(http, "KEY")] == [
        "deezer", "itunes", "lastfm", "musicbrainz"
    ]


# -- resolver --------------------------------------------------------------


def test_resolver_fails_over_past_errors_and_misses():
    broken = FakeProvider("broken", exc=RuntimeError("boom"))
    empty = FakeProvider("empty")
    good = FakeProvider("good", url="https://img/good.jpg")
    never = FakeProvider("never", url="https://img/never.jpg")
    resolver = ArtResolver([broken, empty, good, never], FakeHttp())
    assert run(resolver.find_cover_url("A", "B")) == ("good", "https://img/good.jpg")
    assert not never.called
    assert resolver.errors[0][0] == "broken"


def test_fetch_cover_skips_urls_that_fail_to_download():
    http = FakeHttp(byte_routes={
        "https://img/dead.jpg": None,
        "https://img/html": (b"<html>", "text/html"),
        "https://img/ok.png": (PNG, "image/png"),
    })
    resolver = ArtResolver([
        FakeProvider("dead", url="https://img/dead.jpg"),
        FakeProvider("html", url="https://img/html"),
        FakeProvider("ok", url="https://img/ok.png"),
    ], http)
    cover = run(resolver.fetch_cover("A", "B"))
    assert cover.source == "ok" and cover.body == PNG and cover.content_type == "image/png"


def test_fetch_cover_none_when_all_fail():
    resolver = ArtResolver([FakeProvider("a"), FakeProvider("b", exc=ValueError())], FakeHttp())
    assert run(resolver.fetch_cover("A", "B")) is None


def test_fetch_cover_isolates_download_exceptions():
    http = FakeHttp(byte_routes={
        "https://img/raise": OSError("network"),
        "https://img/ok": (PNG, "image/jpeg"),
    })
    resolver = ArtResolver(
        [FakeProvider("a", url="https://img/raise"), FakeProvider("b", url="https://img/ok")], http
    )
    assert run(resolver.fetch_cover("A", "B")).source == "b"


# -- caching ---------------------------------------------------------------


def test_get_cover_writes_through_and_reads_cache():
    http = FakeHttp(byte_routes={"https://img/ok": (PNG, "image/png")})
    provider = FakeProvider("p", url="https://img/ok")
    cache = MemoryCache()

    first = run(get_cover("Radiohead", "OK Computer", ArtResolver([provider], http), cache))
    assert first.source == "p"
    assert cache.store["radiohead__ok-computer"] == (PNG, "image/png")

    provider.called = False
    second = run(get_cover("Radiohead", "OK Computer", ArtResolver([provider], http), cache))
    assert second.source == "cache" and second.body == PNG
    assert not provider.called


def test_get_cover_without_cache():
    http = FakeHttp(byte_routes={"https://img/ok": (PNG, "image/png")})
    cover = run(get_cover("A", "B", ArtResolver([FakeProvider("p", url="https://img/ok")], http)))
    assert cover.source == "p"


def test_get_cover_survives_broken_cache():
    class BrokenCache:
        async def get(self, key):
            raise RuntimeError("r2 down")

        async def put(self, key, body, content_type):
            raise RuntimeError("r2 down")

    http = FakeHttp(byte_routes={"https://img/ok": (PNG, "image/png")})
    resolver = ArtResolver([FakeProvider("p", url="https://img/ok")], http)
    cover = run(get_cover("A", "B", resolver, BrokenCache()))
    assert cover.source == "p"
    assert [e[0] for e in resolver.errors] == ["cache.get", "cache.put"]


def test_get_cover_miss_is_not_cached():
    cache = MemoryCache()
    assert run(get_cover("A", "B", ArtResolver([FakeProvider("p")], FakeHttp()), cache)) is None
    assert cache.store == {}
