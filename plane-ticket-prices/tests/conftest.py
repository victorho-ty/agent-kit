"""Shared fixtures: a temp DB seeded with synthetic-but-realistic price rows."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from plane_ticket_prices import db

# Realistic aria-labels modelled on Google Flights' en-US list view.
HKG_DXB_OUTBOUND = (
    "Flights from Hong Kong to Dubai, nonstop. "
    "9:35 AM HKG to DXB, 3h 45m, Cathay Pacific. "
    "3:05 PM DXB to HKG, 3h 30m, Cathay Pacific. "
    "$6,542 round trip, 6,542 HK dollars, total for all 1 passenger."
)

HKG_DXB_EMIRATES = (
    "Flights from Hong Kong to Dubai, nonstop. "
    "8:10 AM HKG to DXB, 8h 25m, Emirates. "
    "4:40 PM DXB to HKG, 7h 55m, Emirates. "
    "$5,890 round trip, total for all 1 passenger."
)

HKG_DXB_QATAR_1STOP = (
    "Flights from Hong Kong to Dubai, 1 stop. "
    "12:10 AM HKG to DXB, 11h 20m, 1 stop, Qatar Airways. "
    "2:30 PM DXB to HKG, 9h 45m, 1 stop, Qatar Airways. "
    "$4,100 round trip, total for all 1 passenger."
)

# Narrow no-break space (U+202F) before AM/PM, as Google emits it.
HKG_DXB_NBSP = (
    "Flights from Hong Kong to Dubai, nonstop. "
    "9:35\u202fAM HKG to DXB, 3h 45m, Cathay Pacific. "
    "3:05\u202fPM DXB to HKG, 3h 30m, Cathay Pacific. "
    "HK$5,200 round trip."
)

# Red-eye crossing midnight: departs 23:55, returns 06:50.
HKG_PEN_REDEYE = (
    "Flights from Hong Kong to Penang, nonstop. "
    "11:55 PM HKG to PEN, 3h 40m, AirAsia. "
    "6:50 AM PEN to HKG, 3h 45m, AirAsia. "
    "HK$1,240 round trip, total for all 1 passenger."
)

# A return-grid option shown after selecting an outbound flight.
DXB_HKG_RETURN_GRID = (
    "Tue, Dec 22. 3:05 PM DXB to HKG, 3h 30m, nonstop, Cathay Pacific. "
    "Price: HK$5,200"
)

# Headers / buttons that must be skipped by the parser.
NOISE_LABELS = [
    "Flights",
    "Price",
    "Sort by: Best",
    "More options",
    "1 of 3",
    "Best departing flights",
]


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "ticket_prices.db"
    monkeypatch.setenv("TICKET_PRICES_DB", str(db_path))
    conn = db.connect()
    conn.close()
    return db_path


def seed_series(conn: sqlite3.Connection, scope: str, *, days: int, seed_start: date,
                currency: str = "HKD") -> None:
    """Seed `days` of runs for `scope` with plausible per-cell prices.

    Each run day has two airlines x two departure buckets x one return bucket,
    with a mild day-over-day drift so trend/WoW queries see movement.
    """
    base = {
        "scope": scope, "origin": "HKG", "dest": "DXB",
        "depart_date": date(2026, 12, 18), "return_date": date(2026, 12, 22),
        "out_stops": 0, "ret_stops": 0, "seat": "economy", "currency": currency,
    }
    cells = [
        {"airline": "Cathay Pacific", "dep_bucket": "09-12", "ret_bucket": "15-18", "base": 6500.0},
        {"airline": "Cathay Pacific", "dep_bucket": "21-24", "ret_bucket": "03-06", "base": 5800.0},
        {"airline": "Emirates", "dep_bucket": "06-09", "ret_bucket": "15-18", "base": 6100.0},
        {"airline": "Emirates", "dep_bucket": "12-15", "ret_bucket": "18-21", "base": 7200.0},
    ]
    for offset in range(days):
        run_date = (seed_start + timedelta(days=offset)).isoformat()
        for i, cell in enumerate(cells):
            # deterministic drift: -1.5% per day, plus a per-cell sine wiggle
            price = cell["base"] * (1 - 0.015 * offset + 0.03 * ((offset + i) % 3 - 1))
            db.upsert_cell(conn, {
                "run_date": run_date, "scope": scope,
                "origin": "HKG", "dest": "DXB",
                "depart_date": date(2026, 12, 18), "return_date": date(2026, 12, 22),
                "airline": cell["airline"], "dep_bucket": cell["dep_bucket"],
                "ret_bucket": cell["ret_bucket"], "out_stops": 0, "ret_stops": 0,
                "seat": "economy", "currency": "HKD", "min_price": round(price, 2),
                "n_itineraries": 1,
            })
