import pytest

import sheet_parser
from sheet_parser import Album, Review, SheetFormatError, parse_csv, sheet_csv_url


def test_url_uses_gviz_by_sheet_name():
    url = sheet_csv_url("abc123")
    assert url == "https://docs.google.com/spreadsheets/d/abc123/gviz/tq?tqx=out:csv&sheet=Albums"


def test_url_uses_export_with_gid():
    url = sheet_csv_url("abc123", gid="987")
    assert url == "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=987"
    assert "sheet=" not in url


def test_listeners_detected_positionally(club):
    assert club.listeners == ["Alice", "Bob", "Carol"]


def test_blank_and_incomplete_rows_dropped(club):
    assert [a.album for a in club.albums] == ["Abbey Road", "Let It Bleed", "IV", "OK Computer"]


def test_album_ids_are_sequential_and_stable(club):
    assert [a.album_id for a in club.albums] == [1, 2, 3, 4]


def test_values_are_stripped(club):
    assert club.albums[0].artist == "The Beatles"
    assert club.albums[1].artist == "The Rolling Stones"


def test_whitespace_header_becomes_date_and_is_iso(club):
    assert club.albums[0].date == "2024-01-05"


def test_decade_kept_from_sheet_or_derived(club):
    assert club.albums[0].decade == "60s"
    assert club.albums[3].decade == "1990s"  # blank Decade cell -> derived
    assert club.albums[3].release_year == 1997


def test_reviews_map_score_and_tracks(club):
    abbey_alice = next(r for r in club.reviews if r.album_id == 1 and r.listener == "Alice")
    assert abbey_alice == Review(1, "Alice", 90.0, "Come Together", "Octopus's Garden")
    abbey_bob = next(r for r in club.reviews if r.album_id == 1 and r.listener == "Bob")
    assert abbey_bob.least_favorite_track is None


def test_missing_score_means_no_review(club):
    ok_computer = [r for r in club.reviews if r.album_id == 4]
    assert {r.listener for r in ok_computer} == {"Alice", "Bob"}


def test_zero_score_is_a_review(club):
    assert any(r.album_id == 4 and r.listener == "Alice" and r.score == 0 for r in club.reviews)


def test_average_column_ignored(club):
    assert "Average" not in club.listeners
    assert len(club.reviews) == 3 * 3 + 2


def test_pandas_style_suffixes():
    csv_text = (
        "Date,Requester,Artist,Album,Alice,Alice.1,Alice.2,Bob,Bob.1,Bob.2,Average\n"
        "2024-01-01,Alice,A,B,55.5,Fav,Least,60,F2,,57.75\n"
    )
    data = parse_csv(csv_text)
    assert data.listeners == ["Alice", "Bob"]
    assert data.reviews[0] == Review(1, "Alice", 55.5, "Fav", "Least")
    assert data.albums[0] == Album(1, "A", "B", "Alice", "2024-01-01", None, None)


def test_blank_track_headers_belong_to_preceding_listener():
    csv_text = (
        "Date,Requester,Artist,Album,Alice,,,Bob,,,Average\n"
        "2024-01-01,Alice,A,B,50,Fav,Least,60,F2,L2,55\n"
    )
    data = parse_csv(csv_text)
    assert data.listeners == ["Alice", "Bob"]
    bob = data.reviews[1]
    assert (bob.favorite_track, bob.least_favorite_track) == ("F2", "L2")


def test_existing_date_column_not_overwritten():
    csv_text = " ,Date,Requester,Artist,Album,Alice,Average\nx,2024-03-03,Alice,A,B,10,10\n"
    data = parse_csv(csv_text)
    assert data.albums[0].date == "2024-03-03"


def test_listener_without_track_columns():
    csv_text = "Date,Requester,Artist,Album,Alice,Bob,Average\n2024-01-01,Bob,A,B,10,20,15\n"
    data = parse_csv(csv_text)
    assert data.listeners == ["Alice", "Bob"]
    assert data.reviews[0].favorite_track is None


def test_non_numeric_scores_skipped():
    csv_text = "Date,Requester,Artist,Album,Alice,Bob,Average\n2024-01-01,Bob,A,B,n/a,20,20\n"
    data = parse_csv(csv_text)
    assert [r.listener for r in data.reviews] == ["Bob"]


def test_missing_average_column_raises():
    with pytest.raises(SheetFormatError):
        parse_csv("Date,Artist,Album,Alice\n2024-01-01,A,B,1\n")


def test_empty_csv():
    data = parse_csv("")
    assert data.albums == [] and data.reviews == [] and data.listeners == []


def test_bom_and_short_rows():
    csv_text = "﻿Date,Requester,Artist,Album,Alice,Bob,Average\n2024-01-01,Bob,A,B,10\n"
    data = parse_csv(csv_text)
    assert data.albums[0].date == "2024-01-01"
    assert [r.listener for r in data.reviews] == ["Alice"]


def test_unicode_survives():
    csv_text = (
        "Date,Requester,Artist,Album,Alice,Average\n"
        "2024-01-01,Alice,Lynyrd Skynyrd,(Pronounced 'Lĕh-'nérd 'Skin-'nérd),88,88\n"
    )
    data = parse_csv(csv_text)
    assert data.albums[0].album == "(Pronounced 'Lĕh-'nérd 'Skin-'nérd)"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-01-05", "2024-01-05"),
        ("1/5/2024", "2024-01-05"),
        ("1/5/24", "2024-01-05"),
        ("1/5/2024 0:00:00", "2024-01-05"),
        ("not a date", "not a date"),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert sheet_parser._parse_date(raw) == expected
