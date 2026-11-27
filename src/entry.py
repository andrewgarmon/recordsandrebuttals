"""Cloudflare Worker entrypoint.

Routes:
  GET /api/club-data                       all stats as JSON (edge-cached 5 min)
  GET /api/album-art?artist=...&album=...  cover image (R2 if bound, else edge cache)
  everything else                          static files from ./public

All Workers-specific code lives here; sheet_parser, analytics and art are
plain Python and are tested without the runtime.
"""

import json
from urllib.parse import parse_qs, urlparse

from js import caches
from workers import Response, WorkerEntrypoint, fetch

import analytics
import art
import sheet_parser

CLUB_DATA_TTL = 300  # 5 minutes
ART_TTL = 7 * 24 * 3600  # 7 days (edge cache, when R2 isn't bound)
ART_MISS_TTL = 24 * 3600  # remember "no cover found" for a day
R2_ART_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _env(env, name, default=None):
    """Read an optional var/secret; missing bindings raise or come back undefined."""
    try:
        value = getattr(env, name)
    except Exception:  # noqa: BLE001
        return default
    if value is None or str(value) in ("", "undefined"):
        return default
    return value


class WorkersHttp:
    """art.HttpClient backed by workers.fetch."""

    async def get_json(self, url, headers=None):
        try:
            resp = await fetch(url, headers=headers or {})
            if not resp.ok:
                return None
            return json.loads(await resp.text())
        except Exception:  # noqa: BLE001
            return None

    async def get_bytes(self, url, headers=None):
        try:
            resp = await fetch(url, headers=headers or {})
            if not resp.ok:
                return None
            content_type = resp.headers.get("content-type") or "application/octet-stream"
            return await resp.bytes(), content_type.split(";")[0].strip()
        except Exception:  # noqa: BLE001
            return None


class R2ArtCache:
    """art.ArtCache backed by an R2 bucket binding."""

    def __init__(self, bucket):
        self.bucket = bucket

    async def get(self, key):
        obj = await self.bucket.get(key)
        if obj is None or str(obj) == "undefined":
            return None
        content_type = "image/jpeg"
        meta = getattr(obj, "httpMetadata", None)
        if meta is not None and getattr(meta, "contentType", None):
            content_type = meta.contentType
        buf = await obj.arrayBuffer()
        return bytes(buf.to_py()), content_type

    async def put(self, key, body, content_type):
        from js import Object
        from pyodide.ffi import to_js

        opts = to_js({"httpMetadata": {"contentType": content_type}}, dict_converter=Object.fromEntries)
        await self.bucket.put(key, to_js(body), opts)  # bytes -> Uint8Array


def _json_response(data, status=200, max_age=0):
    headers = {"content-type": "application/json; charset=utf-8"}
    headers["cache-control"] = f"public, max-age={max_age}" if max_age else "no-store"
    return Response(json.dumps(data, allow_nan=False), status=status, headers=headers)


async def _cached(cache_key, build):
    """Serve from caches.default, or build a Response and store a copy."""
    cache = caches.default
    hit = await cache.match(cache_key)
    if hit is not None and str(hit) != "undefined":
        return Response(hit)
    resp = await build()
    if resp.status in (200, 404):
        await cache.put(cache_key, resp.js_object.clone())
    return resp


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        if url.path == "/api/club-data":
            return await self.club_data(request)
        if url.path == "/api/album-art":
            return await self.album_art(request, url)
        if url.path.startswith("/api/"):
            return _json_response({"error": "not found"}, status=404)
        return await self.env.ASSETS.fetch(request)

    # -- /api/club-data ----------------------------------------------------

    async def club_data(self, request):
        cache_key = f"{urlparse(request.url).scheme}://{urlparse(request.url).netloc}/api/club-data"
        return await _cached(cache_key, self._build_club_data)

    async def _build_club_data(self):
        doc_id = _env(self.env, "SHEETS_DOC_ID")
        if not doc_id:
            return _json_response({"error": "SHEETS_DOC_ID is not configured"}, status=500)
        sheet_url = sheet_parser.sheet_csv_url(str(doc_id), gid=_env(self.env, "SHEETS_GID"))
        resp = await fetch(sheet_url)
        if not resp.ok:
            return _json_response({"error": f"sheet fetch failed ({resp.status})"}, status=502)
        try:
            data = sheet_parser.parse_csv(await resp.text())
        except sheet_parser.SheetFormatError as exc:
            return _json_response({"error": str(exc)}, status=502)
        return _json_response(analytics.build_payload(data), max_age=CLUB_DATA_TTL)

    # -- /api/album-art ----------------------------------------------------

    async def album_art(self, request, url):
        params = parse_qs(url.query)
        artist = (params.get("artist") or [""])[0].strip()
        album = (params.get("album") or [""])[0].strip()
        if not artist or not album:
            return _json_response({"error": "artist and album are required"}, status=400)

        http = WorkersHttp()
        resolver = art.ArtResolver(
            art.default_providers(http, _env(self.env, "LAST_FM_API_KEY")), http
        )
        bucket = _env(self.env, "COVER_ART_BUCKET")
        if bucket is not None:
            cover = await art.get_cover(artist, album, resolver, R2ArtCache(bucket))
            return self._art_response(cover, R2_ART_CACHE_CONTROL)

        key = f"{url.scheme}://{url.netloc}/api/album-art/{art.cache_key(artist, album)}"

        async def build():
            cover = await art.get_cover(artist, album, resolver)
            return self._art_response(cover, f"public, max-age={ART_TTL}")

        return await _cached(key, build)

    @staticmethod
    def _art_response(cover, cache_control):
        if cover is None:
            return Response(
                "no cover found",
                status=404,
                headers={"cache-control": f"public, max-age={ART_MISS_TTL}"},
            )
        return Response(
            cover.body,
            headers={
                "content-type": cover.content_type,
                "cache-control": cache_control,
                "x-art-source": cover.source,
            },
        )
