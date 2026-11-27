"""Album cover lookup with a provider fallback chain and optional caching.

Nothing here imports the Workers runtime. Network access goes through an
injected :class:`HttpClient` and storage through an injected
:class:`ArtCache`, so the whole module runs under plain pytest with fakes.
``entry.py`` supplies the real implementations (``workers.fetch`` and an
R2 bucket).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode

USER_AGENT = "RecordsAndRebuttals/1.0 (https://recordsandrebuttals.com)"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------


class HttpClient(Protocol):
    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any | None:
        """Parsed JSON, or None on any non-2xx / network / parse failure."""

    async def get_bytes(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[bytes, str] | None:
        """(body, content type), or None on any failure."""


class ArtProvider(Protocol):
    name: str

    async def find_cover_url(self, artist: str, album: str) -> str | None: ...


class ArtCache(Protocol):
    async def get(self, key: str) -> tuple[bytes, str] | None: ...

    async def put(self, key: str, body: bytes, content_type: str) -> None: ...


@dataclass(frozen=True)
class CoverArt:
    body: bytes
    content_type: str
    source: str  # provider name, or "cache"


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Loose form for comparing names: no accents, case, punctuation or leading 'the'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def names_match(wanted: str, found: str | None) -> bool:
    if not found:
        return False
    a, b = normalize(wanted), normalize(found)
    return bool(a and b) and (a == b or a in b or b in a)


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cache_key(artist: str, album: str) -> str:
    """Stable storage key, e.g. ``radiohead__ok-computer``.

    Names that slug to nothing (non-Latin scripts) get a short hash instead.
    """
    parts = []
    for text in (artist, album):
        s = slug(text)
        if not s:
            s = "h" + hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]
        parts.append(s)
    return "__".join(parts)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class DeezerProvider:
    name = "deezer"

    def __init__(self, http: HttpClient):
        self.http = http

    async def find_cover_url(self, artist: str, album: str) -> str | None:
        q = f'artist:"{artist}" album:"{album}"'
        data = await self.http.get_json(
            "https://api.deezer.com/search/album?" + urlencode({"q": q, "limit": 10})
        )
        for item in (data or {}).get("data") or []:
            if names_match(artist, (item.get("artist") or {}).get("name")) and names_match(
                album, item.get("title")
            ):
                url = item.get("cover_xl") or item.get("cover_big")
                if url:
                    return url
        return None


class ITunesProvider:
    name = "itunes"

    def __init__(self, http: HttpClient):
        self.http = http

    async def find_cover_url(self, artist: str, album: str) -> str | None:
        params = {"term": f"{artist} {album}", "entity": "album", "limit": 10}
        data = await self.http.get_json("https://itunes.apple.com/search?" + urlencode(params))
        for item in (data or {}).get("results") or []:
            if names_match(artist, item.get("artistName")) and names_match(
                album, item.get("collectionName")
            ):
                url = item.get("artworkUrl100")
                if url:
                    return url.replace("100x100bb", "600x600bb")
        return None


class LastFmProvider:
    name = "lastfm"

    def __init__(self, http: HttpClient, api_key: str):
        self.http = http
        self.api_key = api_key

    async def find_cover_url(self, artist: str, album: str) -> str | None:
        params = {
            "method": "album.getinfo",
            "api_key": self.api_key,
            "artist": artist,
            "album": album,
            "autocorrect": 1,
            "format": "json",
        }
        data = await self.http.get_json(
            "https://ws.audioscrobbler.com/2.0/?" + urlencode(params)
        )
        images = ((data or {}).get("album") or {}).get("image") or []
        by_size = {img.get("size"): (img.get("#text") or "").strip() for img in images}
        for size in ("extralarge", "mega", "large"):
            if by_size.get(size):
                return by_size[size]
        return None


class MusicBrainzCaaProvider:
    name = "musicbrainz"

    def __init__(self, http: HttpClient):
        self.http = http

    async def find_cover_url(self, artist: str, album: str) -> str | None:
        query = f'releasegroup:"{_lucene_escape(album)}" AND artist:"{_lucene_escape(artist)}"'
        data = await self.http.get_json(
            "https://musicbrainz.org/ws/2/release-group/?"
            + urlencode({"query": query, "fmt": "json", "limit": 5}),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        for group in (data or {}).get("release-groups") or []:
            credit = " ".join(
                c.get("name") or (c.get("artist") or {}).get("name") or ""
                for c in group.get("artist-credit") or []
            )
            if group.get("id") and names_match(artist, credit) and names_match(
                album, group.get("title")
            ):
                return f"https://coverartarchive.org/release-group/{quote(group['id'])}/front-500"
        return None


def _lucene_escape(text: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", text)


def default_providers(http: HttpClient, last_fm_api_key: str | None = None) -> list[ArtProvider]:
    providers: list[ArtProvider] = [DeezerProvider(http), ITunesProvider(http)]
    if last_fm_api_key:
        providers.append(LastFmProvider(http, last_fm_api_key))
    providers.append(MusicBrainzCaaProvider(http))
    return providers


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


class ArtResolver:
    """Tries providers in order; a provider that errors is skipped, not fatal."""

    def __init__(self, providers: list[ArtProvider], http: HttpClient):
        self.providers = providers
        self.http = http
        self.errors: list[tuple[str, str]] = []

    async def find_cover_url(self, artist: str, album: str) -> tuple[str, str] | None:
        """(provider name, url) from the first provider with a match."""
        for provider in self.providers:
            try:
                url = await provider.find_cover_url(artist, album)
            except Exception as exc:  # noqa: BLE001 - isolate provider failures
                self.errors.append((provider.name, repr(exc)))
                continue
            if url:
                return provider.name, url
        return None

    async def fetch_cover(self, artist: str, album: str) -> CoverArt | None:
        """Image bytes from the first provider whose URL actually downloads."""
        for provider in self.providers:
            try:
                url = await provider.find_cover_url(artist, album)
                if not url:
                    continue
                got = await self.http.get_bytes(url, headers={"User-Agent": USER_AGENT})
            except Exception as exc:  # noqa: BLE001
                self.errors.append((provider.name, repr(exc)))
                continue
            if got is None:
                continue
            body, content_type = got
            if body and len(body) <= MAX_IMAGE_BYTES and content_type.startswith("image/"):
                return CoverArt(body=body, content_type=content_type, source=provider.name)
        return None


async def get_cover(
    artist: str,
    album: str,
    resolver: ArtResolver,
    cache: ArtCache | None = None,
) -> CoverArt | None:
    """Cover image for an album, read through and written to ``cache`` if given."""
    key = cache_key(artist, album)
    if cache is not None:
        try:
            hit = await cache.get(key)
        except Exception as exc:  # noqa: BLE001 - a broken cache shouldn't break art
            resolver.errors.append(("cache.get", repr(exc)))
            hit = None
        if hit is not None:
            return CoverArt(body=hit[0], content_type=hit[1], source="cache")

    art = await resolver.fetch_cover(artist, album)
    if art is not None and cache is not None:
        try:
            await cache.put(key, art.body, art.content_type)
        except Exception as exc:  # noqa: BLE001
            resolver.errors.append(("cache.put", repr(exc)))
    return art
