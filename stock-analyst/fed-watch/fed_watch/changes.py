"""What moved since the last time anything was reported.

The comparison is against the last *reported* snapshot, never the last poll.
That distinction is the entire point. ZQ trades continuously, so consecutive
polls always differ a little; comparing poll to poll means a drift of a third of
a point an hour never clears a one-point threshold and is never reported, while
the probability quietly walks ten points in a day. Comparing against the
baseline lets that drift accumulate until it crosses, then reports it once and
moves the baseline.

A meeting that has rolled off the calendar since the baseline was taken is
reported as ``dropped``, and a newly tracked one as ``new``. Neither is a change
in probability and neither is silently folded into one.
"""

from __future__ import annotations

from .models import Outcome


def _outcome_map(meeting: dict) -> dict[int, dict]:
    return {outcome["step"]: outcome for outcome in meeting["outcomes"]}


def diff(baseline: dict | None, current: dict, threshold: float) -> dict:
    """Compare two hydrated snapshots. ``threshold`` is in percentage points."""
    if baseline is None:
        return {
            "changed": True,
            "reason": "first run; nothing to compare against",
            "threshold_pct": threshold,
            "baseline_taken_at": None,
            "meetings": [],
        }

    baseline_meetings = {m["meeting_date"]: m for m in baseline["meetings"]}
    current_meetings = {m["meeting_date"]: m for m in current["meetings"]}

    moved: list[dict] = []
    largest = 0.0

    for meeting_date, meeting in current_meetings.items():
        before = baseline_meetings.get(meeting_date)
        if before is None:
            moved.append(
                {
                    "meeting_date": meeting_date,
                    "status": "new",
                    "note": "not tracked at the baseline",
                    "outcomes": [
                        {
                            "label": _label(o["step"]),
                            "band": f"{o['band_low']:.2f}-{o['band_high']:.2f}%",
                            "from_pct": None,
                            "to_pct": round(o["probability"] * 100, 1),
                            "delta_pct": None,
                        }
                        for o in meeting["outcomes"]
                    ],
                }
            )
            continue

        previous = _outcome_map(before)
        rows = []
        meeting_largest = 0.0
        for outcome in meeting["outcomes"]:
            was = previous.get(outcome["step"])
            from_pct = round(was["probability"] * 100, 1) if was else 0.0
            to_pct = round(outcome["probability"] * 100, 1)
            delta = round(to_pct - from_pct, 1)
            if abs(delta) >= threshold:
                rows.append(
                    {
                        "label": _label(outcome["step"]),
                        "band": f"{outcome['band_low']:.2f}-{outcome['band_high']:.2f}%",
                        "from_pct": from_pct,
                        "to_pct": to_pct,
                        "delta_pct": delta,
                    }
                )
                meeting_largest = max(meeting_largest, abs(delta))

        if rows:
            largest = max(largest, meeting_largest)
            moved.append(
                {
                    "meeting_date": meeting_date,
                    "status": "moved",
                    "contract": meeting["contract"],
                    "implied_rate_from": before["implied_rate"],
                    "implied_rate_to": meeting["implied_rate"],
                    "expected_rate_from": before["expected_rate"],
                    "expected_rate_to": meeting["expected_rate"],
                    "largest_move_pct": meeting_largest,
                    "outcomes": rows,
                }
            )

    for meeting_date in baseline_meetings:
        if meeting_date not in current_meetings:
            moved.append(
                {
                    "meeting_date": meeting_date,
                    "status": "dropped",
                    "note": "no longer among the tracked meetings",
                }
            )

    policy_moved = (
        baseline["effr"] != current["effr"]
        or baseline["target_low"] != current["target_low"]
        or baseline["target_high"] != current["target_high"]
    )

    return {
        "changed": bool(moved) or policy_moved,
        "threshold_pct": threshold,
        "baseline_taken_at": baseline["taken_at"],
        "largest_move_pct": largest,
        "policy_changed": policy_moved,
        "policy_from": {
            "effr": baseline["effr"],
            "target_range": f"{baseline['target_low']:.2f}-{baseline['target_high']:.2f}%",
        }
        if policy_moved
        else None,
        "meetings": moved,
    }


def _label(step: int) -> str:
    return Outcome(step=step, band_low=0.0, band_high=0.0, probability=0.0).label


def series(history: list[dict]) -> dict[str, dict]:
    """Reported snapshots reshaped into one probability path per outcome band.

    ``{meeting_date: {"timestamps": [...], "series": {label: [pct, ...]}}}``.
    A band absent from an earlier snapshot reads as 0.0 rather than as a gap:
    it was priced at nothing, which is a real reading and plots as one.
    """
    shaped: dict[str, dict] = {}
    for snapshot in history:
        for meeting in snapshot["meetings"]:
            entry = shaped.setdefault(
                meeting["meeting_date"], {"timestamps": [], "series": {}}
            )
            entry["timestamps"].append(snapshot["taken_at"])
            seen = set()
            for outcome in meeting["outcomes"]:
                label = _label(outcome["step"])
                seen.add(label)
                points = entry["series"].setdefault(label, [])
                points.extend([0.0] * (len(entry["timestamps"]) - 1 - len(points)))
                points.append(round(outcome["probability"] * 100, 1))
            for label, points in entry["series"].items():
                if label not in seen:
                    points.extend([0.0] * (len(entry["timestamps"]) - len(points)))
    return shaped
