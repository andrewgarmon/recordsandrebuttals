import json

import pytest

import build_data


def test_build_from_csv(tmp_path, raw_csv):
    src = tmp_path / "sheet.csv"
    src.write_text(raw_csv, encoding="utf-8")
    out = tmp_path / "club-data.json"
    assert build_data.main(["--csv", str(src), "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["overview"]["album_count"] == 4
    assert payload["generated_at"].endswith("+00:00")


def test_refuses_to_write_empty_data(tmp_path):
    src = tmp_path / "sheet.csv"
    src.write_text("Date,Artist,Album,Alice,Average\n", encoding="utf-8")
    out = tmp_path / "club-data.json"
    assert build_data.main(["--csv", str(src), "--out", str(out)]) == 1
    assert not out.exists()


def test_rejects_non_sheet_response(tmp_path):
    # e.g. a Google sign-in page if the sheet stops being link-shared
    src = tmp_path / "sheet.csv"
    src.write_text("<html><body>Sign in</body></html>", encoding="utf-8")
    with pytest.raises(Exception):
        build_data.main(["--csv", str(src), "--out", str(tmp_path / "x.json")])


def test_needs_a_source(monkeypatch):
    monkeypatch.delenv("SHEETS_DOC_ID", raising=False)
    with pytest.raises(SystemExit):
        build_data.main([])
