import json
import math

import pytest

import analytics
from sheet_parser import Album, ClubData, Review

# Fixture recap (0-100 scale), album_id: requester -> scores
#   1 Abbey Road   (Alice): Alice 90, Bob 70,  Carol 80
#   2 Let It Bleed (Bob):   Alice 80, Bob 90,  Carol 70
#   3 IV           (Carol): Alice 70, Bob 100, Carol 90
#   4 OK Computer  (Bob):   Alice 0,  Bob 100


def approx(x):
    return pytest.approx(x, rel=1e-9, abs=1e-9)


def test_helpers():
    assert analytics.mean([]) is None
    assert analytics.stdev([5.0]) is None
    assert analytics.stdev([1.0, 3.0]) == approx(math.sqrt(2))
    assert analytics.pearson([1, 2, 3], [2, 4, 6]) == approx(1.0)
    assert analytics.pearson([1, 1, 1], [2, 4, 6]) is None
    assert analytics.round_floats({"a": [1.23456, None, 2]}) == {"a": [1.23, None, 2]}


def test_album_stats(club):
    rows = {r["album_id"]: r for r in analytics.album_stats(club)}
    assert rows[1]["mean"] == approx(80) and rows[1]["stdev"] == approx(10)
    assert rows[3]["mean"] == approx(260 / 3)
    assert rows[4]["count"] == 2 and rows[4]["min"] == 0 and rows[4]["max"] == 100
    assert rows[4]["median"] == approx(50)


def test_album_with_no_reviews_has_none_stats():
    data = ClubData(albums=[Album(1, "A", "B")], reviews=[], listeners=["X"])
    row = analytics.album_stats(data)[0]
    assert row["count"] == 0 and row["mean"] is None and row["stdev"] is None
    assert analytics.leaderboards([row]) == {
        "top": [], "bottom": [], "most_divisive": [], "most_unanimous": []
    }


def test_leaderboards(club):
    lb = analytics.leaderboards(analytics.album_stats(club))
    assert lb["top"] == [3, 1, 2, 4]  # 1 and 2 tie on mean -> album_id order
    assert lb["bottom"] == [4, 1, 2, 3]
    assert lb["most_divisive"][0] == 4
    # 1 and 2 tie on stdev (10) and mean (80) -> album_id order
    assert lb["most_unanimous"][:2] == [1, 2]


def test_unanimous_breaks_stdev_ties_by_higher_mean():
    albums = [Album(1, "A", "Low"), Album(2, "A", "High")]
    reviews = [
        Review(1, "X", 40), Review(1, "Y", 50),
        Review(2, "X", 90), Review(2, "Y", 100),
    ]
    rows = analytics.album_stats(ClubData(albums, reviews, ["X", "Y"]))
    assert analytics.leaderboards(rows)["most_unanimous"] == [2, 1]


def test_overview(club):
    ov = analytics.overview(club)
    assert ov["album_count"] == 4
    assert ov["review_count"] == 11
    assert ov["listener_count"] == 3
    assert ov["max_score_count"] == 2
    assert ov["zero_score_count"] == 1
    assert ov["top_album_ids"] == [3]
    assert ov["bottom_album_ids"] == [4]
    assert sorted(ov["scores"]) == sorted([90, 70, 80, 80, 90, 70, 70, 100, 90, 0, 100])
    assert ov["mean"] == approx(840 / 11)


def test_overview_keeps_tied_top_albums():
    albums = [Album(1, "A", "B"), Album(2, "C", "D")]
    reviews = [Review(1, "X", 90), Review(2, "X", 90)]
    ov = analytics.overview(ClubData(albums, reviews, ["X"]))
    assert ov["top_album_ids"] == [1, 2]


def test_track_counts(club):
    tracks = analytics.track_counts(club)
    fav = {(t["album_id"], t["track"]): t["count"] for t in tracks["favorite"]}
    assert fav[(1, "Come Together")] == 1
    least = tracks["least_favorite"]
    assert {(t["album_id"], t["track"]) for t in least} == {(1, "Octopus's Garden"), (3, "Four Sticks")}


