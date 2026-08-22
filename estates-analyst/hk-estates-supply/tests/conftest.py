"""Fixtures. No network, no wall clock, no PDF binaries.

The PDF layer is exercised through ``rows_from_words``, which takes plain word
dicts -- so the trap this bundle exists to avoid (a figure and its label on the
same visual row but different baselines) is testable without shipping a 500KB
binary that nobody can diff.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).parent / "fixtures"

HISTORY_CSV = """Quarter,LandReady,BeingBuilt,BuiltNotSold,Total
2026/Jun,16000,61000,19000,96000
2026/Mar,19000,62000,20000,101000
2025/Dec,20000,61000,23000,104000
2025/Sep,14000,62000,26000,102000
2025/Jun,10000,64000,27000,101000
2025/Mar,12000,65000,28000,105000
"""

# The header as the inherited file actually carried it: Chinese written through
# a console that could not encode it. The numbers underneath are intact, and
# reading positionally must not care.
MOJIBAKE_HEADER_CSV = """Quarter,?????,???/?????,????,Total
2026/Jun,16000,61000,19000,96000
2026/Mar,19000,62000,20000,101000
"""


@pytest.fixture
def history_file(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text(HISTORY_CSV, encoding="utf-8")
    return path


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def runs_file(tmp_path):
    return tmp_path / "runs.jsonl"


@pytest.fixture
def index_html():
    return (FIXTURES / "index_page.html").read_text(encoding="utf-8-sig")


def word(text: str, *, x0: float, top: float, height: float = 12.0, width: float = None):
    """One pdfplumber word box. ``width`` defaults to something plausible."""
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + (width if width is not None else 8.0 * len(text)),
        "top": top,
        "bottom": top + height,
    }
