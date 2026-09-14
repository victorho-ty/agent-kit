"""Taking one reading: fetch, compute, store.

The order is fixed and the whole of it is cheap -- one call to the New York Fed
and one Yahoo request per distinct contract (a second only when the intraday
series is empty and the daily bar stands in). That is what makes snapping
several times a day reasonable.

Only the contracts the calculation will actually use are requested. Which those
are depends on the calendar, not on the meeting dates alone: a meeting followed
by a clear month is answered by that clear month's contract, and its own month's
is never fetched. See ``probabilities.plan``.
"""

from __future__ import annotations

from datetime import date, datetime

from . import clock, db, probabilities, quotes, rates
from .config import fomc
from .models import MeetingProbabilities

MEETINGS_TRACKED = 2

# Kept short on purpose: it rides on every payload and the delivery surface is a phone.
ATTRIBUTION = "Computed from 30d FedFund futures using CME methodology"


def take(conn, *, now: datetime | None = None, meetings_tracked: int = MEETINGS_TRACKED) -> dict:
    """Fetch, compute and store one snapshot. Returns the payload."""
    taken_at = now or clock.now()
    calendar = fomc.load()
    upcoming = fomc.upcoming(calendar, taken_at.date(), meetings_tracked)

    def month_has_meeting(year: int, month: int) -> bool:
        return fomc.month_has_meeting(calendar, year, month)

    wanted = [probabilities.plan(meeting, month_has_meeting)[1] for meeting in upcoming]
    policy = rates.current()
    resolved, failures = quotes.fetch(wanted)

    priced = probabilities.solve(policy, upcoming, resolved, month_has_meeting)
    status = "ok" if len(priced) == len(upcoming) else "partial"
    price_source = quotes.describe([resolved[meeting.contract] for meeting in priced])

    snapshot_id = db.store_snapshot(
        conn,
        taken_at,
        policy,
        priced,
        price_source,
        status,
        {meeting.contract: quotes.as_of_label(resolved[meeting.contract]) for meeting in priced},
    )

    payload = render(taken_at, policy, priced, status, price_source, snapshot_id)
    payload["ahead"] = len([m for m in calendar if m >= taken_at.date()])
    if payload["ahead"] < fomc.REFRESH_WARNING_THRESHOLD:
        payload["calendar_warning"] = (
            f"only {payload['ahead']} FOMC date(s) left in fed_watch/config/fomc.json; "
            "add next year's from federalreserve.gov"
        )
    if failures:
        payload["failures"] = failures
    return payload


def render(
    taken_at: datetime,
    policy,
    priced: list[MeetingProbabilities],
    status: str,
    price_source: str,
    snapshot_id: int | None = None,
) -> dict:
    """One snapshot as the JSON the agent reads."""
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "taken_at": taken_at.isoformat(),
        "status": status,
        "price_source": price_source,
        "attribution": ATTRIBUTION,
        "policy": {
            "effr": policy.effr,
            "as_of": policy.as_of.isoformat(),
            "target_low": policy.target_low,
            "target_high": policy.target_high,
            "target_range": f"{policy.target_low:.2f}-{policy.target_high:.2f}%",
        },
        "meetings": [meeting_payload(meeting) for meeting in priced],
    }


def meeting_payload(meeting: MeetingProbabilities) -> dict:
    payload = {
        "meeting_date": meeting.meeting_date.isoformat(),
        "ordinal": meeting.ordinal,
        "contract": meeting.contract,
        "price": round(meeting.price, 6),
        "implied_rate": meeting.implied_rate,
        "expected_rate": meeting.expected_rate,
        "method": meeting.method,
        "outcomes": [
            {
                "step": outcome.step,
                "basis_points": outcome.basis_points,
                "label": outcome.label,
                "band": f"{outcome.band_low:.2f}-{outcome.band_high:.2f}%",
                "probability_pct": round(outcome.probability * 100, 1),
            }
            for outcome in meeting.outcomes
        ],
    }
    return _with_amplification(payload, meeting.meeting_date, meeting.method)


def stored_meeting_payload(meeting: dict) -> dict:
    """The same shape, rebuilt from a stored row."""
    payload = {
        "meeting_date": meeting["meeting_date"],
        "ordinal": meeting["ordinal"],
        "contract": meeting["contract"],
        "price": meeting["price"],
        "implied_rate": meeting["implied_rate"],
        "expected_rate": meeting["expected_rate"],
        "method": meeting["method"],
        "outcomes": [
            {
                "step": outcome["step"],
                "basis_points": outcome["step"] * 25,
                "label": _label(outcome["step"]),
                "band": f"{outcome['band_low']:.2f}-{outcome['band_high']:.2f}%",
                "probability_pct": round(outcome["probability"] * 100, 1),
            }
            for outcome in meeting["outcomes"]
        ],
    }
    return _with_amplification(
        payload, date.fromisoformat(meeting["meeting_date"]), meeting["method"]
    )


def _with_amplification(payload: dict, meeting_date: date, method: str) -> dict:
    """Attach the noise diagnostics, and ``noisy`` only when it is true.

    A clear following month is read directly, so nothing is amplified.

    ``noisy: false`` is deliberately absent rather than present-and-false. It
    tells the reader nothing, and a field that is there invites the agent to
    narrate the all-clear -- which is exactly how "Both readings clean: no noisy
    flag" reached a Telegram message. A field that is not in the payload cannot
    be reported.
    """
    factor = probabilities.amplification(meeting_date) if method == "blend_inversion" else 1.0
    payload["amplification"] = round(factor, 2)
    if factor > probabilities.AMPLIFICATION_WARNING:
        payload["noisy"] = True
    return payload


def _label(step: int) -> str:
    if step == 0:
        return "no change"
    return f"{abs(step) * 25}bp {'hike' if step > 0 else 'cut'}"
