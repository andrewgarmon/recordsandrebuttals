"""Build the site's club-data JSON from the Google Sheet.

Runs on regular Python (in a GitHub Action, or locally). The Worker then
serves the result from KV, so no stats are computed per request.

    python pipeline/build_data.py --doc-id SHEET_ID --out club-data.json
    python pipeline/build_data.py --csv sheet.csv --out club-data.json

SHEETS_DOC_ID / SHEETS_GID environment variables work in place of the flags.
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
import sheet_parser  # noqa: E402


def fetch_csv(doc_id: str, gid: str | None = None) -> str:
    url = sheet_parser.sheet_csv_url(doc_id, gid=gid)
    req = urllib.request.Request(url, headers={"User-Agent": "RecordsAndRebuttals/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def build(csv_text: str) -> dict:
    payload = analytics.build_payload(sheet_parser.parse_csv(csv_text))
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build club-data JSON from the sheet.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--csv", type=Path, help="read a local CSV export instead of fetching")
    src.add_argument("--doc-id", default=os.environ.get("SHEETS_DOC_ID"))
    parser.add_argument("--gid", default=os.environ.get("SHEETS_GID") or None)
    parser.add_argument("--out", type=Path, default=Path("club-data.json"))
    args = parser.parse_args(argv)

    if args.csv:
        text = args.csv.read_text(encoding="utf-8")
    elif args.doc_id:
        text = fetch_csv(args.doc_id, args.gid)
    else:
        parser.error("pass --csv or --doc-id (or set SHEETS_DOC_ID)")

    payload = build(text)
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
