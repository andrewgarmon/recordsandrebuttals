"""All club statistics, computed from parsed albums and reviews.

Pure Python standard library: no Workers imports, no pandas. Everything is
joined on ``album_id`` and kept at full precision until :func:`build_payload`
rounds the final output.

Conventions:

* "Self" scores are a listener's scores on albums they requested. They count
  toward what a listener *gave*, but never toward what a requester
  *received* (requester leaderboard, avg received, the received breakdown).
* Ties are kept: "favorite album", "top album", superlatives etc. return
  every tied entry, never an arbitrary first one.
* ``None`` means "no data" (no overlap, a single score for a std dev,
  the diagonal of the deviation matrix). It is never replaced with 0.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sheet_parser import Album, ClubData, Review

SCORE_MAX = 100  # top of the scoring scale; "perfect" scores equal this
SCORE_MIN = 0
ROUND_DIGITS = 2
TOP_N = 25
BOTTOM_N = 10
SPREAD_N = 10
HOT_TAKES_N = 10
GENRE_MIN_ALBUMS = 3  # genres with fewer albums fold into "Other" in charts
GENRE_MAX = 7  # at most this many named genres in charts (+ "Other")
GENRE_MIN_REVIEWS = 3  # a listener's favorite genre needs this many of their scores
OTHER = "Other"
UNKNOWN = "Unknown"


# --------------------------------------------------------------------------
# Small numeric helpers
# --------------------------------------------------------------------------


# Plain float arithmetic: the statistics module's exact-fraction stdev is
# several times slower, and CPU time is the budget that matters here.


def mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation; None with fewer than two values."""
    n = len(values)
    if n < 2:
        return None
    m = math.fsum(values) / n
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (n - 1))


def _fmean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = _fmean(xs), _fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _argbest(items: dict[str, float], *, highest: bool) -> list[str]:
    """All keys tied for the highest (or lowest) value."""
    if not items:
        return []
    best = max(items.values()) if highest else min(items.values())
    return sorted(k for k, v in items.items() if v == best)


def round_floats(obj: Any, digits: int = ROUND_DIGITS) -> Any:
    """Round every float in a nested structure (for serialization only)."""
    t = type(obj)
    if t is float:
        return round(obj, digits)
    if t is dict:
        return {k: round_floats(v, digits) for k, v in obj.items()}
    if t is list or t is tuple:
        if obj and all(type(v) in _SCALARS for v in obj):
            return [round(v, digits) if type(v) is float else v for v in obj]
        return [round_floats(v, digits) for v in obj]
    return obj


_SCALARS = (float, int, str, bool, type(None))


# --------------------------------------------------------------------------
# Indexes
# --------------------------------------------------------------------------


def _index(data: ClubData) -> "_Index":
    """The (memoized) index for this ClubData."""
    idx = data.__dict__.get("_index")
    if idx is None:
        idx = data.__dict__["_index"] = _Index(data)
    return idx


class _Index:
    def __init__(self, data: ClubData):
        self.albums: dict[int, Album] = {a.album_id: a for a in data.albums}
        self.reviews = [r for r in data.reviews if r.album_id in self.albums]
        self.by_album: dict[int, list[Review]] = defaultdict(list)
        self.by_listener: dict[str, list[Review]] = defaultdict(list)
        for r in self.reviews:
            self.by_album[r.album_id].append(r)
            self.by_listener[r.listener].append(r)
        # Listeners in sheet order, then anyone who only shows up in reviews.
        seen = dict.fromkeys(data.listeners)
        for r in self.reviews:
            seen.setdefault(r.listener)
        self.listeners: list[str] = list(seen)
        self.by_requester: dict[str, list[Album]] = defaultdict(list)
        for a in data.albums:
            if a.requester:
                self.by_requester[a.requester].append(a)
        self.requesters: list[str] = sorted(
            {a.requester for a in data.albums if a.requester},
            key=lambda n: (self.listeners.index(n) if n in self.listeners else len(self.listeners), n),
        )

    def requester_of(self, album_id: int) -> str | None:
        return self.albums[album_id].requester

    def is_self(self, r: Review) -> bool:
        return self.requester_of(r.album_id) == r.listener


# --------------------------------------------------------------------------
# Albums
# --------------------------------------------------------------------------


def album_stats(data: ClubData) -> list[dict[str, Any]]:
    """Metadata plus score aggregates for every album, in sheet order."""
    idx = _index(data)
    return [_album_row(idx, a) for a in data.albums]


