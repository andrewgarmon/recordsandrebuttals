"""Shared fixtures. Puts ``src/`` on the path the way the Worker sees it."""

import os
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)


# Shaped like the live sheet (gviz CSV export): whitespace date header,
# a score block, Average/Median, then Favorite and Worst blocks that repeat
# the listener names, Decade written as "1960", trailing blank columns,
# stray whitespace, a blank row and a half-filled row. Scale is 0-100.
RAW_SHEET_CSV = """\
"         ",Requester,Artist,Album,Release Year,Alice,Bob,Carol,Average,Median,Favorite,Alice,Bob,Carol,Worst,Alice,Bob,Carol,Decade,,
1/5/2024,Alice,The Beatles ,Abbey Road,1969,90,70,80,80,80,,Come Together,Something,Here Comes the Sun,,Octopus's Garden,,,1960,,
2/2/2024,Bob, The Rolling Stones,Let It Bleed,1969,80,90,70,80,80,,Gimme Shelter,Monkey Man,Midnight Rambler,,,,,1960,,
3/1/2024,Carol,Led Zeppelin,IV,1971,70,100,90,86.7,90,,Black Dog,Stairway to Heaven,Rock and Roll,,Four Sticks,,,1970,,
,,,,,,,,,,,,,,,,,,,,
4/5/2024,Alice,,Missing Artist Row,1999,50,50,50,50,50,,,,,,,,,1990,,
4/9/2024,Bob,Radiohead,OK Computer,1997,0,100,,50,50,,Airbag,Let Down,,,,,,,,
"""


@pytest.fixture
def raw_csv() -> str:
    return RAW_SHEET_CSV


@pytest.fixture
def club(raw_csv):
    import sheet_parser

    return sheet_parser.parse_csv(raw_csv)
