"""Turn the club's Google Sheet CSV into clean, typed records.

Every quirk of the sheet lives in this module so nothing downstream has to
know about it:

* The date column has a blank / whitespace-only header (``" "``).
* Each listener has three adjacent columns: score, favorite track and least
  favorite track. Their headers repeat the listener's name (``Alice``,
  ``Alice``, ``Alice``), may carry pandas-style suffixes (``Alice.1``,
  ``Alice.2``), or may be blank.
* Listener columns are found by position: everything after the last
  metadata column and before ``Average``.
* Cells carry stray whitespace, and the sheet has blank or half-filled rows.

The output (:class:`ClubData`) has no sheet-specific concepts in it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import quote

METADATA_COLUMNS = ("Date", "Requester", "Artist", "Album", "Release Year", "Decade")
AVERAGE_COLUMN = "Average"
DEFAULT_SHEET_NAME = "Albums"

_SUFFIX_RE = re.compile(r"^(.*?)\.(\d+)$")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y")


@dataclass(frozen=True)
class Album:
    album_id: int
    artist: str
    album: str
    requester: str | None = None
    date: str | None = None  # ISO 8601 (YYYY-MM-DD) when parseable, else as written
    release_year: int | None = None
    decade: str | None = None


@dataclass(frozen=True)
class Review:
    album_id: int
    listener: str
    score: float
    favorite_track: str | None = None
    least_favorite_track: str | None = None


@dataclass
class ClubData:
    albums: list[Album] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    listeners: list[str] = field(default_factory=list)


class SheetFormatError(ValueError):
    """The CSV doesn't look like the club sheet (e.g. no Average column)."""


def sheet_csv_url(doc_id: str, gid: str | None = None, sheet: str = DEFAULT_SHEET_NAME) -> str:
    """URL for a CSV export of one tab of a public Google Sheet.

    With ``gid`` the tab is chosen by its numeric id via ``/export``.
    Otherwise the gviz endpoint picks it by name; ``/export`` ignores a
    ``sheet=`` parameter and silently returns the first tab.
    """
    base = f"https://docs.google.com/spreadsheets/d/{quote(doc_id, safe='')}"
    if gid:
        return f"{base}/export?format=csv&gid={quote(str(gid), safe='')}"
    return f"{base}/gviz/tq?tqx=out:csv&sheet={quote(sheet, safe='')}"


def parse_csv(text: str) -> ClubData:
    """Parse the sheet's CSV text into albums, reviews and listeners."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return ClubData()
    header = _normalize_header(rows[0])
    layout = _Layout.from_header(header)

    albums: list[Album] = []
    reviews: list[Review] = []
    next_id = 1
    for raw in rows[1:]:
        cells = [c.strip() for c in raw] + [""] * (len(header) - len(raw))
        artist = cells[layout.meta["Artist"]] if "Artist" in layout.meta else ""
        title = cells[layout.meta["Album"]] if "Album" in layout.meta else ""
        if not artist or not title:
            continue

        album_id = next_id
        next_id += 1
        release_year = _parse_year(_cell(cells, layout.meta, "Release Year"))
        decade = _cell(cells, layout.meta, "Decade") or _year_to_decade(release_year)
        albums.append(
            Album(
                album_id=album_id,
                artist=artist,
                album=title,
                requester=_cell(cells, layout.meta, "Requester"),
                date=_parse_date(_cell(cells, layout.meta, "Date")),
                release_year=release_year,
                decade=decade,
            )
        )

        for listener, (score_i, fav_i, least_i) in layout.listeners.items():
            score = _parse_score(cells[score_i])
            if score is None:
                continue
            reviews.append(
                Review(
                    album_id=album_id,
                    listener=listener,
                    score=score,
                    favorite_track=(cells[fav_i] or None) if fav_i is not None else None,
                    least_favorite_track=(cells[least_i] or None) if least_i is not None else None,
                )
            )

    return ClubData(albums=albums, reviews=reviews, listeners=list(layout.listeners))


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _normalize_header(header: list[str]) -> list[str]:
    cols = [h.strip() for h in header]
    if "Date" not in cols:
        for i, h in enumerate(cols):
            if h == "":
                cols[i] = "Date"
                break
    return cols


def _base_name(col: str) -> str:
    m = _SUFFIX_RE.match(col)
    return m.group(1) if m else col


@dataclass
class _Layout:
    meta: dict[str, int]
    # listener -> (score column, favorite column or None, least favorite column or None)
    listeners: dict[str, tuple[int, int | None, int | None]]

    @classmethod
    def from_header(cls, cols: list[str]) -> "_Layout":
        if AVERAGE_COLUMN not in cols:
            raise SheetFormatError(f"No {AVERAGE_COLUMN!r} column in sheet header: {cols!r}")
        avg_i = cols.index(AVERAGE_COLUMN)

        meta: dict[str, int] = {}
        for i, c in enumerate(cols[:avg_i]):
            if c in METADATA_COLUMNS and c not in meta:
                meta[c] = i
        start = max(meta.values(), default=-1) + 1

        # Group the columns between metadata and Average into runs: a named
        # column opens a listener, and following columns with the same base
        # name (or no name) belong to it as favorite / least favorite tracks.
        groups: list[tuple[str, list[int]]] = []
        for i in range(start, avg_i):
            name = cols[i]
            base = _base_name(name)
            if not name or name.lower().startswith("unnamed"):
                if groups:
                    groups[-1][1].append(i)
                continue
            if groups and base == groups[-1][0]:
                groups[-1][1].append(i)
            else:
                groups.append((base, [i]))

        listeners: dict[str, tuple[int, int | None, int | None]] = {}
        for name, idxs in groups:
            if name in listeners:
                continue
            fav = idxs[1] if len(idxs) > 1 else None
            least = idxs[2] if len(idxs) > 2 else None
            listeners[name] = (idxs[0], fav, least)
        return cls(meta=meta, listeners=listeners)


def _cell(cells: list[str], meta: dict[str, int], name: str) -> str | None:
    i = meta.get(name)
    if i is None:
        return None
    return cells[i] or None


def _parse_score(value: str) -> float | None:
    if not value:
        return None
    try:
        score = float(value)
    except ValueError:
        return None
    if score != score or score in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return score


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _year_to_decade(year: int | None) -> str | None:
    if year is None:
        return None
    return f"{(year // 10) * 10}s"


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    # gviz sometimes appends a time: "1/5/2024 0:00:00"
    candidate = value.split(" ")[0] if re.match(r"^\d", value) else value
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return value
