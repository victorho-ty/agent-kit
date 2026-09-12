"""30-Day Fed Funds futures symbols, and which contract answers which meeting.

The symbol form is the part that is not guessable. Yahoo serves dated CBOT
contracts as ``ZQ<month code><two-digit year>.CBT`` -- ``ZQU26.CBT``. The two
spellings a reasonable person tries first, ``ZQU26=F`` and bare ``ZQU26``, both
404, and the widely cited ``ZQ=F`` is the front-month continuous series, which
cannot answer a question about a meeting two months out.

Expired contracts are delisted rather than archived, so a month that has already
settled cannot be read back. Nothing here looks backwards.
"""

from __future__ import annotations

from datetime import date

# CME month codes. January through December, in order.
MONTH_CODES = "FGHJKMNQUVXZ"

ROOT = "ZQ"
SUFFIX = ".CBT"


def symbol(year: int, month: int) -> str:
    """The Yahoo symbol for the ZQ contract delivering in ``month`` of ``year``."""
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return f"{ROOT}{MONTH_CODES[month - 1]}{year % 100:02d}{SUFFIX}"


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def symbol_for_meeting(meeting: date) -> str:
    return symbol(meeting.year, meeting.month)
