"""On-demand purge and media GC.

Invoked only by explicit user request, relayed by the agent. Never scheduled.
Always account-scoped: there is no all-accounts purge and no flag that adds one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import clock, store
from .accounts import AccountScope
from .errors import NotFoundError, PurgeUnsafeError
from .models import Coupon

DEFAULT_STATUSES = ("used", "expired")


@dataclass(frozen=True)
class PurgeSelection:
    include_void: bool = False
    older_than_days: int | None = None
    merchant: str | None = None
    ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def statuses(self) -> tuple[str, ...]:
        return DEFAULT_STATUSES + ("void",) if self.include_void else DEFAULT_STATUSES

    def to_dict(self, account_id: str) -> dict:
        return {
            "account_id": account_id,
            "statuses": list(self.statuses),
            "older_than_days": self.older_than_days,
            "merchant": self.merchant,
            "ids": list(self.ids),
        }


def select(scope: AccountScope, selection: PurgeSelection, now: datetime) -> list[Coupon]:
    sql = ["SELECT * FROM coupon WHERE account_id = ?"]
    params: list[object] = [scope.account_id]

    placeholders = ", ".join("?" for _ in selection.statuses)
    sql.append(f"AND status IN ({placeholders})")
    params.extend(selection.statuses)

    if selection.merchant:
        sql.append("AND merchant LIKE ?")
        params.append(f"%{selection.merchant}%")

    if selection.older_than_days is not None:
        cutoff = clock.today(now) - timedelta(days=selection.older_than_days)
        sql.append("AND expires_on < ?")
        params.append(clock.iso_date(cutoff))

    if selection.ids:
        # An id from another account must fail loudly, not be silently skipped.
        for coupon_id in selection.ids:
            store.get(scope, coupon_id)
        id_placeholders = ", ".join("?" for _ in selection.ids)
        sql.append(f"AND id IN ({id_placeholders})")
        params.extend(selection.ids)

    sql.append("ORDER BY expires_on, merchant")
    rows = scope.conn.execute(" ".join(sql), params).fetchall()
    return [Coupon.from_row(row) for row in rows]


def run(
    scope: AccountScope,
    selection: PurgeSelection,
    now: datetime,
    *,
    commit: bool = False,
) -> dict:
    """Purge the selection and GC any media it releases.

    Stages 1 and 2 run in one transaction; file deletions happen only after the
    DB commit succeeds, so a DB failure can never leave orphaned deletions.
    """
    scope.ensure_dirs()
    targets = select(scope, selection, now)
    target_ids = {c.id for c in targets}

    anomalies = _scan_anomalies(scope)

    # Which media would drop to zero references once the targets are gone?
    media_rows = scope.conn.execute(
        "SELECT * FROM media WHERE account_id = ? ORDER BY created_at",
        (scope.account_id,),
    ).fetchall()

    to_delete: list[dict] = []
    held: list[dict] = []
    for row in media_rows:
        referrers = [
            r["id"]
            for r in scope.conn.execute(
                "SELECT id FROM coupon WHERE media_id = ? AND account_id = ?",
                (row["id"], scope.account_id),
            )
        ]
        survivors = [r for r in referrers if r not in target_ids]
        released_by = [r for r in referrers if r in target_ids]
        if not referrers:
            continue
        if survivors:
            held.append(
                {
                    "id": row["id"],
                    "path": row["path"],
                    "refs": len(survivors),
                    "held_by": survivors,
                    "held_reason": "surviving coupons still reference this image",
                }
            )
        else:
            to_delete.append(
                {
                    "id": row["id"],
                    "path": row["path"],
                    "bytes": row["bytes"],
                    "released_by": released_by,
                }
            )

    bytes_freed = 0
    for entry in to_delete:
        target = scope.media_dir / entry["path"]
        if target.is_file():
            bytes_freed += target.stat().st_size

    manifest = {
        "ran_at": clock.iso(now),
        "dry_run": not commit,
        "account": {"id": scope.account_id, "display_name": scope.account.display_name},
        "selection": selection.to_dict(scope.account_id),
        "coupons_purged": [
            {
                "id": c.id,
                "merchant": c.merchant,
                "title": c.title,
                "final_status": c.status,
                "expires_on": c.expires_on,
            }
            for c in targets
        ],
        "media_deleted": to_delete,
        "media_held": held,
        "anomalies": anomalies,
        "totals": {
            "coupons": len(targets),
            "media_deleted": len(to_delete),
            "media_held": len(held),
            "bytes_freed": bytes_freed,
        },
    }

    if commit and targets:
        scope.conn.execute("BEGIN IMMEDIATE")
        try:
            scope.conn.executemany(
                "DELETE FROM coupon WHERE id = ? AND account_id = ?",
                [(c.id, scope.account_id) for c in targets],
            )
            scope.conn.executemany(
                "DELETE FROM media WHERE id = ? AND account_id = ?",
                [(m["id"], scope.account_id) for m in to_delete],
            )
        except BaseException:
            scope.conn.execute("ROLLBACK")
            raise
        scope.conn.execute("COMMIT")

        # Files only after the DB is durable.
        for entry in to_delete:
            target = scope.media_dir / entry["path"]
            if target.is_file():
                target.unlink()

    return manifest


def write_manifest(scope: AccountScope, manifest: dict, now: datetime) -> Path:
    scope.config.logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.iso(now).replace(":", "").replace("-", "")
    path = scope.config.logs_dir / f"purge-{scope.account_id}-{stamp}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def is_unsafe(manifest: dict) -> bool:
    return any(manifest["anomalies"].values())


def check(manifest: dict) -> None:
    """Raise if anomalies were found. Clean cases have already been handled."""
    if is_unsafe(manifest):
        raise PurgeUnsafeError(
            "purge completed for the clean cases but found anomalies",
            {"anomalies": manifest["anomalies"], "totals": manifest["totals"]},
        )


def _scan_anomalies(scope: AccountScope) -> dict:
    """Report, never auto-delete. Reads only this account's media directory."""
    rows = scope.conn.execute(
        "SELECT id, path FROM media WHERE account_id = ?", (scope.account_id,)
    ).fetchall()
    known = {row["path"] for row in rows}

    missing = [
        {"id": row["id"], "path": row["path"]}
        for row in rows
        if not (scope.media_dir / row["path"]).is_file()
    ]

    orphans: list[str] = []
    if scope.media_dir.is_dir():
        for entry in sorted(scope.media_dir.rglob("*")):
            if not entry.is_file():
                continue
            relative = entry.relative_to(scope.media_dir).as_posix()
            if relative not in known:
                orphans.append(relative)

    return {"orphan_files": orphans, "missing_files": missing}


