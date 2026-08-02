"""Parser tests -- the deterministic core. No network, no browser."""
from __future__ import annotations

from datetime import date

import pytest

from plane_ticket_prices import parsing
from plane_ticket_prices.crawler import (
    build_search_url,
    cells_and_itineraries,
    decode_tfs,
    select_outbound_options,
)
from plane_ticket_prices.db import bucket_from_hour
from plane_ticket_prices.config.scope import TravelScope
from tests.conftest import (
    DXB_HKG_RETURN_GRID,
    HKG_DXB_EMIRATES,
    HKG_DXB_NBSP,
    HKG_DXB_OUTBOUND,
    HKG_DXB_QATAR_1STOP,
    HKG_PEN_REDEYE,
    NOISE_LABELS,
)


def parse_first(label: str):
    parsed = parsing.parse_label(label)
    assert parsed is not None, f"label should parse: {label!r}"
    return parsed


class TestParseLabel:
    def test_basic_round_trip(self):
        option = parse_first(HKG_DXB_OUTBOUND)
        assert len(option.legs) == 2
        out, back = option.legs
        assert (out.depart_time, out.depart_airport, out.arrive_airport) == ("09:35", "HKG", "DXB")
        assert out.duration_min == 3 * 60 + 45
        assert out.airline == "Cathay Pacific"
        assert out.stops == 0
        assert (back.depart_time, back.depart_airport) == ("15:05", "DXB")
        assert option.price == 6542.0
        assert option.currency is None  # bare $ matched, no currency code
        assert option.round_trip is True

    def test_narrow_no_break_space_before_am_pm(self):
        option = parse_first(HKG_DXB_NBSP)
        assert option.legs[0].depart_time == "09:35"
        assert option.legs[1].depart_time == "15:05"
        assert option.price == 5200.0
        assert option.currency == "HK"

    def test_pm_rollover_and_red_eye(self):
        option = parse_first(HKG_PEN_REDEYE)
        out, back = option.legs
        assert out.depart_time == "23:55"      # 11:55 PM -> 23:55
        assert bucket_from_hour(out.depart_hour) == "21-24"
        assert back.depart_time == "06:50"     # 6:50 AM stays 06:50
        assert bucket_from_hour(back.depart_hour) == "06-09"
        assert out.airline == "AirAsia"
        assert option.price == 1240.0

    def test_one_stop(self):
        option = parse_first(HKG_DXB_QATAR_1STOP)
        out, back = option.legs
        assert out.stops == 1
        assert back.stops == 1
        assert out.airline == "Qatar Airways"
        assert out.duration_min == 11 * 60 + 20

    def test_midnight_am_is_zero_hour(self):
        option = parse_first(
            "Flights from Hong Kong to Dubai, nonstop. "
            "12:05 AM HKG to DXB, 8h 0m, Emirates. "
            "11:30 PM DXB to HKG, 7h 55m, Emirates. $5,000 round trip."
        )
        assert option.legs[0].depart_time == "00:05"
        assert bucket_from_hour(option.legs[0].depart_hour) == "00-03"
        assert option.legs[1].depart_time == "23:30"

    def test_price_prefix(self):
        option = parse_first(DXB_HKG_RETURN_GRID)
        assert option.price == 5200.0
        assert option.legs[0].airline == "Cathay Pacific"
        assert option.legs[0].stops == 0

    @pytest.mark.parametrize("label", NOISE_LABELS)
    def test_noise_labels_are_skipped(self, label):
        assert parsing.parse_label(label) is None

    def test_empty_and_none(self):
        assert parsing.parse_label("") is None
        assert parsing.parse_label(None) is None

    def test_hour_minute_duration_variants(self):
        option = parse_first(
            "Flights from Hong Kong to Penang, nonstop. "
            "9:00 AM HKG to PEN, 3 hr 40 min, AirAsia. "
            "1:10 PM PEN to HKG, 3 h 45 m, AirAsia. HK$999 round trip."
        )
        assert option.legs[0].duration_min == 220
        assert option.legs[1].duration_min == 225


class TestDirectionExtraction:
    def test_outbound_vs_return(self):
        outbound = parsing.extract_outbound_options([HKG_DXB_OUTBOUND], "HKG", "DXB")
        returns = parsing.extract_return_options([HKG_DXB_OUTBOUND], "HKG", "DXB")
        assert len(outbound) == 1
        assert len(returns) == 1

    def test_return_grid_label_only_matches_return(self):
        outbound = parsing.extract_outbound_options([DXB_HKG_RETURN_GRID], "HKG", "DXB")
        returns = parsing.extract_return_options([DXB_HKG_RETURN_GRID], "HKG", "DXB")
        assert outbound == []
        assert len(returns) == 1


