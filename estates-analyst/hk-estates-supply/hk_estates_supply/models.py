"""The three things this package passes around.

All figures are whole units as the Housing Bureau prints them -- which is to say
rounded to the nearest thousand at source. Nothing here ever divides or
re-rounds them; see :func:`hk_estates_supply.history.quarter_on_quarter` for what
that rounding does to a percentage.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Figures:
    """The three headline figures from page 2 of a quarterly PDF, and their sum."""

    land_ready: int
    being_built: int
    built_not_sold: int

    @property
    def total(self) -> int:
        return self.land_ready + self.being_built + self.built_not_sold

    def as_dict(self) -> dict:
        return {
            "land_ready": self.land_ready,
            "being_built": self.being_built,
            "built_not_sold": self.built_not_sold,
            "total": self.total,
        }


@dataclass(frozen=True)
class QuarterRow:
    """One line of the history CSV: a quarter label and its four numbers."""

    quarter: str  # "2026/Jun"
    land_ready: int
    being_built: int
    built_not_sold: int
    total: int

    @property
    def figures(self) -> Figures:
        return Figures(self.land_ready, self.being_built, self.built_not_sold)

    def as_dict(self) -> dict:
        return {
            "quarter": self.quarter,
            "land_ready": self.land_ready,
            "being_built": self.being_built,
            "built_not_sold": self.built_not_sold,
            "total": self.total,
        }


@dataclass(frozen=True)
class Publication:
    """What the index page says is currently published."""

    href: str          # "stat202606.pdf"
    url: str           # absolute
    quarter: str       # "2026/Jun"
    year: int
    month: int
    label: str | None  # the page's own wording, e.g. "2026年6月", when it carries one

    def as_dict(self) -> dict:
        return {
            "href": self.href,
            "url": self.url,
            "quarter": self.quarter,
            "published_label": self.label,
        }
