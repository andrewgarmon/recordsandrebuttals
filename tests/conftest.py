"""Shared fixtures. Puts ``src/`` on the path the way the Worker sees it."""

import os
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


# Shaped like the real sheet: whitespace date header, three repeated headers
# per listener, stray whitespace, a blank row, a half-filled row, and an
# Average column the parser must ignore. Scores are on a 0-100 scale.
RAW_SHEET_CSV = """\
" ",Requester,Artist,Album,Release Year,Decade,Alice,Alice,Alice,Bob,Bob,Bob,Carol,Carol,Carol,Average
1/5/2024,Alice,The Beatles ,Abbey Road,1969,60s,90,Come Together,Octopus's Garden,70,Something,,80,Here Comes the Sun,,80
2/2/2024,Bob, The Rolling Stones,Let It Bleed,1969,60s,80,Gimme Shelter,,90,Monkey Man,,70,Midnight Rambler,,80
3/1/2024,Carol,Led Zeppelin,IV,1971,70s,70,Black Dog,Four Sticks,100,Stairway to Heaven,,90,Rock and Roll,,86.67
,,,,,,,,,,,,,,,
4/5/2024,Alice,,Missing Artist Row,1999,90s,50,,,50,,,50,,,50
4/9/2024,Bob,Radiohead,OK Computer,1997,,0,Airbag,,100,Let Down,,,,,50
"""


@pytest.fixture
def raw_csv() -> str:
    return RAW_SHEET_CSV


@pytest.fixture
def club(raw_csv):
    import sheet_parser

    return sheet_parser.parse_csv(raw_csv)
