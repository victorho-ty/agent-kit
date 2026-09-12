"""Loading the FOMC calendar, and answering the two questions it exists for.

Which meeting is next, and does a given month contain one? The second is what
decides how a meeting's post-decision rate is recovered from futures prices, so
a calendar with a hole in it does not produce an error -- it produces a
confidently wrong probability. That is why :func:`load` insists the dates are
sorted, unique and parseable, and why running out is a hard error.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..errors import ConfigError

CALENDAR_FILE = Path(__file__).with_name("fomc.json")

# Below this many future meetings, the calendar is close enough to running out
# that somebody should copy next year's in from federalreserve.gov.
REFRESH_WARNING_THRESHOLD = 3


def load(path: Path | None = None) -> list[date]:
    """Every known decision date, ascending."""
    from .. import settings

    resolved = Path(path) if path else settings.calendar_path()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"no FOMC calendar at {resolved}", path=str(resolved)) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read the FOMC calendar: {exc}", path=str(resolved)) from exc

    entries = raw.get("meetings")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("the FOMC calendar has no `meetings` list", path=str(resolved))

    try:
        meetings = [date.fromisoformat(entry) for entry in entries]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"a meeting date is not ISO8601: {exc}", path=str(resolved)) from exc

    if meetings != sorted(set(meetings)):
        raise ConfigError(
            "meeting dates must be sorted and unique",
            path=str(resolved),
            remedy="sort the `meetings` array and remove duplicates",
        )
    return meetings


def upcoming(meetings: list[date], today: date, count: int) -> list[date]:
    """The next ``count`` decisions on or after ``today``, ascending.

    A meeting is still "upcoming" on its own decision day: the announcement
    lands in the afternoon, and until it does the futures are still pricing it.
    """
    ahead = [meeting for meeting in meetings if meeting >= today]
    if len(ahead) < count:
        raise ConfigError(
            f"the FOMC calendar holds {len(ahead)} meeting(s) on or after {today}, "
            f"but {count} were asked for",
            known_through=meetings[-1].isoformat() if meetings else None,
            remedy="add next year's dates to fed_watch/config/fomc.json "
            "from federalreserve.gov/monetarypolicy/fomccalendars.htm",
        )
    return ahead[:count]


def month_has_meeting(meetings: list[date], year: int, month: int) -> bool:
    return any(meeting.year == year and meeting.month == month for meeting in meetings)
