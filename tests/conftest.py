"""Shared fixtures and helpers.

Fixtures live in ``test-data/`` at the repository root — one root, shared with
the Galaxy wrapper later, so the two cannot drift apart.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "test-data"

# The 6.4 MB reference file cannot be committed: the Galaxy tool repository caps
# any single file at 1 MB. Tests needing it skip when it is absent, and it is
# archived on Zenodo so the check can be run deliberately.
REFERENCE_TXT = REPO.parent / "Galaxy-tool" / "Thebenletzte.txt"
REFERENCE_TSV = REPO.parent / "Galaxy-tool" / "Thebenletzte.tsv"

EXPECTED_RANGES = [
    (1, 12),
    (18, 27),
    (31, 37),
    (42, 47),
    (53, 64),
    (70, 73),
    (77, 80),
    (85, 89),
    (92, 93),
    (100, 110),
    (115, 125),
]
EXPECTED_NAMES = [
    "DATE",
    "BEST_TIME",
    "DELTAT_s",
    "sigma",
    "Egyptian_Date",
    "TSun",
    "TMoon",
    "q",
    "code",
    "NEW_MOON_DATE",
    "TIME",
]

needs_reference = pytest.mark.skipif(
    not REFERENCE_TXT.exists(),
    reason=(
        "the 6.4 MB reference file is not present; it exceeds the 1 MB "
        "per-file limit of the Galaxy tool repository and is archived on "
        "Zenodo instead"
    ),
)


@pytest.fixture
def clean() -> Path:
    return DATA / "fixedwidth_input.txt"


@pytest.fixture
def messy() -> Path:
    return DATA / "fixedwidth_messy.txt"


@pytest.fixture
def expected_tsv() -> Path:
    return DATA / "fixedwidth_expected.tsv"


def non_blank_positions(path: Path, encoding: str = "utf-8") -> set[int]:
    """1-based positions that carry a non-blank character in some record."""
    lines = [ln for ln in path.read_text(encoding=encoding).split("\n") if ln]
    if not lines:
        return set()
    width = Counter(len(ln) for ln in lines).most_common(1)[0][0]
    records = [ln for ln in lines if len(ln) == width]
    return {i + 1 for i in range(width) if any(rec[i] != " " for rec in records)}


def covers(fixture: Path, source: Path) -> bool:
    """Whether ``fixture`` exercises every position that ``source`` does.

    DC-4, stated as a comparison — a self-contained check is worthless here,
    since a fixture is always trivially coverage-complete with respect to
    itself. A fixture that fails this has *different correct geometry* from the
    data it was drawn from: positions merely usually blank become always blank
    in a small sample, so extra gutters appear and the boundaries shift. On the
    reference file the first 5, 10 and 20 records all produce wrong boundaries,
    and two of those still produce the right field *count* — so a test built on
    such a fixture passes while asserting nonsense.
    """
    return non_blank_positions(source) <= non_blank_positions(fixture)
