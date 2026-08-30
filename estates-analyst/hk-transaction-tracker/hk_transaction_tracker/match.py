"""Whether a transaction is one the operator asked to hear about.

The rule is ANDed, and it has one wrinkle that has to be stated because it
decides what happens to about a quarter of all sale rows.

**A dimension the source did not publish cannot reject a transaction.** Roughly
a quarter of sale rows come from the Land Registry with a price, an address and
no 面積(實) at all, because Centanet has not matched the unit to its own
records. Rejecting those for failing a size band would quietly drop real sales
in the tracked block; accepting them into a size band would be inventing a
number. So a missing dimension is neither: it is skipped, the other dimension
still has to pass, and the transaction is flagged ``area_missing`` and reported
in its own 面積待補 group -- never averaged, never charted, never given a 呎價.

The floor under that is ``satisfied``: at least one configured dimension must
actually have been checked and passed. A row with neither 間隔 nor 面積(實)
matches nothing, because there is nothing about it to match on.
"""

from __future__ import annotations

import dataclasses

from .config.estates import EstateEntry, SizeRange
from .models import BEDROOM_CAP, Transaction, bedroom_label


@dataclasses.dataclass(frozen=True)
class MatchResult:
    """Why a transaction is, or is not, worth reporting."""

    matched: bool
    reason: str
    size_range: SizeRange | None = None
    area_missing: bool = False
    tracked_side: bool = True

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "reason": self.reason,
            "size_range": self.size_range.label if self.size_range else None,
            "area_missing": self.area_missing,
        }


def bedrooms_match(count: int | None, wanted: tuple[int, ...]) -> bool:
    """``4`` in the config means 4房或以上, exactly as Centanet's filter reads it."""
    if count is None:
        return False
    if count in wanted:
        return True
    return BEDROOM_CAP in wanted and count >= BEDROOM_CAP


def find_range(area: float, bands: tuple[SizeRange, ...]) -> SizeRange | None:
    """The first configured band containing ``area``.

    First rather than best: overlapping bands are the operator's own doing, and
    reporting one transaction under two headings would double-count it in the
    summary.
    """
    return next((band for band in bands if band.contains(area)), None)


def judge(transaction: Transaction, entry: EstateEntry) -> MatchResult:
    """Whether ``transaction`` meets ``entry``'s criteria."""
    if transaction.deal_type not in entry.track:
        return MatchResult(
            matched=False,
            reason=f"{transaction.deal_type} 不在此屋苑的追蹤範圍",
            tracked_side=False,
        )

    wants_bedrooms = bool(entry.bedrooms)
    wants_size = bool(entry.size_ranges)

    if not wants_bedrooms and not wants_size:
        return MatchResult(
            matched=True,
            reason="此屋苑沒有設定間隔或面積條件，全部成交都會報告",
            area_missing=transaction.area_missing,
        )

    satisfied: list[str] = []
    unknown: list[str] = []
    band: SizeRange | None = None

    if wants_bedrooms:
        if transaction.bedrooms is None:
            unknown.append("間隔")
        elif bedrooms_match(transaction.bedrooms, entry.bedrooms):
            satisfied.append(f"間隔 {bedroom_label(transaction.bedrooms)}")
        else:
            wanted = "、".join(bedroom_label(count) for count in entry.bedrooms)
            return MatchResult(
                matched=False,
                reason=f"間隔 {bedroom_label(transaction.bedrooms)} 不在追蹤範圍（{wanted}）",
            )

    if wants_size:
        if transaction.saleable_area is None:
            unknown.append("面積(實)")
        else:
            band = find_range(transaction.saleable_area, entry.size_ranges)
            if band is None:
                wanted = "、".join(item.label for item in entry.size_ranges)
                return MatchResult(
                    matched=False,
                    reason=f"面積(實) {transaction.saleable_area:,.0f}呎 不在追蹤範圍（{wanted}）",
                )
            satisfied.append(f"面積(實) {transaction.saleable_area:,.0f}呎 屬 {band.label}")

    if not satisfied:
        return MatchResult(
            matched=False,
            reason="來源沒有公布" + "、".join(unknown) + "，無法判斷是否符合條件",
        )

    reason = "；".join(satisfied)
    if unknown:
        reason += f"（來源未公布{'、'.join(unknown)}）"
    return MatchResult(
        matched=True,
        reason=reason,
        size_range=band,
        # Flagged on the fact, not on the criteria: a transaction with no
        # 面積(實) has no 呎價(實) either, whether or not a band was configured.
        area_missing=transaction.area_missing,
    )