def _album_row(idx: _Index, a: Album) -> dict[str, Any]:
    scores = [r.score for r in idx.by_album.get(a.album_id, [])]
    return {
        "album_id": a.album_id,
        "artist": a.artist,
        "album": a.album,
        "requester": a.requester,
        "date": a.date,
        "release_year": a.release_year,
        "decade": a.decade,
        "genre": a.genres[0] if a.genres else None,
        "genres": list(a.genres),
        "count": len(scores),
        "mean": mean(scores),
        "median": median(scores),
        "stdev": stdev(scores),
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
    }


def leaderboards(rows: Iterable[dict[str, Any]]) -> dict[str, list[int]]:
    """Album ids for the top/bottom/divisive/unanimous lists."""
    scored = [r for r in rows if r["count"] > 0]
    multi = [r for r in scored if r["count"] >= 2]
    by_mean_desc = sorted(scored, key=lambda r: (-r["mean"], r["album_id"]))
    by_mean_asc = sorted(scored, key=lambda r: (r["mean"], r["album_id"]))
    divisive = sorted(multi, key=lambda r: (-r["stdev"], -r["mean"], r["album_id"]))
    unanimous = sorted(multi, key=lambda r: (r["stdev"], -r["mean"], r["album_id"]))
    ids = lambda rs, n: [r["album_id"] for r in rs[:n]]  # noqa: E731
    return {
        "top": ids(by_mean_desc, TOP_N),
        "bottom": ids(by_mean_asc, BOTTOM_N),
        "most_divisive": ids(divisive, SPREAD_N),
        "most_unanimous": ids(unanimous, SPREAD_N),
    }


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------


