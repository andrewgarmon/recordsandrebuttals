"""Preview the site locally without the Workers runtime.

Serves ./public and answers /api/club-data from a CSV file using the same
sheet_parser + analytics code the Worker runs. Album art returns 404, so
covers show as blank squares.

    python scripts/preview.py path/to/sheet.csv [--port 8000]

Export the sheet with File > Download > CSV, or point at any CSV shaped
like it.
"""

import argparse
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import analytics  # noqa: E402
import sheet_parser  # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    csv_path: Path

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/club-data":
            data = sheet_parser.parse_csv(self.csv_path.read_text(encoding="utf-8"))
            body = json.dumps(analytics.build_payload(data), allow_nan=False).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/api/"):
            self.send_error(404)
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    Handler.csv_path = args.csv
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(ROOT / "public")))
    print(f"Serving on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
