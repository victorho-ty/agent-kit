"""Pure parser for Google Flights option aria-labels.

Deterministic core of the crawler, pinned against the real label set captured
in ``tests/fixtures/gf_labels_2026.json``. Two schemas are supported:

**Schema v2 (2025+, what Google serves today)** -- one clause per leg::

    "From 2514 Hong Kong dollars round trip total. Nonstop flight with
     Hong Kong Express. Leaves Hong Kong International Airport at 11:55 AM
     on Friday, December 18 and arrives at Penang International Airport at
     3:40 PM on Friday, December 18. Total duration 3 hr 45 min.
     Select flight"

  - price: ``From <amount> <currency words> round trip total``
  - stops/airline: ``(Nonstop|<n> stop) flight with <carrier>``
    (a connecting option says "Cathay Pacific and Emirates" -- the first
    carrier is the outbound airline)
  - leg: ``Leaves <airport> at <time> on <weekday>, <Month> <day> and
    arrives at <airport> at <time> on <weekday>, <Month> <day>``
  - duration: ``Total duration <n> hr <m> min`` (authoritative when present)
  - airports are given by NAME; the caller supplies a city-name -> IATA map
    learned from the page's "Where from? Hong Kong HKG" labels.

**Schema v1 (legacy)** -- ``9:35 AM HKG to DXB, 3h 45m, Cathay Pacific``
phrases with ``$6,542 round trip`` prices. Kept as a fallback.

Known Google quirks handled: narrow no-break space U+202F before AM/PM;
red-eyes crossing midnight (arrival date stated, so duration stays correct);
"Flight details." / "Leaves ..." duplicates of the same flight without a
price (kept, priced at +inf so they never win dedupe).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Schema v2 regexes (current Google Flights)
# ---------------------------------------------------------------------------

_AIRPORT = r"[A-Za-z .'\-]+?(?:\s+International\s+Airport|\s+Airport)?"

_LEG_V2_RE = re.compile(
    r"Leaves\s+(?P<dep_city>[A-Za-z .'\-]+?)(?:\s+International\s+Airport|\s+Airport)?\s+at\s+"
    r"(?P<dep_time>\d{1,2}:\d{2})\s*(?P<dep_meridiem>AM|PM)\s+on\s+"
    r"(?P<dep_wday>[A-Za-z]+),\s+(?P<dep_month>[A-Za-z]+)\s+(?P<dep_day>\d+)"
    r".*?arrives at\s+"
    r"(?P<arr_city>[A-Za-z .'\-]+?)(?:\s+International\s+Airport|\s+Airport)?\s+at\s+"
    r"(?P<arr_time>\d{1,2}:\d{2})\s*(?P<arr_meridiem>AM|PM)\s+on\s+"
    r"(?P<arr_wday>[A-Za-z]+),\s+(?P<arr_month>[A-Za-z]+)\s+(?P<arr_day>\d+)",
    re.IGNORECASE,
)

_AIRLINE_V2_RE = re.compile(r"flight with\s+([^.]*?)\.", re.IGNORECASE)
_STOPS_V2_RE = re.compile(r"(nonstop|(\d+)\s+stop)\s+flight", re.IGNORECASE)
_DURATION_V2_RE = re.compile(r"Total duration\s+(\d+)\s*hr(?:s?)?\s*(?:(\d+)\s*min)?", re.IGNORECASE)
_PRICE_V2_RE = re.compile(
    r"From\s+(?P<price>[\d,]+)\s+(?P<cur>[A-Za-z ]+?)\s+round trip total", re.IGNORECASE
)

_SEARCHBOX_RE = re.compile(r"Where (?:from|to)\??\s*(?P<city>.+?)\s+(?P<code>[A-Z]{3})$")

_CURRENCY_WORDS = {
    "hong kong dollars": "HKD", "us dollars": "USD", "u.s. dollars": "USD",
    "singapore dollars": "SGD", "malaysian ringgit": "MYR", "new taiwan dollars": "TWD",
    "japanese yen": "JPY", "euros": "EUR", "euro": "EUR", "british pounds": "GBP",
    "australian dollars": "AUD", "canadian dollars": "CAD", "thai baht": "THB",
    "indonesian rupiah": "IDR", "philippine pesos": "PHP", "south korean won": "KRW",
    "chinese yuan renminbi": "CNY", "new zealand dollars": "NZD", "indian rupees": "INR",
}

# City-name -> IATA fallback for direction detection when the page does not
# state the pair. The page-learned map always wins.
CITY_CODES = {
    "hong kong": "HKG", "dubai": "DXB", "penang": "PEN", "singapore": "SIN",
    "kuala lumpur": "KUL", "bangkok": "BKK", "taipei": "TPE", "tokyo": "TYO",
    "osaka": "KIX", "seoul": "ICN", "london": "LON", "paris": "PAR",
    "manila": "MNL", "sydney": "SYD", "melbourne": "MEL", "bali": "DPS",
    "phuket": "HKT", "ho chi minh city": "SGN", "hanoi": "HAN",
    "jakarta": "CGK", "shanghai": "SHA", "beijing": "PEK", "new york": "NYC",
    "san francisco": "SFO", "los angeles": "LAX", "doha": "DOH",
    "abu dhabi": "AUH", "mumbai": "BOM", "delhi": "DEL", "frankfurt": "FRA",
    "amsterdam": "AMS", "zurich": "ZRH", "istanbul": "IST", "cairo": "CAI",
}

_MONTHS = {name: i for i, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}

# ---------------------------------------------------------------------------
# Schema v1 regexes (legacy)
# ---------------------------------------------------------------------------

_LEG_V1_RE = re.compile(
    r"(?P<time>\d{1,2}:\d{2})\s*(?:AM|PM)\s+"
    r"(?P<dep>[A-Z]{3})\s+to\s+(?P<arr>[A-Z]{3})",
    re.IGNORECASE,
)
_DURATION_V1_RE = re.compile(r"(\d{1,2})\s*h(?:r)?\s*(?:(\d{1,2})\s*m(?:in)?)?")
_STOPS_V1_RE = re.compile(r"non-?stop|(\d)\s*stop", re.IGNORECASE)
_FLIGHT_NO_RE = re.compile(r"\b([A-Z]{2})\s*(\d{3,4})\b")

# v1 price patterns, tried in order. Price is the named group "price".
_PRICE_V1_PATTERNS = [
    re.compile(r"(?P<cur>[A-Z]{2,3})?\s*\$?(?P<price>[\d,]+(?:\.\d{2})?)\s*(?:round\s+trip|total|for\s+all)"),
    re.compile(r"Price:\s*(?P<cur>[A-Z]{2,3})?\s*\$?(?P<price>[\d,]+(?:\.\d{2})?)"),
    re.compile(r"\$(?P<price>[\d,]+(?:\.\d{2})?)"),
    re.compile(r"(?P<price>[\d,]{4,})\s*(?:HK\s*dollars|dollars|HKD|for\s+all)"),
]

_NON_PRICE_NOISE = re.compile(
    r"(flights? from .*?(?=\.\s*\d|$)|round trip.*$|total for.*$|"
    r"departs? .*$|arrives? .*$|price:.*$|select .*$|opens? .*$)",
    re.IGNORECASE,
)
_PRICE_NOISE_RE = re.compile(
    r"Price:\s*(?:[A-Z]{2,3}\s*)?\$?[\d,]+(?:\.\d{2})?|"
    r"(?:[A-Z]{2,3}\s*)?\$[\d,]+(?:\.\d{2})?|"
    r"[\d,]{4,}\s*(?:HK\s*dollars|dollars|HKD)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Leg:
    """One leg: route (IATA codes), local departure/arrival, duration, carrier."""
    depart_time: str          # "09:35", 24h clock
    depart_airport: str       # IATA code (e.g. "HKG"); raw city name if unknown
    arrive_time: str | None   # "15:20", 24h clock; None when label lacks it
    arrive_airport: str       # IATA code
    duration_min: int
    stops: int
    airline: str
    flight_no: str | None
    arrive_day_shift: int = 0  # 1 when arrival is on the day AFTER departure (red-eye)

    @property
    def depart_hour(self) -> int:
        return int(self.depart_time.split(":")[0])


@dataclass(frozen=True)
class OptionLabel:
    """A parsed aria-label: the legs it describes plus the round-trip price."""
    legs: tuple[Leg, ...]
    price: float | None
    currency: str | None
    round_trip: bool
    raw: str | None = None   # the (normalised) label text, for re-locating the row

    def leg(self, depart_airport: str) -> Leg | None:
        """The leg departing from ``depart_airport`` (outbound vs return pick)."""
        for leg in self.legs:
            if leg.depart_airport == depart_airport:
                return leg
        return None


def learn_airport_codes(labels: list[str]) -> dict[str, str]:
    """City-name -> IATA map from the page's 'Where from? Hong Kong HKG' labels."""
    codes: dict[str, str] = {}
    for label in labels:
        match = _SEARCHBOX_RE.search(label)
        if match:
            codes[match.group("city").strip().lower()] = match.group("code").upper()
    return codes


