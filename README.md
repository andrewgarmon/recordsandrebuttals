# Records & Rebuttals

Record club stats at [recordsandrebuttals.com](https://recordsandrebuttals.com):
a Cloudflare Python Worker plus a static frontend. It replaces the
Streamlit app in [`andrewgarmon/record_club`](https://github.com/andrewgarmon/record_club).

## How it fits together

```
Google Sheet ──► pipeline/ (GitHub Action, every 30 min) ──► Workers KV ──► GET /api/club-data
Deezer → iTunes → Last.fm → MusicBrainz ──► src/art.py ──► GET /api/album-art?artist=…&album=…
public/ (HTML + vanilla JS + Pico CSS + Plotly) ◄── everything else
```

The stats are built on regular Python in GitHub Actions, not in the Worker.
Under Cloudflare's WebAssembly Python they take about 18 ms of CPU, which
is over the free plan's 10 ms per request. The Worker only hands out the
stored JSON.

| File | Job |
| --- | --- |
| `pipeline/sheet_parser.py` | All the sheet's quirks: blank date header, score block plus Favorite/Worst blocks that repeat listener names, `Decade` after the track blocks, stray whitespace, blank rows, integer `album_id`s. |
| `pipeline/analytics.py` | Every stat on the site, in one payload. Standard library only. |
| `pipeline/build_data.py` | Fetches the sheet and writes `club-data.json`. |
| `.github/workflows/refresh-data.yml` | Runs the build and writes the result to KV: every 30 min, after pipeline changes on `main`, or on demand. |
| `src/art.py` | Album covers from a chain of providers (fuzzy name matching), cached in the R2 bucket `record-club-art`. |
| `src/entry.py` | The Worker: routing, edge cache, KV read. |
| `public/` | The site. No build step. |

Scores are on a 0–100 scale (`SCORE_MAX` in `pipeline/analytics.py`).

## Deploying

Cloudflare Workers Builds deploys on every push. `main` goes to production;
other branches get preview URLs. In the Worker's Settings → Builds:

- **Deploy command:** `sh scripts/cf-build.sh deploy`
- **Non-production branch deploy command:** `sh scripts/cf-build.sh preview`

The script deploys through `pywrangler`, which bundles the `workers` Python SDK
with the Worker. Plain `npx wrangler deploy` doesn't, and the Worker then fails
at startup with `No module named 'workers'`.

### One-time setup

1. **KV namespace:** in Cloudflare go to Storage & Databases → KV → Create, and
   put its ID in `wrangler.jsonc` under `kv_namespaces`.
2. **API token:** My Profile → API Tokens → Create Token → Custom token with
   the permission *Account · Workers KV Storage · Edit*.
3. **GitHub secrets** (repo Settings → Secrets and variables → Actions):
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (shown on the Workers
   overview page), `SHEETS_DOC_ID`, and optionally `SHEETS_GID`.
4. Run **Actions → Refresh club data → Run workflow** once to fill KV.
5. Optional: a `LAST_FM_API_KEY` Worker secret adds Last.fm as a cover
   source.
6. **R2 bucket** `record-club-art` (R2 Object Storage → Create bucket) for the
   cover cache. Without it, remove `r2_buckets` from `wrangler.jsonc` and covers
   fall back to the edge cache.

The sheet must stay viewable by anyone with the link. If it isn't, the
Action fails and the site keeps serving the last good data.

## Working locally

```
pip install pytest && pytest                                  # all tests, no Workers runtime needed
python pipeline/build_data.py --doc-id SHEET_ID               # build club-data.json from the live sheet
python scripts/preview.py sheet.csv                           # site at http://127.0.0.1:8000 from a CSV export
```
