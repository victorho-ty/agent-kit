"""The one record type, and the vocabulary the rest of the package agrees on.

A :class:`Transaction` is one row of Centanet's 成交 list, already sorted into
買賣 or 租賃 and already stripped of the fields nothing here reads. It is the
same shape whether it came off the network or out of the database, which is what
lets the matcher, the trend and the renderer be tested without either.

**買賣 and 租賃 are not two flavours of one number.** For a sale, ``price`` is
成交價 and ``saleable_unit_price`` is 呎價(實), in dollars per square foot. For a
rental they are the monthly rent and 呎租, and a 呎租 of 57 sits beside a 呎價 of
24,458 in the same column of the same source. Nothing may average, compare or
chart across the two, and every query in this package is scoped by
``deal_type`` for that reason.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

DEAL_TYPES = ("sale", "rental")

# Centanet's own postType, which is the only thing distinguishing a sale row
# from a rental row in a list that interleaves both.
POST_TYPE_TO_DEAL = {"S": "sale", "R": "rental"}

DEAL_LABELS = {"sale": "買賣", "rental": "租賃"}

# What the price column means on each side. The renderer and the summary lines
# both read these rather than hard-coding "成交價", which would be a lie on a
# rental row.
PRICE_LABELS = {"sale": "成交價", "rental": "月租"}
UNIT_PRICE_LABELS = {"sale": "呎價(實)", "rental": "呎租(實)"}

# Centanet's transTheme. Anything else -- CarPark today -- is not a home and is
# dropped at extraction, so it can never reach a bedroom bucket or a 呎價 median.
RESIDENTIAL_THEME = "Post"

# 4 means "4房或以上" in Centanet's own filter, so a configured 4 matches 4, 5
# and 6 alike. Nothing above 4 is ever labelled separately.
BEDROOM_CAP = 4


def bedroom_label(count: int | None) -> str:
    """The 間隔 as Centanet's filter words it."""
    if count is None:
        return "間隔未列"
    if count <= 0:
        return "開放式"
    if count >= BEDROOM_CAP:
        return f"{BEDROOM_CAP}房或以上"
    return f"{count}房"


def parse_date(value: str | None) -> date | None:
    """An ISO timestamp from the payload, or a plain date from the database."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


@dataclasses.dataclass(frozen=True)
class Transaction:
    """One 成交 record, scoped to the configured estate entry that found it."""

    estate: str                     # the config entry's name, not Centanet's
    tx_id: str                      # Centanet's own id; unique within an estate
    deal_type: str                  # sale | rental
    price: float                    # 成交價, or the monthly rent
    ins_date: date                  # 成交日期 -- present on every row, both sides
    estate_name: str = ""           # as Centanet publishes it
    building: str = ""              # 座
    floor: str = ""                 # 樓層
    unit: str = ""                  # 室
    address_line: str = ""          # the one-line address Centanet displays
    bedrooms: int | None = None     # 間隔; None when unpublished
    saleable_area: float | None = None        # 面積(實); None when unpublished
    saleable_unit_price: float | None = None  # 呎價(實) / 呎租(實)
    gross_area: float | None = None           # 面積(建); recorded, never averaged
    gross_unit_price: float | None = None
    reg_date: date | None = None    # 登記日期; sales only, rentals are never registered
    data_source: str = ""           # Land (土地註冊處) | AC (中原集團)
    first_or_second_hand: str = ""
    detail_url: str = ""

    @property
    def unit_label(self) -> str:
        """"2座 57樓 A室", skipping whatever the source left blank."""
        parts = [part for part in (self.building, self.floor, self.unit) if part]
        return " ".join(parts) or "—"

    @property
    def area_missing(self) -> bool:
        """No 面積(實), so no 呎價(實) and no size bucket.

        Common on land-registry sale rows: the registry publishes a price and an
        address, and the saleable area only arrives if Centanet can match the
        unit to its own records. Roughly a quarter of sale rows on a mature
        estate. These are reported in their own 面積待補 group and are excluded
        from every median, percentage and chart.
        """
        return self.saleable_area is None or self.saleable_unit_price is None

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["ins_date"] = self.ins_date.isoformat()
        data["reg_date"] = self.reg_date.isoformat() if self.reg_date else None
        data["unit_label"] = self.unit_label
        data["bedroom_label"] = bedroom_label(self.bedrooms)
        data["deal_label"] = DEAL_LABELS[self.deal_type]
        data["area_missing"] = self.area_missing
        return data

    @classmethod
    def from_row(cls, row) -> "Transaction":
        """Rebuild from a ``transaction_row`` record."""
        return cls(
            estate=row["estate"],
            tx_id=row["tx_id"],
            deal_type=row["deal_type"],
            price=row["price"],
            ins_date=parse_date(row["ins_date"]),
            estate_name=row["estate_name"] or "",
            building=row["building"] or "",
            floor=row["floor"] or "",
            unit=row["unit"] or "",
            address_line=row["address_line"] or "",
            bedrooms=row["bedrooms"],
            saleable_area=row["saleable_area"],
            saleable_unit_price=row["saleable_unit_price"],
            gross_area=row["gross_area"],
            gross_unit_price=row["gross_unit_price"],
            reg_date=parse_date(row["reg_date"]),
            data_source=row["data_source"] or "",
            first_or_second_hand=row["first_or_second_hand"] or "",
            detail_url=row["detail_url"] or "",
        )