def doctor(scope: AccountScope) -> dict:
    """Integrity checks for one account. `couponctl doctor` runs this per account."""
    problems: list[dict] = []

    dangling = scope.conn.execute(
        "SELECT c.id, c.media_id FROM coupon c LEFT JOIN media m"
        "  ON m.id = c.media_id AND m.account_id = c.account_id"
        " WHERE c.account_id = ? AND c.media_id IS NOT NULL AND m.id IS NULL",
        (scope.account_id,),
    ).fetchall()
    for row in dangling:
        problems.append(
            {
                "kind": "dangling_media_id",
                "coupon_id": row["id"],
                "media_id": row["media_id"],
                "detail": "coupon points at media that is missing or owned by another account",
            }
        )

    mismatched = scope.conn.execute(
        "SELECT a.coupon_id FROM alerts_sent a JOIN coupon c ON c.id = a.coupon_id"
        " WHERE a.account_id = ? AND c.account_id <> a.account_id",
        (scope.account_id,),
    ).fetchall()
    for row in mismatched:
        problems.append(
            {
                "kind": "alert_account_mismatch",
                "coupon_id": row["coupon_id"],
                "detail": "alerts_sent.account_id disagrees with the coupon's owner",
            }
        )

    # Purge deliberately never deletes a media row with zero references — that
    # window belongs to register_media, before its coupon row exists. So the
    # rows can accumulate, and doctor is what makes them visible.
    unreferenced = scope.conn.execute(
        "SELECT m.id, m.path FROM media m LEFT JOIN coupon c"
        "  ON c.media_id = m.id AND c.account_id = m.account_id"
        " WHERE m.account_id = ? AND c.id IS NULL",
        (scope.account_id,),
    ).fetchall()
    for row in unreferenced:
        problems.append(
            {
                "kind": "unreferenced_media",
                "media_id": row["id"],
                "path": row["path"],
                "detail": "media row no coupon references; purge leaves these alone",
            }
        )

    anomalies = _scan_anomalies(scope)
    for orphan in anomalies["orphan_files"]:
        problems.append(
            {"kind": "orphan_media_file", "path": orphan, "detail": "file with no media row"}
        )
    for missing in anomalies["missing_files"]:
        problems.append(
            {
                "kind": "missing_media_file",
                "media_id": missing["id"],
                "path": missing["path"],
                "detail": "media row with no file on disk",
            }
        )

    return {
        "account_id": scope.account_id,
        "display_name": scope.account.display_name,
        "clean": not problems,
        "problems": problems,
    }
