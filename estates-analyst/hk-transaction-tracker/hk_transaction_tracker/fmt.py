"""Money and labels, formatted once so nobody downstream has to.

Every string the agent relays is built here, and the agent relays it verbatim.
That is the whole token argument for this module: a run with nine new
transactions across three estates costs nine finished lines, not nine
paragraphs of a model deciding again how to write a price.

Hong Kong writes property prices in 萬 and 億, not in millions: $12,400,000 is
$1,240萬 on every listing board in the city, and a summary that says "HK$12.4M"
reads as a translation. Rents are written in full. Both conventions are
followed here rather than in three separate call sites.
"""

from __future__ import annotations

from .models import DEAL_LABELS, PRICE_LABELS, UNIT_PRICE_LABELS

EM_DASH = "—"


def price(value: float | None, deal_type: str) -> str:
    """成交價 in 萬/億, or a monthly rent in full dollars."""
    if value is None:
        return EM_DASH
    if deal_type == "rental":
        return f"${value:,.0f}/月"
    if value >= 100_000_000:
        return f"${value / 100_000_000:,.2f}億"
    if value >= 10_000:
        man = value / 10_000
        # 1,240萬 rather than 1,240.0萬; a half-萬 is $5,000 and does show up.
        return f"${man:,.0f}萬" if abs(man - round(man)) < 0.005 else f"${man:,.1f}萬"
    return f"${value:,.0f}"


def unit_price(value: float | None, deal_type: str) -> str:
    """呎價(實) or 呎租(實). Rents are two orders of magnitude smaller; both are per foot."""
    if value is None:
        return EM_DASH
    if deal_type == "rental":
        return f"${value:,.1f}/呎".replace(".0/", "/")
    return f"${value:,.0f}/呎"


def area(value: float | None) -> str:
    if value is None:
        return EM_DASH
    return f"{value:,.0f}呎"


def pct(value: float | None) -> str:
    if value is None:
        return EM_DASH
    return f"{value:+.1f}%"


def deal_label(deal_type: str) -> str:
    return DEAL_LABELS.get(deal_type, deal_type)


def price_label(deal_type: str) -> str:
    return PRICE_LABELS.get(deal_type, "價格")


def unit_price_label(deal_type: str) -> str:
    return UNIT_PRICE_LABELS.get(deal_type, "呎價")


def size_range_label(low: float | None, high: float | None) -> str:
    """How one configured size band is written wherever it is shown."""
    if low is None and high is None:
        return "任何面積"
    if low is None:
        return f"{high:,.0f}呎以下"
    if high is None:
        return f"{low:,.0f}呎以上"
    return f"{low:,.0f}-{high:,.0f}呎"


def direction_word(direction: str) -> str:
    return {"up": "升", "down": "跌", "flat": "持平"}.get(direction, EM_DASH)
