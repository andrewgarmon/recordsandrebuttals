import json

import pytest

import build_data


def test_build_from_csv(tmp_path, raw_csv):
    src = tmp_path / "sheet.csv"
    src.write_text(raw_csv, encoding="utf-8")
    out = tmp_path / "club-data.json"
    assert build_data.main(["--csv", str(src), "--out", str(out), "--no-genre-lookup"]) == 0
    payload = json.loads(out.read_text())
    assert payload["overview"]["album_count"] == 4
    assert payload["generated_at"].endswith("+00:00")


def test_refuses_to_write_empty_data(tmp_path):
    src = tmp_path / "sheet.csv"
    src.write_text("Date,Artist,Album,Alice,Average\n", encoding="utf-8")
    out = tmp_path / "club-data.json"
    assert build_data.main(["--csv", str(src), "--out", str(out), "--no-genre-lookup"]) == 1
    assert not out.exists()


def test_rejects_non_sheet_response(tmp_path):
    # e.g. a Google sign-in page if the sheet stops being link-shared
    src = tmp_path / "sheet.csv"
    src.write_text("<html><body>Sign in</body></html>", encoding="utf-8")
    with pytest.raises(Exception):
        build_data.main(["--csv", str(src), "--out", str(tmp_path / "x.json"), "--no-genre-lookup"])


def test_needs_a_source(monkeypatch):
    monkeypatch.delenv("SHEETS_DOC_ID", raising=False)
    with pytest.raises(SystemExit):
        build_data.main([])


def test_reuses_genres_from_previous_build(tmp_path, raw_csv, monkeypatch):
    import genres

    monkeypatch.setattr(genres, "http_get_json", lambda url: pytest.fail(f"network call: {url}"))
    monkeypatch.setattr(genres, "OVERRIDES_PATH", tmp_path / "none.json")
    src = tmp_path / "sheet.csv"
    src.write_text(raw_csv, encoding="utf-8")
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"albums": [
        {"artist": "The Beatles", "album": "Abbey Road", "genres": ["Rock"]},
        {"artist": "The Rolling Stones", "album": "Let It Bleed", "genres": ["Rock"]},
        {"artist": "Led Zeppelin", "album": "IV", "genres": ["Rock", "Hard Rock"]},
        {"artist": "Radiohead", "album": "OK Computer", "genres": ["Alternative"]},
    ]}))
    out = tmp_path / "club-data.json"
    assert build_data.main(["--csv", str(src), "--out", str(out), "--previous", str(prev)]) == 0
    payload = json.loads(out.read_text())
    assert [a["genre"] for a in payload["albums"]] == ["Rock", "Rock", "Rock", "Alternative"]
    assert payload["genres"]["chart_genres"] == ["Rock", "Other"]


def test_missing_previous_build_is_fine(tmp_path, raw_csv):
    src = tmp_path / "sheet.csv"
    src.write_text(raw_csv, encoding="utf-8")
    out = tmp_path / "club-data.json"
    args = ["--csv", str(src), "--out", str(out), "--previous", str(tmp_path / "nope.json"), "--no-genre-lookup"]
    assert build_data.main(args) == 0
