# Records & Rebuttals

Record club stats at [recordsandrebuttals.com](https://recordsandrebuttals.com):
a Cloudflare Python Worker plus a static frontend. It replaces the
Streamlit app in [`andrewgarmon/record_club`](https://github.com/andrewgarmon/record_club).

## How it fits together

```
Google Sheet (CSV) ──► src/sheet_parser.py ──► src/analytics.py ──► GET /api/club-data (JSON, edge-cached 5 min)
Deezer → iTunes → Last.fm → MusicBrainz ──► src/art.py ──► GET /api/album-art?artist=…&album=…
public/ (HTML + vanilla JS + Pico CSS + Plotly) ◄── everything else
```

| File | Job |
| --- | --- |
| `src/sheet_parser.py` | All the sheet's quirks: blank date header, repeated listener headers, listeners found by position before `Average`, stray whitespace, blank rows, integer `album_id`s. |
| `src/analytics.py` | Every stat on the site, in one payload. Standard library only, so it stays well under the free plan's 10 ms CPU limit. |
| `src/art.py` | Album covers from a chain of providers, with optional R2 caching. |
| `src/entry.py` | The Worker: routing, edge cache, Workers runtime glue. |
| `public/` | The site. No build step. |

Scores are on a 0–100 scale (`SCORE_MAX` in `src/analytics.py`).

## Deploying

Cloudflare Workers Builds deploys on every push. `main` goes to production;
other branches get preview URLs. The deploy command is the default
`npx wrangler deploy`. No `pywrangler` is needed because the Worker has no
Python packages.

### One-time setup

1. Set the sheet ID as a secret (Worker → Settings → Variables and Secrets,
   or `npx wrangler secret put SHEETS_DOC_ID`). The sheet must be viewable by
   anyone with the link.
2. Optional: `SHEETS_GID` secret if the tab isn't named `Albums`
   (it's the `gid=` number in the sheet URL).
3. Optional: `LAST_FM_API_KEY` secret adds Last.fm as an album-art source.
4. Optional R2 cover cache: `npx wrangler r2 bucket create record-club-art`,
   then uncomment `r2_buckets` in `wrangler.jsonc`.

## Working locally

```
pip install pytest && pytest                     # tests, no Workers runtime needed
python scripts/preview.py sheet.csv              # site at http://127.0.0.1:8000 using a CSV export
npx wrangler dev                                 # the real Worker (put SHEETS_DOC_ID in .dev.vars)
```