def _city_to_code(city: str, airport_codes: dict[str, str] | None) -> str:
    city = city.strip()
    if airport_codes and city.lower() in airport_codes:
        return airport_codes[city.lower()]
    return CITY_CODES.get(city.lower(), city)


def _to_24h(time_str: str) -> str:
    """'9:35 AM' -> '09:35'"""
    match = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_str, re.IGNORECASE)
    if not match:
        return time_str
    hour, minute, meridiem = match.groups()
    hour = int(hour)
    if meridiem.upper() == "PM" and hour != 12:
        hour += 12
    elif meridiem.upper() == "AM" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _to_minutes(hhmm: str) -> int:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return hour * 60 + minute


def _duration_from_times(dep_time: str, dep_meridiem: str, dep_month: str, dep_day: str,
                         arr_time: str, arr_meridiem: str, arr_month: str, arr_day: str) -> int:
    dep_min = _to_minutes(_to_24h(f"{dep_time} {dep_meridiem}"))
    arr_min = _to_minutes(_to_24h(f"{arr_time} {arr_meridiem}"))
    delta_days = _day_shift(dep_month, dep_day, arr_month, arr_day)
    return (arr_min - dep_min) + delta_days * 1440


def _day_shift(dep_month: str, dep_day: str, arr_month: str, arr_day: str) -> int:
    """0 when arrival is same day, 1 when it is the next day (or later)."""
    dep_ord = _MONTHS.get(dep_month.lower(), 0) * 31 + int(dep_day)
    arr_ord = _MONTHS.get(arr_month.lower(), 0) * 31 + int(arr_day)
    delta = arr_ord - dep_ord
    if delta < 0:              # year wrap (Dec -> Jan) or malformed label
        return 1
    return delta


