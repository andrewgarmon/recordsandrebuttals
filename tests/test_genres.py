import json

import pytest

import analytics
import genres
from sheet_parser import Album, ClubData, Review


def fake_api(routes):
    """get_json stand-in: the first route whose key is in the URL wins."""
    calls = []

    def get_json(url):
        calls.append(url)
        for key, body in routes.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return body
        return {}

    get_json.calls = calls
    return get_json


DEEZER_SEARCH = {"data": [
    {"id": 1, "title": "Something Else", "artist": {"name": "Nobody"}},
    {"id": 42, "title": "Paranoid (Remastered)", "artist": {"name": "Black Sabbath"}},
]}
DEEZER_ALBUM = {"genres": {"data": [{"name": "Rock"}, {"name": "Hard Rock"}]}}


def test_deezer_genres_broadest_first():
    api = fake_api({"search/album": DEEZER_SEARCH, "album/42": DEEZER_ALBUM})
    assert genres.lookup("Black Sabbath", "Paranoid", api) == ["Rock", "Hard Rock"]


def test_falls_back_to_itunes_with_renamed_genre():
    api = fake_api({
        "deezer": {"data": []},
        "itunes": {"results": [{"artistName": "Nas", "collectionName": "Illmatic", "primaryGenreName": "Hip-Hop/Rap"}]},
    })
    assert genres.lookup("Nas", "Illmatic", api) == ["Rap/Hip Hop"]


def test_a_failing_source_is_skipped():
    api = fake_api({
        "deezer": RuntimeError("down"),
        "itunes": {"results": [{"artistName": "Nas", "collectionName": "Illmatic", "primaryGenreName": "Hip-Hop/Rap"}]},
    })
    assert genres.lookup("Nas", "Illmatic", api) == ["Rap/Hip Hop"]


def test_unknown_album_has_no_genres():
    assert genres.lookup("Nobody", "Nothing", fake_api({})) == []


def test_overrides_beat_cache_beat_lookup(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"_comment": "x", "the beatles - ABBEY ROAD!": "Pop"}))
    data = ClubData(albums=[Album(1, "The Beatles", "Abbey Road"), Album(2, "Nas", "Illmatic"), Album(3, "New", "Album")])
    api = fake_api({"search/album": {"data": [{"id": 7, "title": "Album", "artist": {"name": "New"}}]},
                    "album/7": {"genres": {"data": [{"name": "Jazz"}]}}})
    out = genres.attach_genres(
        data,
        overrides=genres.load_overrides(path),
        cache=genres.cache_from_payload({"albums": [
            {"artist": "The Beatles", "album": "Abbey Road", "genres": ["Rock"]},
            {"artist": "nas", "album": "illmatic", "genres": ["Rap/Hip Hop"]},
        ]}),
        get_json=api, pause=0,
    )
    assert [a.genres for a in out.albums] == [("Pop",), ("Rap/Hip Hop",), ("Jazz",)]
    assert all("Abbey" not in u and "Illmatic" not in u for u in api.calls)  # only the new album


def test_no_lookup_leaves_unknown_albums_empty():
    out = genres.attach_genres(ClubData(albums=[Album(1, "A", "B")]), get_json=None)
    assert out.albums[0].genres == ()


def test_bad_override_key(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"No separator here": "Rock"}))
    with pytest.raises(ValueError):
        genres.load_overrides(path)


def test_repo_overrides_file_loads():
    assert genres.load_overrides()  # the committed file parses


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------

def genre_club():
    # Rock x3 (picked by A, A, B), Rap x3 (B, C, C), Jazz x1 (C), one unknown (A).
    albums = [
        Album(1, "r1", "r1", "A", genres=("Rock",)),
        Album(2, "r2", "r2", "A", genres=("Rock", "Metal")),
        Album(3, "r3", "r3", "B", genres=("Rock",)),
        Album(4, "h1", "h1", "B", genres=("Rap/Hip Hop",)),
        Album(5, "h2", "h2", "C", genres=("Rap/Hip Hop",)),
        Album(6, "h3", "h3", "C", genres=("Rap/Hip Hop",)),
        Album(7, "j1", "j1", "C", genres=("Jazz",)),
        Album(8, "u1", "u1", "A"),
    ]
    # A loves rock, hates rap. B and C the reverse. Everyone gives 50 to the rest.
    scores = {
        "A": {1: 90, 2: 90, 3: 90, 4: 20, 5: 20, 6: 20, 7: 50, 8: 50},
        "B": {1: 30, 2: 30, 3: 30, 4: 80, 5: 80, 6: 80, 7: 50, 8: 50},
        "C": {1: 30, 2: 40, 3: 50, 4: 90, 5: 90, 6: 90, 7: 50, 8: 50},
    }
    reviews = [Review(aid, l, float(s)) for l, row in scores.items() for aid, s in row.items()]
    return ClubData(albums=albums, reviews=reviews, listeners=["A", "B", "C"])


