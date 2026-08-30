"""The decoded payload, turned into transactions.

Everything this module drops, it counts. A row that is not a home, a row with a
postType nobody has seen before, a row with no price -- each lands in
``skipped``, which reaches the agent through ``check``. Silent filtering is how
a tracker ends up confidently reporting nothing.

The one field that carries the whole 買賣/租賃 distinction is ``postType``:
``S`` and ``R``, on the row itself, in a list that interleaves both. There is no
separate rental page and no filter to set -- the site's own 買賣/租賃 control
just hides rows client-side, so the served payload always contains both and the
split is made here.
"""

from __future__ import annotations

import dataclasses

from .errors import ParseError
from .models import POST_TYPE_TO_DEAL, RESIDENTIAL_THEME, Transaction, parse_date


@dataclasses.dataclass(frozen=True)
class Extraction:
    """What one page yielded, and what it did not."""

    records: tuple[Transaction, ...]
    published_count: int          # Centanet's own total for the search, not len(records)
    search: dict                  # the search that produced it: day window, size, sort
    skipped: dict                 # reason -> how many
    warnings: tuple[str, ...]     # things worth an operator's eye

    def to_dict(self) -> dict:
        return {
            "parsed": len(self.records),
            "published_count": self.published_count,
            "search": self.search,
            "skipped": {key: value for key, value in self.skipped.items() if value},
            "warnings": list(self.warnings),
        }


def _number(value) -> float | None:
    """A positive measurement, or nothing.

    Zero is not a small area and not a cheap square foot -- Centanet uses it and
    ``null`` interchangeably for "not published", and a zero reaching a median
    would drag it toward the floor.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _dig(payload: dict, *path: str):
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ParseError(
                "the transaction payload is not where it used to be",
                missing=key, path=".".join(path),
            )
        node = node[key]
    return node


def extract(payload: dict, estate: str) -> Extraction:
    """Transactions for one configured estate entry, newest first."""
    listing = _dig(payload, "state", "transaction", "transactionList")
    if not isinstance(listing, dict) or "data" not in listing:
        # This is exactly what an over-large `size` produces, and it is
        # indistinguishable from a quiet estate unless it is raised. See
        # references/data-source.md.
        raise ParseError(
            "the page carried an empty transactionList -- the request was accepted "
            "but returned no list at all",
            estate=estate,
            keys=sorted(listing) if isinstance(listing, dict) else type(listing).__name__,
        )

    rows = listing.get("data") or []
    if not isinstance(rows, list):
        raise ParseError("transactionList.data is not a list", estate=estate)

    search = _dig(payload, "state", "transaction", "transactionSearch")
    skipped: dict = {
        "non_residential": 0, "unknown_post_type": 0,
        "no_price": 0, "no_date": 0, "no_id": 0,
    }
    unknown_types: set[str] = set()
    records: list[Transaction] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        if _text(row.get("transTheme")) != RESIDENTIAL_THEME:
            skipped["non_residential"] += 1
            continue

        post_type = _text(row.get("postType"))
        deal_type = POST_TYPE_TO_DEAL.get(post_type)
        if deal_type is None:
            skipped["unknown_post_type"] += 1
            unknown_types.add(post_type or "<blank>")
            continue

        tx_id = _text(row.get("id"))
        if not tx_id:
            skipped["no_id"] += 1
            continue

        price = _number(row.get("transactionPrice"))
        if price is None:
            # Confidential and withheld prices do occur; there is nothing to
            # report about them and nothing to average them into.
            skipped["no_price"] += 1
            continue

        # 成交日期 is on every row on both sides. 登記日期 is a land-registry
        # fact and is null on every rental, so it can order sales but not a
        # mixed list -- which is why insDate is the date this package uses.
        ins_date = parse_date(row.get("insDate")) or parse_date(row.get("regDate"))
        if ins_date is None:
            skipped["no_date"] += 1
            continue

        bedrooms = row.get("bedroomCount")
        display = row.get("displayText") or {}
        address = (display.get("addr") or {}) if isinstance(display, dict) else {}

        records.append(Transaction(
            estate=estate,
            tx_id=tx_id,
            deal_type=deal_type,
            price=price,
            ins_date=ins_date,
            estate_name=_text(row.get("estateName")),
            building=_text(row.get("buildingName")),
            floor=_text(row.get("yAxis")),
            unit=_text(row.get("xAxis")),
            address_line=_text(row.get("address")) or _text(address.get("line5")),
            bedrooms=int(bedrooms) if isinstance(bedrooms, (int, float)) else None,
            saleable_area=_number(row.get("nArea")),
            saleable_unit_price=_number(row.get("nUnitPrice")),
            gross_area=_number(row.get("gArea")),
            gross_unit_price=_number(row.get("gUnitPrice")),
            reg_date=parse_date(row.get("regDate")),
            data_source=_text(row.get("dataSource")),
            first_or_second_hand=_text(row.get("firstOrSecondHand")),
            detail_url=_text(row.get("detailUrl")),
        ))

    warnings: list[str] = []
    if unknown_types:
        warnings.append(
            "unrecognised postType " + ", ".join(sorted(unknown_types))
            + " -- neither 買賣 nor 租賃, and dropped"
        )

    published = listing.get("count")
    return Extraction(
        records=tuple(records),
        published_count=int(published) if isinstance(published, (int, float)) else len(records),
        search={
            key: search.get(key)
            for key in ("day", "size", "offset", "sort", "order", "postType")
            if isinstance(search, dict)
        },
        skipped=skipped,
        warnings=tuple(warnings),
    )
