"""Fixtures: a real captured page, a temporary archive, and a config to match.

The HTML fixture is a genuine Centanet response with the served DOM trimmed
away -- the DOM carries no transaction rows, so nothing was lost. Keeping a real
payload rather than a hand-written one is the point: the fragile part of this
package is the minifier's output, and a synthetic fixture would only ever test
the shape this code already assumes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hk_transaction_tracker import db
from hk_transaction_tracker.config import load_config
from hk_transaction_tracker.models import Transaction

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def page_html() -> str:
    return (FIXTURES / "transaction_list.html").read_text(encoding="utf-8")


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "archive.db")
    yield connection
    connection.close()


def write_config(tmp_path: Path, estates: list[dict], **top) -> Path:
    payload = {
        "timezone": "Asia/Hong_Kong",
        "request_delay_seconds": 0,
        "estates": estates,
        **top,
    }
    path = tmp_path / "estates.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def config_factory(tmp_path):
    def build(estates: list[dict] | None = None, **top):
        estates = estates if estates is not None else [{
            "name": "泓都",
            "label": "泓都 Island Harbourview",
            "url": "https://hk.centanet.com/findproperty/list/transaction/x_2-Y",
            "bedrooms": [2, 3],
            "size_ranges": [[500, 700]],
        }]
        return load_config(write_config(tmp_path, estates, **top))

    return build


def make_transaction(**overrides) -> Transaction:
    """A plausible sale, with every field overridable."""
    base = dict(
        estate="泓都",
        tx_id="TX1",
        deal_type="sale",
        price=12_400_000.0,
        ins_date=date(2026, 8, 17),
        estate_name="泓都",
        building="2座",
        floor="57樓",
        unit="A室",
        bedrooms=2,
        saleable_area=507.0,
        saleable_unit_price=24_458.0,
    )
    base.update(overrides)
    return Transaction(**base)


@pytest.fixture
def transaction():
    return make_transaction