def test_genre_breakdown():
    g = analytics.build_payload(genre_club())["genres"]
    by = {x["genre"]: x for x in g["genres"]}
    assert [x["genre"] for x in g["genres"]] == ["Rap/Hip Hop", "Rock", "Jazz", "Unknown"]
    assert by["Rock"]["album_count"] == 3 and by["Rock"]["requesters"] == {"A": 2, "B": 1}
    assert by["Rock"]["mean"] == pytest.approx(53.33, abs=0.01)
    assert by["Rap/Hip Hop"]["mean"] == pytest.approx(63.33, abs=0.01)
    assert g["chart_genres"] == ["Rap/Hip Hop", "Rock", "Other"]  # Jazz (1 album) and Unknown fold in
    assert g["album_genre"]["7"] == "Other" and g["album_genre"]["8"] == "Other"
    assert g["unknown_count"] == 1
    assert g["superlatives"]["most_loved"]["genres"] == ["Rap/Hip Hop"]
    assert g["superlatives"]["least_loved"]["genres"] == ["Rock"]
    assert g["superlatives"]["most_picked"]["genres"] == ["Rap/Hip Hop", "Rock"]  # tie kept
    assert g["superlatives"]["most_varied_picker"] == {"requesters": ["B", "C"], "value": 2.0}
    assert g["by_requester"]["C"] == {"Rap/Hip Hop": 2, "Other": 1}


def test_tastes_exclude_self_scores():
    t = analytics.build_payload(genre_club())["genres"]["tastes"]
    # A's own picks (1, 2, 8) are left out: A's rock is just album 3.
    assert t["A"]["counts"]["Rock"] == 1
    assert t["A"]["avg"]["Rock"] == 90
    # ...too few rock scores to count, so rap is A's only ranked genre, and
    # one genre isn't a preference: no favorite or least favorite.
    assert t["A"]["favorite"] == [] and t["A"]["least_favorite"] == []
    # B has only two scores in each named genre once self-picks are out.
    assert t["B"]["counts"] == {"Rap/Hip Hop": 2, "Rock": 2, "Other": 2} and t["B"]["favorite"] == []
    c = t["C"]
    assert c["counts"]["Rock"] == 3 and c["counts"]["Rap/Hip Hop"] == 1  # own rap picks out
    assert c["affinity"]["Rock"] == pytest.approx(40 - c["mean"], abs=0.01)
    assert c["favorite"] == [] and c["least_favorite"] == []


def test_favorite_and_least_favorite_genre():
    # Z (picks nothing) scores three rock and three rap albums picked by Y.
    albums = [Album(i, f"a{i}", f"a{i}", "Y", genres=("Rock",) if i <= 3 else ("Pop",)) for i in range(1, 7)]
    reviews = [Review(i, "Z", 90.0 if i <= 3 else 60.0) for i in range(1, 7)]
    t = analytics.build_payload(ClubData(albums=albums, reviews=reviews, listeners=["Y", "Z"]))["genres"]["tastes"]["Z"]
    assert t["favorite"] == ["Rock"] and t["least_favorite"] == ["Pop"]
    assert t["affinity"] == {"Rock": 15.0, "Pop": -15.0}


def test_no_genres_at_all():
    g = analytics.build_payload(ClubData(albums=[Album(1, "a", "b", "A")], reviews=[], listeners=["A"]))["genres"]
    assert g["chart_genres"] == ["Other"] and g["unknown_count"] == 1
    assert g["superlatives"]["most_loved"] == {"genres": [], "value": None}
