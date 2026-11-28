"""Album genres, looked up from Deezer with iTunes as the fallback.

The sheet has no genre column, so build_data.py fills each album's genres
from here. Lookups run in the GitHub Action (regular Python), never in the
Worker. To keep the Action quick and polite to the APIs, genres already in
the previous build are reused, so only new albums are looked up.

Deezer gives a short list per album, broadest first ("Rock", "Metal"). The
first entry is the album's primary genre, used for most stats. iTunes gives
one genre, renamed to Deezer's vocabulary. genre_overrides.json (next to
this file) beats both, for albums the APIs miss or get wrong.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from art import names_match, normalize  # noqa: E402
from sheet_parser import ClubData  # noqa: E402

OVERRIDES_PATH = Path(__file__).resolve().parent / "genre_overrides.json"
USER_AGENT = "RecordsAndRebuttals/1.0 (https://recordsandrebuttals.com)"

# iTunes (and stray Deezer) names -> the Deezer names used on the site.
ALIASES = {
    "hip-hop/rap": "Rap/Hip Hop",
    "hip hop": "Rap/Hip Hop",
    "rap": "Rap/Hip Hop",
    "r&b/soul": "R&B",
    "soul": "Soul & Funk",
    "singer/songwriter": "Singer & Songwriter",
    "electronic": "Electro",
    "latin": "Latin Music",
    "world": "World",
    "worldwide": "World",
    "cuban": "Latin Music",
    "african": "African Music",
    "indie rock/rock pop": "Indie Rock",
}

GetJson = Callable[[str], Any]


def canonical(name: str) -> str:
    name = " ".join(name.split())
    return ALIASES.get(name.lower(), name)


def album_key(artist: str, album: str) -> str:
    return f"{normalize(artist)}|{normalize(album)}"


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def deezer_genres(artist: str, album: str, get_json: GetJson) -> list[str]:
    q = urlencode({"q": f"{artist} {album}", "limit": 10})
    for item in (get_json(f"https://api.deezer.com/search/album?{q}") or {}).get("data") or []:
        if names_match(artist, (item.get("artist") or {}).get("name")) and names_match(
            album, item.get("title")
        ):
            detail = get_json(f"https://api.deezer.com/album/{item['id']}") or {}
            names = [g.get("name") for g in (detail.get("genres") or {}).get("data") or []]
            return _dedupe(canonical(n) for n in names if n)
    return []


def itunes_genres(artist: str, album: str, get_json: GetJson) -> list[str]:
    q = urlencode({"term": f"{artist} {album}", "entity": "album", "limit": 10})
    for item in (get_json(f"https://itunes.apple.com/search?{q}") or {}).get("results") or []:
        if names_match(artist, item.get("artistName")) and names_match(
            album, item.get("collectionName")
        ):
            name = item.get("primaryGenreName")
            return [canonical(name)] if name else []
    return []


def lookup(artist: str, album: str, get_json: GetJson = http_get_json) -> list[str]:
    """Genres for one album, broadest first; [] when no source knows it."""
    for source in (deezer_genres, itunes_genres):
        try:
            found = source(artist, album, get_json)
        except Exception as exc:  # one flaky source shouldn't sink the build
            print(f"warning: {source.__name__}({artist!r}, {album!r}): {exc}", file=sys.stderr)
            continue
        if found:
            return found
    return []


def cache_from_payload(payload: dict | None) -> dict[str, list[str]]:
    """Genres per album from a previous club-data payload (empty if unusable)."""
    cache: dict[str, list[str]] = {}
    for row in (payload or {}).get("albums") or []:
        if isinstance(row, dict) and row.get("genres") and row.get("artist") and row.get("album"):
            cache[album_key(row["artist"], row["album"])] = list(row["genres"])
    return cache


def load_overrides(path: Path | None = None) -> dict[str, list[str]]:
    """``{"Artist - Album": "Genre" | ["Genre", ...]}`` keyed like album_key."""
    path = path or OVERRIDES_PATH
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for title, genres in raw.items():
        if title.startswith("_"):  # comments
            continue
        artist, sep, album = title.partition(" - ")
        if not sep:
            raise ValueError(f"{path.name}: {title!r} should look like 'Artist - Album'")
        genres = [genres] if isinstance(genres, str) else genres
        out[album_key(artist, album)] = [canonical(g) for g in genres]
    return out


def attach_genres(
    data: ClubData,
    *,
    cache: dict[str, list[str]] | None = None,
    overrides: dict[str, list[str]] | None = None,
    get_json: GetJson | None = http_get_json,
    pause: float = 0.15,
) -> ClubData:
    """A copy of ``data`` with every album's genres filled in.

    Order of precedence: overrides, then the cache, then a live lookup (only
    when ``get_json`` is given). Albums nobody knows keep ``()``.
    """
    cache = cache or {}
    overrides = overrides or {}
    albums = []
    looked_up = 0
    for a in data.albums:
        key = album_key(a.artist, a.album)
        genres = overrides.get(key) or cache.get(key)
        if genres is None and get_json is not None:
            if looked_up and pause:
                time.sleep(pause)  # stays well under Deezer's 50 requests / 5 s
            genres = lookup(a.artist, a.album, get_json)
            looked_up += 1
        albums.append(replace(a, genres=tuple(genres or ())))
    if looked_up:
        print(f"looked up genres for {looked_up} album(s)")
    return replace(data, albums=albums)


def _dedupe(names) -> list[str]:
    return list(dict.fromkeys(names))