def _parse_v2_leg(label: str, airport_codes: dict[str, str] | None) -> tuple[list[Leg], list[str]]:
    """Parse schema-v2 legs. Returns (legs, city-name matches consumed)."""
    duration_match = _DURATION_V2_RE.search(label)
    stated_duration = None
    if duration_match:
        stated_duration = int(duration_match.group(1)) * 60 + int(duration_match.group(2) or 0)

    legs: list[Leg] = []
    for match in _LEG_V2_RE.finditer(label):
        airline = "Unknown"
        stops = 0
        airline_match = _AIRLINE_V2_RE.search(label)
        if airline_match:
            airline = airline_match.group(1).strip()
            airline = airline.split(" and ")[0].strip()   # connecting carrier second
        stops_match = _STOPS_V2_RE.search(label)
        if stops_match and stops_match.group(2):
            stops = int(stops_match.group(2))

        duration = stated_duration if stated_duration is not None else _duration_from_times(
            match.group("dep_time"), match.group("dep_meridiem"), match.group("dep_month"),
            match.group("dep_day"), match.group("arr_time"), match.group("arr_meridiem"),
            match.group("arr_month"), match.group("arr_day"),
        )
        day_shift = _day_shift(match.group("dep_month"), match.group("dep_day"),
                               match.group("arr_month"), match.group("arr_day"))
        legs.append(Leg(
            depart_time=_to_24h(f"{match.group('dep_time')} {match.group('dep_meridiem')}"),
            depart_airport=_city_to_code(match.group("dep_city"), airport_codes),
            arrive_time=_to_24h(f"{match.group('arr_time')} {match.group('arr_meridiem')}"),
            arrive_airport=_city_to_code(match.group("arr_city"), airport_codes),
            duration_min=duration,
            stops=stops,
            airline=airline,
            flight_no=None,
            arrive_day_shift=day_shift,
        ))
    return legs


