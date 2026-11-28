"""Build the site's club-data JSON from the Google Sheet.

Runs on regular Python (in a GitHub Action, or locally). The Worker then
serves the result from KV, so no stats are computed per request.

    python pipeline/build_data.py --doc-id SHEET_ID --out club-data.json
    python pipeline/build_data.py --csv sheet.csv --out club-data.json

SHEETS_DOC_ID / SHEETS_GID environment variables work in place of the flags.

Album genres come from Deezer/iTunes (pipeline/genres.py). Pass the last
build with --previous to reuse its genres and only look up new albums, or
--no-genre-lookup to skip the network (overrides still apply).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analytics  # noqa: E402
import genres  # noqa: E402
import sheet_parser  # noqa: E402


def fetch_csv(doc_id: str, gid: str | None = None) -> str:
    url = sheet_parser.sheet_csv_url(doc_id, gid=gid)
    req = urllib.request.Request(url, headers={"User-Agent": "RecordsAndRebuttals/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def load_previous(path: Path | None) -> dict | None:
    """The previous payload, or None if it's missing or unreadable."""
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"note: not reusing genres from {path}: {exc}", file=sys.stderr)
        return None


def build(csv_text: str, *, previous: dict | None = None, lookup: bool = True) -> dict:
    data = genres.attach_genres(
        sheet_parser.parse_csv(csv_text),
        cache=genres.cache_from_payload(previous),
        overrides=genres.load_overrides(),
        get_json=genres.http_get_json if lookup else None,
    )
    payload = analytics.build_payload(data)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build club-data JSON from the sheet.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--csv", type=Path, help="read a local CSV export instead of fetching")
    src.add_argument("--doc-id", default=os.environ.get("SHEETS_DOC_ID"))
    parser.add_argument("--gid", default=os.environ.get("SHEETS_GID") or None)
    parser.add_argument("--out", type=Path, default=Path("club-data.json"))
    parser.add_argument("--previous", type=Path, help="last build, to reuse its album genres")
    parser.add_argument("--no-genre-lookup", action="store_true", help="don't call Deezer/iTunes")
    args = parser.parse_args(argv)

    if args.csv:
        text = args.csv.read_text(encoding="utf-8")
    elif args.doc_id:
        text = fetch_csv(args.doc_id, args.gid)
    else:
        parser.error("pass --csv or --doc-id (or set SHEETS_DOC_ID)")

    payload = build(text, previous=load_previous(args.previous), lookup=not args.no_genre_lookup)
    if not payload["albums"]:
        print("error: the sheet produced no albums; not writing output", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    ov = payload["overview"]
    print(f"wrote {args.out}: {ov['album_count']} albums, {ov['review_count']} reviews, "
          f"{ov['listener_count']} listeners")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