def overview(data: ClubData, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    idx = _index(data)
    rows = rows if rows is not None else album_stats(data)
    scores = [r.score for r in idx.reviews]
    means = {str(r["album_id"]): r["mean"] for r in rows if r["count"] > 0}
    return {
        "album_count": len(data.albums),
        "review_count": len(scores),
        "listener_count": len(idx.listeners),
        "mean": mean(scores),
        "median": median(scores),
        "stdev": stdev(scores),
        "max_score_count": sum(1 for s in scores if s == SCORE_MAX),
        "zero_score_count": sum(1 for s in scores if s == SCORE_MIN),
        "top_album_ids": [int(k) for k in _argbest(means, highest=True)],
        "bottom_album_ids": [int(k) for k in _argbest(means, highest=False)],
        "scores": scores,
    }


# --------------------------------------------------------------------------
# Tracks
# --------------------------------------------------------------------------


def track_counts(data: ClubData) -> dict[str, list[dict[str, Any]]]:
    idx = _index(data)

    def count(attr: str) -> list[dict[str, Any]]:
        counts: dict[tuple[int, str], list[str]] = defaultdict(list)
        for r in idx.reviews:
            track = getattr(r, attr)
            if track:
                counts[(r.album_id, track)].append(r.listener)
        out = [
            {"album_id": aid, "track": track, "count": len(who), "listeners": sorted(who)}
            for (aid, track), who in counts.items()
        ]
        return sorted(out, key=lambda t: (-t["count"], t["album_id"], t["track"]))

    return {"favorite": count("favorite_track"), "least_favorite": count("least_favorite_track")}


# --------------------------------------------------------------------------
# Listener x requester
# --------------------------------------------------------------------------


def listener_requester_matrix(data: ClubData) -> dict[str, dict[str, float | None]]:
    """matrix[listener][requester] = listener's mean score on that requester's picks.

    The diagonal (a listener scoring their own picks) is included as data;
    summary stats elsewhere exclude it.
    """
    idx = _index(data)
    cells: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in idx.reviews:
        req = idx.requester_of(r.album_id)
        if req:
            cells[r.listener][req].append(r.score)
    return {
        lst: {req: mean(cells[lst][req]) for req in idx.requesters}
        for lst in idx.listeners
    }


# --------------------------------------------------------------------------
# Deviation (pairwise RMSE)
# --------------------------------------------------------------------------


def deviation_matrix(data: ClubData) -> dict[str, Any]:
    """Pairwise RMSE between listeners over the albums both scored.

    ``matrix[a][b]`` is None on the diagonal and where a and b share no
    albums. ``average[b]`` is the mean of b's column over real values only.
    """
    idx = _index(data)
    by_listener = {l: {r.album_id: r.score for r in idx.by_listener[l]} for l in idx.listeners}
    matrix: dict[str, dict[str, float | None]] = {}
    overlap: dict[str, dict[str, int]] = {}
    for a in idx.listeners:
        matrix[a], overlap[a] = {}, {}
        for b in idx.listeners:
            common = by_listener[a].keys() & by_listener[b].keys()
            overlap[a][b] = len(common)
            if a == b or not common:
                matrix[a][b] = None
                continue
            sq = [(by_listener[a][k] - by_listener[b][k]) ** 2 for k in common]
            matrix[a][b] = math.sqrt(_fmean(sq))
    average = {
        b: mean([matrix[a][b] for a in idx.listeners if matrix[a][b] is not None])
        for b in idx.listeners
    }
    return {"matrix": matrix, "average": average, "overlap": overlap}


# --------------------------------------------------------------------------
# Listener profiles
# --------------------------------------------------------------------------


def listener_profiles(
    data: ClubData,
    matrix: dict[str, dict[str, float | None]] | None = None,
    deviation: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    idx = _index(data)
    matrix = matrix if matrix is not None else listener_requester_matrix(data)
    deviation = deviation if deviation is not None else deviation_matrix(data)
    profiles: dict[str, dict[str, Any]] = {}
    for lst in idx.listeners:
        given = idx.by_listener[lst]
        given_scores = [r.score for r in given]
        by_album = {str(r.album_id): r.score for r in given}
        received = [
            r
            for a in idx.by_requester.get(lst, [])
            for r in idx.by_album.get(a.album_id, [])
            if r.listener != lst
        ]
        received_by: dict[str, list[float]] = defaultdict(list)
        for r in received:
            received_by[r.listener].append(r.score)
        profiles[lst] = {
            "review_count": len(given_scores),
            "avg_given": mean(given_scores),
            "median_given": median(given_scores),
            "stdev_given": stdev(given_scores),
            "favorite_album_ids": [int(k) for k in _argbest(by_album, highest=True)],
            "least_favorite_album_ids": [int(k) for k in _argbest(by_album, highest=False)],
            "requested_album_ids": [a.album_id for a in idx.by_requester.get(lst, [])],
            "avg_received": mean([r.score for r in received]),
            "received_count": len(received),
            "given_by_requester": dict(matrix.get(lst, {})),
            "received_by_listener": {
                other: mean(received_by[other]) for other in idx.listeners if other != lst
            },
            "deviation": {
                other: v for other, v in deviation["matrix"].get(lst, {}).items() if other != lst
            },
        }
    return profiles


# --------------------------------------------------------------------------
# Stats page
# --------------------------------------------------------------------------


def superlatives(profiles: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    active = {l: p for l, p in profiles.items() if p["review_count"] > 0}
    avg = {l: p["avg_given"] for l, p in active.items()}
    spread = {l: p["stdev_given"] for l, p in active.items() if p["stdev_given"] is not None}
    count = {l: float(p["review_count"]) for l, p in active.items()}

    def pick(values: dict[str, float], highest: bool) -> dict[str, Any]:
        names = _argbest(values, highest=highest)
        return {"listeners": names, "value": values[names[0]] if names else None}

    return {
        "harshest": pick(avg, highest=False),
        "most_generous": pick(avg, highest=True),
        "most_consistent": pick(spread, highest=False),
        "most_volatile": pick(spread, highest=True),
        "most_active": pick(count, highest=True),
    }


def requester_leaderboard(data: ClubData) -> list[dict[str, Any]]:
    """How each requester's picks were received, excluding their own scores."""
    idx = _index(data)
    out = []
    for req in idx.requesters:
        picks = idx.by_requester.get(req, [])
        pick_means: dict[str, float] = {}
        all_scores: list[float] = []
        for a in picks:
            others = [r.score for r in idx.by_album.get(a.album_id, []) if r.listener != req]
            all_scores.extend(others)
            if others:
                pick_means[str(a.album_id)] = _fmean(others)
        out.append(
            {
                "requester": req,
                "picks": len(picks),
                "review_count": len(all_scores),
                "avg_reception": mean(list(pick_means.values())),
                "avg_score": mean(all_scores),
                "best_album_ids": [int(k) for k in _argbest(pick_means, highest=True)],
                "worst_album_ids": [int(k) for k in _argbest(pick_means, highest=False)],
            }
        )
    return sorted(
        out,
        key=lambda r: (r["avg_reception"] is None, -(r["avg_reception"] or 0), r["requester"]),
    )


def decade_breakdown(data: ClubData, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    idx = _index(data)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["decade"]:
            groups[r["decade"]].append(r)
    out = []
    for dec, members in groups.items():
        scores = [rv.score for m in members for rv in idx.by_album.get(m["album_id"], [])]
        years = [m["release_year"] for m in members if m["release_year"] is not None]
        out.append(
            {
                "decade": dec,
                "album_count": len(members),
                "review_count": len(scores),
                "mean": mean(scores),
                "album_mean": mean([m["mean"] for m in members if m["mean"] is not None]),
                "_sort": min(years) if years else _decade_sort_key(dec),
            }
        )
    out.sort(key=lambda d: (d["_sort"], d["decade"]))
    for d in out:
        del d["_sort"]
    return out


def _decade_sort_key(decade: str) -> int:
    digits = "".join(ch for ch in decade if ch.isdigit())
    if not digits:
        return 10**6
    n = int(digits)
    if n < 100:  # "60s" -> 1960, "00s" -> 2000, "20s" -> 2020
        n += 1900 if n >= 30 else 2000
    return n


def year_correlation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = [
        {"album_id": r["album_id"], "release_year": r["release_year"], "mean": r["mean"]}
        for r in rows
        if r["release_year"] is not None and r["mean"] is not None
    ]
    return {
        "pearson_r": pearson([p["release_year"] for p in points], [p["mean"] for p in points]),
        "points": points,
    }


def timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Albums in date order with a running count (undated albums are left out)."""
    dated = [r for r in rows if r["date"] and _is_iso_date(r["date"])]
    dated.sort(key=lambda r: (r["date"], r["album_id"]))
    return [
        {"date": r["date"], "album_id": r["album_id"], "mean": r["mean"], "cumulative": i}
        for i, r in enumerate(dated, start=1)
    ]


def _is_iso_date(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def hot_takes(data: ClubData, n: int = HOT_TAKES_N) -> list[dict[str, Any]]:
    """Single scores furthest from the rest of the club on that album.

    ``club_avg`` is the mean of the *other* listeners' scores, so a take
    isn't diluted by itself; albums with only one reviewer are skipped.
    """
    idx = _index(data)
    takes = []
    for r in idx.reviews:
        others = [o.score for o in idx.by_album[r.album_id] if o.listener != r.listener]
        if not others:
            continue
        club_avg = _fmean(others)
        takes.append(
            {
                "album_id": r.album_id,
                "listener": r.listener,
                "score": r.score,
                "club_avg": club_avg,
                "delta": r.score - club_avg,
            }
        )
    takes.sort(key=lambda t: (-abs(t["delta"]), t["album_id"], t["listener"]))
    return takes[:n]


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def genre_breakdown(data: ClubData, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Genre stats, by each album's primary genre.

    ``genres`` lists every primary genre (plus "Unknown" for albums with
    none). Charts use ``chart_genres``: the biggest few, with the rest folded
    into "Other". Listener tastes exclude self-scores, like the other
    reception stats, since people tend to pick (and rate) their own genres.
    """
    idx = _index(data)
    primary = {r["album_id"]: r["genre"] or UNKNOWN for r in rows}
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        members[primary[r["album_id"]]].append(r)

    genres = []
    for g, ms in members.items():
        scores = [rv.score for m in ms for rv in idx.by_album.get(m["album_id"], [])]
        means = {str(m["album_id"]): m["mean"] for m in ms if m["mean"] is not None}
        spreads = [m["stdev"] for m in ms if m["stdev"] is not None]
        genres.append(
            {
                "genre": g,
                "album_count": len(ms),
                "review_count": len(scores),
                "share": len(ms) / len(rows) if rows else None,
                "mean": mean(scores),
                "album_mean": mean(list(means.values())),
                # how much listeners disagree on a typical album of this genre
                "avg_album_stdev": mean(spreads),
                "best_album_ids": [int(k) for k in _argbest(means, highest=True)],
                "worst_album_ids": [int(k) for k in _argbest(means, highest=False)],
                "requesters": _count(m["requester"] for m in ms if m["requester"]),
            }
        )
    genres.sort(key=lambda g: (g["genre"] == UNKNOWN, -g["album_count"], g["genre"]))

    named = [
        g["genre"] for g in genres
        if g["genre"] != UNKNOWN and g["album_count"] >= GENRE_MIN_ALBUMS
    ][:GENRE_MAX]
    bucket = {aid: (g if g in named else OTHER) for aid, g in primary.items()}
    chart_genres = named + ([OTHER] if any(b == OTHER for b in bucket.values()) else [])

    # Listener tastes: average score per genre bucket, and how far that sits
    # from the listener's own average (so harsh and generous scorers compare).
    tastes: dict[str, dict[str, Any]] = {}
    for lst in idx.listeners:
        given = [r for r in idx.by_listener[lst] if not idx.is_self(r)]
        overall = mean([r.score for r in given])
        by_bucket: dict[str, list[float]] = defaultdict(list)
        for r in given:
            by_bucket[bucket[r.album_id]].append(r.score)
        avg = {g: mean(by_bucket[g]) for g in chart_genres}
        affinity = {
            g: (avg[g] - overall if avg[g] is not None and overall is not None else None)
            for g in chart_genres
        }
        eligible = {
            g: v for g, v in affinity.items()
            if v is not None and g != OTHER and len(by_bucket[g]) >= GENRE_MIN_REVIEWS
        }
        tastes[lst] = {
            "mean": overall,
            "avg": avg,
            "affinity": affinity,
            "counts": {g: len(by_bucket[g]) for g in chart_genres},
            # Both need two genres to compare: with one, it's not a preference.
            "favorite": _argbest(eligible, highest=True) if len(eligible) > 1 else [],
            "least_favorite": _argbest(eligible, highest=False) if len(eligible) > 1 else [],
        }

    by_requester = {
        req: _count(bucket[a.album_id] for a in idx.by_requester.get(req, []))
        for req in idx.requesters
    }
    variety = {
        req: float(len({primary[a.album_id] for a in idx.by_requester.get(req, [])} - {UNKNOWN}))
        for req in idx.requesters
    }
    by_decade: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        if r["decade"]:
            cell = by_decade[r["decade"]]
            cell[bucket[r["album_id"]]] = cell.get(bucket[r["album_id"]], 0) + 1
    decades = [d["decade"] for d in decade_breakdown(data, rows)]

    ranked = {
        g["genre"]: g for g in genres
        if g["genre"] in named and g["mean"] is not None
    }

    def pick(field: str, highest: bool) -> dict[str, Any]:
        values = {g: v[field] for g, v in ranked.items() if v[field] is not None}
        names = _argbest(values, highest=highest)
        return {"genres": names, "value": values[names[0]] if names else None}

    def pick_requester(values: dict[str, float]) -> dict[str, Any]:
        names = _argbest(values, highest=True)
        return {"requesters": names, "value": values[names[0]] if names else None}

    return {
        "genres": genres,
        "chart_genres": chart_genres,
        "album_genre": {str(aid): b for aid, b in bucket.items()},
        "tastes": tastes,
        "by_requester": by_requester,
        "by_decade": {d: by_decade[d] for d in decades if d in by_decade},
        "unknown_count": sum(1 for g in primary.values() if g == UNKNOWN),
        "superlatives": {
            "most_picked": pick("album_count", highest=True),
            "most_loved": pick("mean", highest=True),
            "least_loved": pick("mean", highest=False),
            "most_divisive": pick("avg_album_stdev", highest=True),
            "most_varied_picker": pick_requester(variety),
        },
    }


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


def build_payload(data: ClubData) -> dict[str, Any]:
    """Everything the frontend needs, as one JSON-ready dict."""
    rows = album_stats(data)
    matrix = listener_requester_matrix(data)
    deviation = deviation_matrix(data)
    profiles = listener_profiles(data, matrix=matrix, deviation=deviation)
    idx = _index(data)
    payload = {
        "score_scale": {"min": SCORE_MIN, "max": SCORE_MAX},
        "listeners": idx.listeners,
        "requesters": idx.requesters,
        "overview": overview(data, rows),
        "albums": rows,
        "leaderboards": leaderboards(rows),
        "tracks": track_counts(data),
        "listener_requester": matrix,
        "deviation": deviation,
        "profiles": profiles,
        "stats": {
            "superlatives": superlatives(profiles),
            "requesters": requester_leaderboard(data),
            "decades": decade_breakdown(data, rows),
            "year_correlation": year_correlation(rows),
            "timeline": timeline(rows),
            "hot_takes": hot_takes(data),
        },
        "genres": genre_breakdown(data, rows),
    }
    return round_floats(payload)
