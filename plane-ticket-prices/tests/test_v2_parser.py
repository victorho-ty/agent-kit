"""Parser tests against the REAL captured Google Flights label set.

Fixtures: tests/fixtures/gf_labels_2026.json, captured 2026-08-02 by
scripts/capture_fixtures.py. If Google changes the label schema, re-run that
script and adjust the parser -- do not hand-edit these expectations.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plane_ticket_prices import parsing

FIXTURES = Path(__file__).parent / "fixtures" / "gf_labels_2026.json"
REAL = json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pen_codes() -> dict[str, str]:
    boxes = REAL["HKG-PEN-nonstop"]["search_boxes"]
    return parsing.learn_airport_codes(boxes)


def cards(section: str) -> list[str]:
    """The priced option cards ("From ... Select flight") in a fixture section."""
    return [l for l in REAL[section]["outbound"] if l.startswith("From ")]


class TestLearnAirportCodes:
    def test_search_boxes_yield_city_code_map(self):
        codes = parsing.learn_airport_codes(REAL["HKG-PEN-nonstop"]["search_boxes"])
        assert codes == {"hong kong": "HKG", "penang": "PEN"}

    def test_dxb_search_boxes(self):
        codes = parsing.learn_airport_codes(REAL["HKG-DXB-1stop"]["search_boxes"])
        assert codes == {"hong kong": "HKG", "dubai": "DXB"}


class TestRealOutboundCards:
    def test_all_priced_cards_parse_with_legs_and_price(self, pen_codes):
        for label in cards("HKG-PEN-nonstop"):
            option = parsing.parse_label(label, pen_codes)
            assert option is not None, label
            assert option.price is not None, label
            assert option.currency == "HKD", label
            assert len(option.legs) == 1, label
            leg = option.legs[0]
            assert leg.depart_airport == "HKG", label
            assert leg.arrive_airport == "PEN", label
            assert leg.airline in ("Hong Kong Express", "Cathay Pacific"), label

    def test_cheapest_hk_express_card_details(self, pen_codes):
        label = next(l for l in cards("HKG-PEN-nonstop") if "2514" in l)
        option = parsing.parse_label(label, pen_codes)
        assert option.price == 2514.0
        leg = option.legs[0]
        assert leg.airline == "Hong Kong Express"
        assert leg.stops == 0
        assert leg.depart_time == "11:55"
        assert leg.arrive_time == "15:40"
        assert leg.duration_min == 225          # stated: 3 hr 45 min
        assert option.round_trip is True

    def test_red_eye_crossing_midnight(self, pen_codes):
        label = next(l for l in cards("HKG-PEN-nonstop") if "8:20 PM" in l)
        option = parsing.parse_label(label, pen_codes)
        leg = option.legs[0]
        assert leg.depart_time == "20:20"
        assert leg.arrive_time == "00:05"
        assert leg.arrive_day_shift == 1        # Dec 18 20:20 -> Dec 19 00:05
        assert leg.duration_min == 225          # 20:20 -> 00:05 = 3h45m
        from plane_ticket_prices.db import bucket_from_hour
        assert bucket_from_hour(leg.depart_hour) == "18-21"

    def test_same_day_arrival_has_no_shift(self, pen_codes):
        label = next(l for l in cards("HKG-PEN-nonstop") if "11:55 AM" in l)
        leg = parsing.parse_label(label, pen_codes).legs[0]
        assert leg.arrive_day_shift == 0

    def test_cathay_card(self, pen_codes):
        label = next(l for l in cards("HKG-PEN-nonstop") if "Cathay" in l)
        option = parsing.parse_label(label, pen_codes)
        assert option.price == 7560.0
        assert option.legs[0].airline == "Cathay Pacific"
        assert option.legs[0].duration_min == 230  # 3 hr 50 min


class TestRealOneStop:
    def test_airline_is_first_carrier(self):
        codes = parsing.learn_airport_codes(REAL["HKG-DXB-1stop"]["search_boxes"])
        label = next(l for l in cards("HKG-DXB-1stop") if "12082" in l)
        option = parsing.parse_label(label, codes)
        assert option.price == 12082.0
        leg = option.legs[0]
        assert leg.airline == "Cathay Pacific"   # "Cathay Pacific and Emirates"
        assert leg.stops == 1
        assert leg.depart_airport == "HKG"
        assert leg.arrive_airport == "DXB"
        assert leg.duration_min == 750           # 12 hr 30 min

    def test_emirates_one_stop(self):
        codes = parsing.learn_airport_codes(REAL["HKG-DXB-1stop"]["search_boxes"])
        label = next(l for l in cards("HKG-DXB-1stop") if "5056" in l)
        option = parsing.parse_label(label, codes)
        assert option.price == 5056.0
        assert option.legs[0].airline == "Emirates"
        assert option.legs[0].stops == 0


class TestRealVariants:
    def test_flight_details_and_bare_leaves_parse_without_price(self, pen_codes):
        for label in REAL["HKG-PEN-nonstop"]["outbound"]:
            if label.startswith("From "):
                continue
            option = parsing.parse_label(label, pen_codes)
            assert option is not None, label
            assert option.price is None, label

    def test_noise_labels_are_skipped(self, pen_codes):
        for label in REAL["HKG-PEN-nonstop"]["noise"] + REAL["HKG-DXB-1stop"]["noise"]:
            assert parsing.parse_label(label, pen_codes) is None, label

    def test_direction_extraction_with_codes(self, pen_codes):
        labels = REAL["HKG-PEN-nonstop"]["outbound"]
        out = parsing.extract_outbound_options(labels, "HKG", "PEN", pen_codes)
        back = parsing.extract_return_options(labels, "HKG", "PEN", pen_codes)
        assert len(out) >= 3
        assert back == []

    def test_return_grid_direction(self, pen_codes):
        labels = REAL["HKG-PEN-return-grid"]["return"]
        out = parsing.extract_outbound_options(labels, "HKG", "PEN", pen_codes)
        back = parsing.extract_return_options(labels, "HKG", "PEN", pen_codes)
        assert out == []
        assert len(back) >= 2
        option = parsing.parse_label(
            next(l for l in labels if l.startswith("From 2514")), pen_codes)
        assert option.price == 2514.0
        assert option.legs[0].depart_airport == "PEN"
        assert option.legs[0].arrive_airport == "HKG"

    def test_dedupe_prefers_priced_card_over_bare_duplicate(self, pen_codes):
        from plane_ticket_prices.crawler import select_outbound_options
        labels = REAL["HKG-PEN-nonstop"]["outbound"]
        # crawler contract: only priced cards reach the dedupe
        options = [o for o in (parsing.parse_label(l, pen_codes) for l in labels)
                   if o and o.price is not None]
        picked = select_outbound_options(options, max_cells=10)
        assert all(o.price is not None for o in picked)
        airlines = {o.legs[0].airline for o in picked}
        assert "Hong Kong Express" in airlines and "Cathay Pacific" in airlines
        # one cell per (airline, bucket): HK Express 09-12, HK Express 18-21, CX 15-18
        assert len(picked) == 3


class TestCellAggregationReal:
    def test_hk_express_pair_yields_cells(self, pen_codes):
        from datetime import date
        from plane_ticket_prices.config.scope import TravelScope
        from plane_ticket_prices.crawler import cells_and_itineraries

        scope = TravelScope(
            name="HKG-PEN-Real", from_airport="HKG", to_airport="PEN",
            depart_from=date(2026, 12, 18), depart_to=date(2026, 12, 20),
            return_from=date(2026, 12, 22), return_to=date(2026, 12, 26),
        )
        outbound = parsing.parse_label(
            next(l for l in cards("HKG-PEN-nonstop") if "2514" in l), pen_codes)
        returns = [
            parsing.parse_label(l, pen_codes)
            for l in REAL["HKG-PEN-return-grid"]["return"]
        ]
        cells, its = cells_and_itineraries(
            outbound, [r for r in returns if r], scope=scope, run_date="2026-08-02",
            depart=date(2026, 12, 18), returnd=date(2026, 12, 22),
        )
        # two return options: 00-03 bucket (12:50 AM, 2514) and 15-18 (4:25 PM, 2774)
        by_bucket = {c["ret_bucket"]: c["min_price"] for c in cells}
        assert by_bucket["00-03"] == 2514.0
        assert by_bucket["15-18"] == 2774.0
        assert cells[0]["airline"] == "Hong Kong Express"
        assert its[0]["out_arrive"] == "2026-12-18T15:40:00"   # stated arrival time
        # the red-eye return arrives the NEXT day: 00:50 Dec 22 -> arrival 04:45 Dec 22,
        # and the 12:50 AM return departs Dec 22 (no shift on depart date)
        assert its[0]["ret_depart"] == "2026-12-22T00:50:00"
