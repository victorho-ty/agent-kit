"""What has been reported, and what the daily check has been doing.

Two files, both under the profile state directory, both disposable:

``hk_supply_state.json`` is the delivery ledger -- one timestamp per quarter that
has actually been sent. **It is what makes the report queue self-healing.** A
quarter present in the history CSV but absent from the ledger is pending, so a
send that failed on Monday is still pending on Tuesday without any retry logic,
and a send that succeeded is never repeated however many times the cron fires.
Deleting the file re-queues everything, which is a blunt but honest recovery.

``hk_supply_runs.jsonl`` is the liveness record -- one line per daily check,
written on failures too. A monitor that is *correct* is silent for three months
at a stretch, so "nothing was said" and "nothing has run since April" look
identical from the outside; this file is the only thing that tells them apart.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import settings

SCHEMA = 1


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "reported": {}, "consecutive_failures": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger costs at most one duplicate report. Refusing to run
        # until somebody repairs a cache file would cost the report itself.
        return {"schema": SCHEMA, "reported": {}, "consecutive_failures": 0}
    data.setdefault("reported", {})
    data.setdefault("consecutive_failures", 0)
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=str(path.parent),
        prefix=path.name, suffix=".tmp",
    )
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def load(path: Path | None = None) -> dict:
    return _read_json(Path(path) if path is not None else settings.state_file())


def reported_quarters(path: Path | None = None) -> dict[str, str]:
    return dict(load(path).get("reported", {}))


def is_reported(quarter: str, path: Path | None = None) -> bool:
    return quarter in load(path).get("reported", {})


def mark_reported(quarter: str, when, path: Path | None = None) -> None:
    """Stamp a quarter as delivered. Idempotent: the first stamp is kept.

    Kept rather than refreshed so the ledger answers "when did this household
    first hear about 2026/Jun", which does not change when somebody asks for the
    report again in November.
    """
    target = Path(path) if path is not None else settings.state_file()
    data = _read_json(target)
    data["reported"].setdefault(quarter, when.isoformat())
    _write_json(target, data)


def note_failure(path: Path | None = None) -> int:
    target = Path(path) if path is not None else settings.state_file()
    data = _read_json(target)
    data["consecutive_failures"] = int(data.get("consecutive_failures", 0)) + 1
    _write_json(target, data)
    return data["consecutive_failures"]


def note_success(path: Path | None = None) -> None:
    target = Path(path) if path is not None else settings.state_file()
    data = _read_json(target)
    if data.get("consecutive_failures"):
        data["consecutive_failures"] = 0
        _write_json(target, data)


def consecutive_failures(path: Path | None = None) -> int:
    return int(load(path).get("consecutive_failures", 0))


def ensure_seeded(rows, when, path: Path | None = None) -> bool:
    """Absorb the existing history on first use. Returns True if it seeded.

    Without this, installing the bundle makes eighteen quarters pending at once
    and the first thing the household sees is eighteen reports about figures
    published years ago -- and the alert everyone remembers is the one they had
    to mute. A back catalogue is not news.

    Called explicitly rather than from :func:`pending`, and always *before* a new
    quarter is appended, so the quarter that arrives on the very first run is
    still reported rather than being absorbed along with the history.
    """
    target = Path(path) if path is not None else settings.state_file()
    data = _read_json(target)
    if data.get("seeded"):
        return False
    for row in rows:
        data["reported"].setdefault(row.quarter, when.isoformat())
    data["seeded"] = True
    data["seeded_at"] = when.isoformat()
    _write_json(target, data)
    return True


def pending(rows, path: Path | None = None) -> list[str]:
    """Quarters in the history that have never been reported, oldest first.

    Normally empty, or one item long for the day a quarter is published. Pure:
    seeding is :func:`ensure_seeded`'s job, because the queue must not be
    redefined by the act of reading it.
    """
    done = load(path).get("reported", {})
    from .history import quarter_key

    return sorted(
        (row.quarter for row in rows if row.quarter not in done),
        key=quarter_key,
    )


# ------------------------------------------------------------------- run log


def record_run(entry: dict, path: Path | None = None) -> None:
    """Append one line, then trim. Never raises: a full disk must not lose a report."""
    target = Path(path) if path is not None else settings.runs_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        _trim(target)
    except OSError:
        pass


def _trim(path: Path) -> None:
    keep = settings.RUN_LOG_LINES
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= keep * 2:  # rewrite in batches rather than on every append
        return
    path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")


def recent_runs(limit: int, path: Path | None = None) -> list[dict]:
    """The most recent runs, newest first. Unparseable lines are skipped, not fatal."""
    target = Path(path) if path is not None else settings.runs_file()
    if not target.exists():
        return []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