def test_track_counts_aggregate_same_track():
    albums = [Album(1, "A", "B")]
    reviews = [Review(1, "X", 50, "Song"), Review(1, "Y", 60, "Song"), Review(1, "Z", 70, "Other")]
    fav = analytics.track_counts(ClubData(albums, reviews, ["X", "Y", "Z"]))["favorite"]
    assert fav[0] == {"album_id": 1, "track": "Song", "count": 2, "listeners": ["X", "Y"]}


def test_tracks_join_on_album_id_not_title():
    # Two different albums with the same title must not be merged.
    albums = [Album(1, "Artist One", "Greatest Hits"), Album(2, "Artist Two", "Greatest Hits")]
    reviews = [Review(1, "X", 50, "Song"), Review(2, "X", 90, "Song")]
    data = ClubData(albums, reviews, ["X"])
    fav = analytics.track_counts(data)["favorite"]
    assert [(t["album_id"], t["count"]) for t in fav] == [(1, 1), (2, 1)]
    rows = {r["album_id"]: r["mean"] for r in analytics.album_stats(data)}
    assert rows == {1: 50, 2: 90}


def test_listener_requester_matrix(club):
    m = analytics.listener_requester_matrix(club)
    assert m["Alice"]["Alice"] == approx(90)
    assert m["Alice"]["Bob"] == approx(40)
    assert m["Alice"]["Carol"] == approx(70)
    assert m["Carol"]["Bob"] == approx(70)


def test_deviation_matrix(club):
    dev = analytics.deviation_matrix(club)
    m = dev["matrix"]
    assert m["Alice"]["Bob"] == approx(math.sqrt(2850))
    assert m["Bob"]["Alice"] == m["Alice"]["Bob"]
    assert m["Alice"]["Carol"] == approx(math.sqrt(200))
    assert m["Alice"]["Alice"] is None
    assert dev["overlap"]["Alice"]["Bob"] == 4
    assert dev["average"]["Alice"] == approx((math.sqrt(2850) + math.sqrt(200)) / 2)


def test_deviation_average_ignores_diagonal_and_no_overlap():
    albums = [Album(1, "A", "One"), Album(2, "A", "Two")]
    reviews = [Review(1, "X", 50), Review(1, "Y", 70), Review(2, "Z", 10)]
    dev = analytics.deviation_matrix(ClubData(albums, reviews, ["X", "Y", "Z"]))
    assert dev["matrix"]["X"]["Z"] is None  # no overlap
    assert dev["average"]["X"] == approx(20)  # only Y counts, not a fake 0
    assert dev["average"]["Z"] is None


def test_listener_profiles(club):
    p = analytics.listener_profiles(club)
    alice = p["Alice"]
    assert alice["avg_given"] == approx(60)
    assert alice["favorite_album_ids"] == [1]
    assert alice["least_favorite_album_ids"] == [4]
    assert alice["avg_received"] == approx(75)  # Bob 70 + Carol 80, own 90 excluded
    assert alice["received_by_listener"] == {"Bob": approx(70), "Carol": approx(80)}
    assert alice["requested_album_ids"] == [1]
    assert "Alice" not in alice["deviation"]
    assert p["Bob"]["favorite_album_ids"] == [3, 4]  # tie kept
    assert p["Bob"]["stdev_given"] == approx(math.sqrt(200))


def test_superlatives(club):
    s = analytics.superlatives(analytics.listener_profiles(club))
    assert s["harshest"] == {"listeners": ["Alice"], "value": approx(60)}
    assert s["most_generous"]["listeners"] == ["Bob"]
    assert s["most_consistent"]["listeners"] == ["Carol"]
    assert s["most_volatile"]["listeners"] == ["Alice"]
    assert s["most_active"] == {"listeners": ["Alice", "Bob"], "value": 4.0}


