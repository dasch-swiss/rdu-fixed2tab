"""Shared fixtures. Constants and helper functions live in ``helpers``."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import DATA


@pytest.fixture
def clean() -> Path:
    return DATA / "fixedwidth_input.txt"


@pytest.fixture
def messy() -> Path:
    return DATA / "fixedwidth_messy.txt"


@pytest.fixture
def expected_tsv() -> Path:
    return DATA / "fixedwidth_expected.tsv"