def _parse_v2_price(label: str) -> tuple[float | None, str | None]:
    match = _PRICE_V2_RE.search(label)
    if not match:
        return None, None
    price = float(match.group("price").replace(",", ""))
    currency = _CURRENCY_WORDS.get(match.group("cur").strip().lower())
    return price, currency


def _parse_v1_leg(label: str) -> list[Leg]:
    """Legacy schema: '9:35 AM HKG to DXB, 3h 45m, Cathay Pacific.'"""
    legs: list[Leg] = []
    for match in _LEG_V1_RE.finditer(label):
        time_str = match.group("time").strip()
        if not re.search(r"(AM|PM)", match.group(0), re.IGNORECASE):
            continue
        start = match.end()
        nxt = _LEG_V1_RE.search(label, start)
        segment = label[start: nxt.start()] if nxt else label[start:]

        duration = None
        dur_match = _DURATION_V1_RE.search(segment)
        if dur_match:
            duration = int(dur_match.group(1) or 0) * 60 + int(dur_match.group(2) or 0)
        stops = 0
        stop_match = _STOPS_V1_RE.search(segment)
        if stop_match and stop_match.group(1):
            stops = int(stop_match.group(1))

        flight = _FLIGHT_NO_RE.search(segment)
        flight_no = f"{flight.group(1)}{flight.group(2)}" if flight else None
        clean = _DURATION_V1_RE.sub(" ", segment)
        clean = _FLIGHT_NO_RE.sub(" ", clean)
        clean = _STOPS_V1_RE.sub(" ", clean)
        clean = _PRICE_NOISE_RE.sub(" ", clean)
        airline = _NON_PRICE_NOISE.sub(" ", clean).strip(" .,;:-")
        if not airline and flight_no:
            airline = flight_no[:2]
        if not airline:
            airline = "Unknown"

        legs.append(Leg(
            depart_time=_to_24h(match.group(0)),
            depart_airport=match.group("dep").upper(),
            arrive_time=None,
            arrive_airport=match.group("arr").upper(),
            duration_min=duration or 0,
            stops=stops,
            airline=airline,
            flight_no=flight_no,
        ))
    return legs


def parse_label(aria_label: str, airport_codes: dict[str, str] | None = None) -> OptionLabel | None:
    """Parse one aria-label into an :class:`OptionLabel`.

    Returns ``None`` for headers, buttons and empty cells (labels without a
    leg clause) -- the crawler must skip those.
    """
    if not aria_label:
        return None
    label = aria_label.replace("\u202f", " ").replace("\u2009", " ")

    legs = _parse_v2_leg(label, airport_codes)
    schema = "v2"
    if not legs:
        legs = _parse_v1_leg(label)
        schema = "v1"
    if not legs:
        return None

    if schema == "v2":
        price, currency = _parse_v2_price(label)
    else:
        price, currency = None, None
        for pattern in _PRICE_V1_PATTERNS:
            match = pattern.search(label)
            if match:
                price = float(match.group("price").replace(",", ""))
                currency = match.group("cur")
                break

    round_trip = bool(re.search(r"round\s+trip", label, re.IGNORECASE)) or len(legs) >= 2
    return OptionLabel(legs=tuple(legs), price=price, currency=currency,
                       round_trip=round_trip, raw=label)


def extract_outbound_options(labels: list[str], origin: str, dest: str,
                             airport_codes: dict[str, str] | None = None) -> list[OptionLabel]:
    """Options with a leg ``origin -> dest`` (the outbound direction)."""
    return _extract(labels, origin, dest, airport_codes)


def extract_return_options(labels: list[str], origin: str, dest: str,
                           airport_codes: dict[str, str] | None = None) -> list[OptionLabel]:
    """Options with a leg ``dest -> origin`` (the return direction)."""
    return _extract(labels, dest, origin, airport_codes)


def _extract(labels: list[str], depart: str, arrive: str,
             airport_codes: dict[str, str] | None) -> list[OptionLabel]:
    out: list[OptionLabel] = []
    for label in labels:
        parsed = parse_label(label, airport_codes)
        if parsed is None:
            continue
        for leg in parsed.legs:
            if leg.depart_airport == depart and leg.arrive_airport == arrive:
                out.append(parsed)
                break
    return out