class TestSelectOutbound:
    def test_dedupe_keeps_cheapest_per_airline_bucket(self):
        labels = [
            "9:35 AM HKG to DXB, 3h 45m, Cathay Pacific. $6,542 round trip.",
            "9:40 AM HKG to DXB, 3h 50m, Cathay Pacific. $6,100 round trip.",
            "11:55 AM HKG to DXB, 3h 50m, Cathay Pacific. $6,300 round trip.",
            "8:10 AM HKG to DXB, 8h 25m, Emirates. $5,890 round trip.",
            "2:30 PM HKG to DXB, 8h 20m, Emirates. $6,400 round trip.",
        ]
        options = [parsing.parse_label(l) for l in labels]
        picked = select_outbound_options([o for o in options if o], max_cells=10)
        keys = {(o.legs[0].airline, o.legs[0].depart_time) for o in picked}
        # Cathay 09-12 bucket: cheapest is 9:40 ($6100); Emirates 06-09: 8:10 AM;
        # Emirates 12-15: 2:30 PM -- three distinct (airline, bucket) cells.
        assert ("Cathay Pacific", "09:40") in keys
        assert ("Cathay Pacific", "09:35") not in keys
        assert ("Emirates", "08:10") in keys
        assert ("Emirates", "14:30") in keys
        assert len(picked) == 3

    def test_cap_respected(self):
        labels = [
            f"{h}:00 AM HKG to DXB, 3h 45m, Airline{i}. ${5000 + i} round trip."
            for i, h in enumerate(["6", "7", "8", "9", "10"])
        ]
        options = [o for o in (parsing.parse_label(l) for l in labels) if o]
        picked = select_outbound_options(options, max_cells=3)
        assert len(picked) == 3
        # cheapest three kept, ascending
        assert [o.price for o in picked] == sorted(o.price for o in picked)


class TestCellAggregation:
    SCOPE = TravelScope(
        name="HKG-DXB-Test", from_airport="HKG", to_airport="DXB",
        depart_from=date(2026, 12, 18), depart_to=date(2026, 12, 20),
        return_from=date(2026, 12, 22), return_to=date(2026, 12, 28),
    )

    def test_min_price_per_return_bucket(self):
        outbound = parsing.parse_label(HKG_DXB_OUTBOUND)
        returns = [
            parsing.parse_label(
                "Tue, Dec 22. 3:05 PM DXB to HKG, 3h 30m, Cathay Pacific. $6,542 round trip."),
            parsing.parse_label(
                "Tue, Dec 22. 5:15 PM DXB to HKG, 3h 35m, Cathay Pacific. $6,200 round trip."),
            parsing.parse_label(
                "Tue, Dec 22. 2:45 AM DXB to HKG, 7h 50m, Emirates. $5,800 round trip."),
        ]
        cells, its = cells_and_itineraries(
            outbound, [r for r in returns if r],
            scope=self.SCOPE, run_date="2026-12-01",
            depart=self.SCOPE.date_pairs()[0][0], returnd=self.SCOPE.date_pairs()[0][1],
        )
        # 15-18 bucket: min of 6542 / 6200 = 6200; 00-03 bucket: 5800
        by_bucket = {c["ret_bucket"]: c["min_price"] for c in cells}
        assert by_bucket["15-18"] == 6200.0
        assert by_bucket["00-03"] == 5800.0
        assert all(c["airline"] == "Cathay Pacific" for c in cells)
        assert len(its) == 3
        assert its[0]["out_airline"] == "Cathay Pacific"
        assert its[0]["ret_depart"].startswith("2026-12-22T")

    def test_no_price_returns_are_skipped(self):
        outbound = parsing.parse_label(HKG_DXB_OUTBOUND)
        returns = [parsing.parse_label("Tue, Dec 22. 3:05 PM DXB to HKG, 3h 30m, Cathay Pacific.")]
        cells, its = cells_and_itineraries(
            outbound, [r for r in returns if r],
            scope=self.SCOPE, run_date="2026-12-01",
            depart=self.SCOPE.date_pairs()[0][0], returnd=self.SCOPE.date_pairs()[0][1],
        )
        assert cells == []
        assert its == []


class TestTfsUrl:
    SCOPE = TravelScope(
        name="HKG-DXB-Test", from_airport="HKG", to_airport="DXB",
        depart_from="2026-12-18", depart_to="2026-12-20",
        return_from="2026-12-22", return_to="2026-12-28",
        max_stops=0, seat="economy", currency="HKD",
    )

    def test_url_encodes_a_round_trip_with_two_legs(self):
        url = build_search_url(self.SCOPE, date(2026, 12, 18), date(2026, 12, 22))
        assert url.startswith("https://www.google.com/travel/flights/search?tfs=")
        assert "&hl=en" in url and "&curr=HKD" in url

        tfs = url.split("tfs=")[1].split("&")[0]
        from fast_flights.pb import flights_pb2 as pb
        info = decode_tfs(tfs)
        assert info.trip == pb.ROUND_TRIP
        assert len(info.data) == 2
        outbound, return_leg = info.data
        assert outbound.from_airport.airport == "HKG"
        assert outbound.to_airport.airport == "DXB"
        assert outbound.date == "2026-12-18"
        assert return_leg.from_airport.airport == "DXB"
        assert return_leg.to_airport.airport == "HKG"
        assert return_leg.date == "2026-12-22"
        assert outbound.max_stops == 0

    def test_bucket_boundaries(self):
        assert bucket_from_hour(0) == "00-03"
        assert bucket_from_hour(2) == "00-03"
        assert bucket_from_hour(3) == "03-06"
        assert bucket_from_hour(9) == "09-12"
        assert bucket_from_hour(21) == "21-24"
        assert bucket_from_hour(23) == "21-24"