def test_requester_leaderboard_excludes_self(club):
    board = analytics.requester_leaderboard(club)
    assert [r["requester"] for r in board] == ["Carol", "Alice", "Bob"]
    by = {r["requester"]: r for r in board}
    assert by["Carol"]["avg_reception"] == approx(85)
    assert by["Alice"]["avg_reception"] == approx(75)
    assert by["Bob"]["avg_reception"] == approx(37.5)
    assert by["Bob"]["avg_score"] == approx(50)
    assert by["Bob"]["picks"] == 2
    assert by["Bob"]["best_album_ids"] == [2]
    assert by["Bob"]["worst_album_ids"] == [4]


def test_requester_with_only_self_reviews():
    albums = [Album(1, "A", "B", requester="X")]
    reviews = [Review(1, "X", 90)]
    board = analytics.requester_leaderboard(ClubData(albums, reviews, ["X"]))
    assert board == [
        {
            "requester": "X", "picks": 1, "review_count": 0, "avg_reception": None,
            "avg_score": None, "best_album_ids": [], "worst_album_ids": [],
        }
    ]


def test_decades(club):
    decades = analytics.decade_breakdown(club, analytics.album_stats(club))
    assert [d["decade"] for d in decades] == ["1960s", "1970s", "1990s"]
    assert decades[0]["album_count"] == 2 and decades[0]["mean"] == approx(80)


def test_decade_sort_key():
    assert analytics._decade_sort_key("60s") == 1960
    assert analytics._decade_sort_key("00s") == 2000
    assert analytics._decade_sort_key("1980s") == 1980


def test_year_correlation(club):
    yc = analytics.year_correlation(analytics.album_stats(club))
    assert len(yc["points"]) == 4
    assert yc["pearson_r"] < 0  # the 1997 album scored lowest


def test_timeline(club):
    tl = analytics.timeline(analytics.album_stats(club))
    assert [t["album_id"] for t in tl] == [1, 2, 3, 4]
    assert [t["cumulative"] for t in tl] == [1, 2, 3, 4]


def test_timeline_sorts_by_date_and_skips_undated():
    albums = [
        Album(1, "A", "Late", date="2024-05-01"),
        Album(2, "A", "Early", date="2023-01-01"),
        Album(3, "A", "Undated"),
    ]
    tl = analytics.timeline(analytics.album_stats(ClubData(albums, [], [])))
    assert [t["album_id"] for t in tl] == [2, 1]


def test_hot_takes(club):
    takes = analytics.hot_takes(club)
    assert takes[0] == {
        "album_id": 4, "listener": "Alice", "score": 0, "club_avg": 100, "delta": -100
    }
    assert takes[1]["listener"] == "Bob" and takes[1]["delta"] == 100


def test_hot_takes_skip_single_reviewer_albums():
    albums = [Album(1, "A", "B")]
    assert analytics.hot_takes(ClubData(albums, [Review(1, "X", 0)], ["X"])) == []


def test_reviews_for_unknown_albums_ignored():
    data = ClubData([Album(1, "A", "B")], [Review(1, "X", 50), Review(99, "X", 0)], ["X"])
    assert analytics.overview(data)["review_count"] == 1


def test_full_precision_until_payload():
    albums = [Album(1, "A", "B")]
    reviews = [Review(1, "X", 1), Review(1, "Y", 1), Review(1, "Z", 2)]
    data = ClubData(albums, reviews, ["X", "Y", "Z"])
    assert analytics.album_stats(data)[0]["mean"] == approx(4 / 3)
    assert analytics.build_payload(data)["albums"][0]["mean"] == 1.33


def test_payload_is_json_serializable_and_complete(club):
    payload = analytics.build_payload(club)
    text = json.dumps(payload, allow_nan=False)
    assert json.loads(text) == payload
    assert set(payload) >= {
        "score_scale", "listeners", "requesters", "overview", "albums", "leaderboards",
        "tracks", "listener_requester", "deviation", "profiles", "stats",
    }
    assert payload["score_scale"] == {"min": 0, "max": 100}
    assert payload["requesters"] == ["Alice", "Bob", "Carol"]


def test_empty_payload():
    payload = analytics.build_payload(ClubData())
    assert payload["overview"]["album_count"] == 0
    assert payload["stats"]["superlatives"]["harshest"] == {"listeners": [], "value": None}
    json.dumps(payload, allow_nan=False)
