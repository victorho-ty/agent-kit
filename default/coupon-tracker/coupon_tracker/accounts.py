"""Accounts, and the scope handle every other module takes.

``AccountScope`` is the only way the rest of the package reaches the database.
No function in ``store``, ``query``, ``lifecycle``, ``purge`` or ``alerts``
accepts a bare connection, so no query can forget its ``WHERE account_id = ?``.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ulid import ULID

from . import clock
from .config import Config
from .errors import AccountError
from .models import Account


@dataclass(frozen=True)
class AccountScope:
    """A connection bound to exactly one account.

    Paths are derived here and never accepted from a caller, so a file can only
    ever land inside its owner's directory.
    """

    conn: sqlite3.Connection
    config: Config
    account: Account

    @property
    def account_id(self) -> str:
        return self.account.id

    @property
    def media_dir(self) -> Path:
        return self.config.account_media_dir(self.account_id)

    @property
    def inbox_dir(self) -> Path:
        return self.config.account_inbox_dir(self.account_id)

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def media_path(self, relative: str) -> Path:
        """Resolve a media-relative path, refusing anything that escapes."""
        return _contained(self.media_dir, relative, self.account_id)

    def inbox_path(self, relative: str) -> Path:
        return _contained(self.inbox_dir, relative, self.account_id)

    def assert_owns_path(self, path: str | Path) -> Path:
        """Confirm a path handed in from outside belongs to this account."""
        resolved = Path(path).expanduser().resolve()
        for root in (self.media_dir, self.inbox_dir):
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                continue
            return resolved
        raise AccountError(
            f"path does not belong to account {self.account_id}: {path}",
            {"account_id": self.account_id, "path": str(path)},
        )


def _contained(root: Path, relative: str, account_id: str) -> Path:
    candidate = (root / relative).expanduser()
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise AccountError(
            f"path escapes account directory: {relative}",
            {"account_id": account_id, "path": relative},
        ) from exc
    return candidate


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def get(conn: sqlite3.Connection, account_id: str) -> Account | None:
    row = conn.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
    return Account.from_row(row) if row else None


def resolve_telegram(conn: sqlite3.Connection, telegram_user_id: str | int) -> Account | None:
    row = conn.execute(
        "SELECT * FROM account WHERE telegram_user_id = ?", (str(telegram_user_id),)
    ).fetchone()
    return Account.from_row(row) if row else None


def open_scope(conn: sqlite3.Connection, config: Config, account_id: str) -> AccountScope:
    """Bind a connection to an account. Unknown id is ERR_ACCOUNT, never a create."""
    account = get(conn, account_id)
    if account is None:
        raise AccountError(f"unknown account: {account_id}", {"account_id": account_id})
    scope = AccountScope(conn=conn, config=config, account=account)
    scope.ensure_dirs()
    return scope


def open_scope_for_telegram(
    conn: sqlite3.Connection, config: Config, telegram_user_id: str | int
) -> AccountScope:
    account = resolve_telegram(conn, telegram_user_id)
    if account is None:
        raise AccountError(
            f"no account bound to telegram user {telegram_user_id}",
            {"telegram_user_id": str(telegram_user_id)},
        )
    return open_scope(conn, config, account.id)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def create(
    conn: sqlite3.Connection,
    config: Config,
    display_name: str,
    now: datetime,
    *,
    telegram_user_id: str | int | None = None,
    chat_id: str | int | None = None,
) -> Account:
    name = (display_name or "").strip()
    if not name:
        raise AccountError("display name is required")

    tg = str(telegram_user_id).strip() if telegram_user_id is not None else None
    if tg:
        existing = resolve_telegram(conn, tg)
        if existing is not None:
            raise AccountError(
                f"telegram user {tg} already belongs to account {existing.id}",
                {"telegram_user_id": tg, "account_id": existing.id},
            )

    stamp = clock.iso(now)
    account_id = str(ULID())
    conn.execute(
        "INSERT INTO account (id, display_name, telegram_user_id, chat_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            account_id,
            name,
            tg or None,
            str(chat_id) if chat_id is not None else None,
            stamp,
            stamp,
        ),
    )
    account = get(conn, account_id)
    assert account is not None
    config.account_media_dir(account_id).mkdir(parents=True, exist_ok=True)
    config.account_inbox_dir(account_id).mkdir(parents=True, exist_ok=True)
    return account


def update(
    conn: sqlite3.Connection,
    account_id: str,
    now: datetime,
    *,
    display_name: str | None = None,
    telegram_user_id: str | int | None = None,
    chat_id: str | int | None = None,
) -> Account:
    account = get(conn, account_id)
    if account is None:
        raise AccountError(f"unknown account: {account_id}", {"account_id": account_id})

    fields: list[str] = []
    values: list[object] = []

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise AccountError("display name cannot be empty")
        fields.append("display_name = ?")
        values.append(name)

    if telegram_user_id is not None:
        tg = str(telegram_user_id).strip() or None
        if tg:
            clash = resolve_telegram(conn, tg)
            if clash is not None and clash.id != account_id:
                raise AccountError(
                    f"telegram user {tg} already belongs to account {clash.id}",
                    {"telegram_user_id": tg, "account_id": clash.id},
                )
        fields.append("telegram_user_id = ?")
        values.append(tg)

    if chat_id is not None:
        fields.append("chat_id = ?")
        values.append(str(chat_id).strip() or None)

    if not fields:
        return account

    fields.append("updated_at = ?")
    values.extend([clock.iso(now), account_id])
    conn.execute(f"UPDATE account SET {', '.join(fields)} WHERE id = ?", values)
    refreshed = get(conn, account_id)
    assert refreshed is not None
    return refreshed


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute("SELECT * FROM account ORDER BY id").fetchall()
    return [Account.from_row(row) for row in rows]


def summary(conn: sqlite3.Connection, account_id: str) -> dict:
    """Account plus row counts — what `account show` prints."""
    account = get(conn, account_id)
    if account is None:
        raise AccountError(f"unknown account: {account_id}", {"account_id": account_id})

    by_status = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM coupon WHERE account_id = ? GROUP BY status",
            (account_id,),
        )
    }
    media_count, media_bytes = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM media WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    inbox_queued = conn.execute(
        "SELECT COUNT(*) FROM inbox_item WHERE account_id = ? AND state = 'queued'",
        (account_id,),
    ).fetchone()[0]

    return {
        "account": account.to_dict(),
        "coupons": {"total": sum(by_status.values()), "by_status": by_status},
        "media": {"count": media_count, "bytes": media_bytes},
        "inbox": {"queued": inbox_queued},
    }


def delete(
    conn: sqlite3.Connection,
    config: Config,
    account_id: str,
    now: datetime,
    *,
    commit: bool = False,
) -> dict:
    """Delete an account and everything it owns. Dry-run by default.

    Follows the purge protocol: the DB transaction commits first, files go
    afterwards, and the manifest lists everything either way.
    """
    account = get(conn, account_id)
    if account is None:
        raise AccountError(f"unknown account: {account_id}", {"account_id": account_id})

    counts = summary(conn, account_id)
    media_dir = config.account_media_dir(account_id)
    inbox_dir = config.account_inbox_dir(account_id)
    files = [
        str(p.relative_to(config.root)) if p.is_relative_to(config.root) else str(p)
        for directory in (media_dir, inbox_dir)
        if directory.exists()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    ]
    bytes_freed = sum(
        p.stat().st_size
        for directory in (media_dir, inbox_dir)
        if directory.exists()
        for p in directory.rglob("*")
        if p.is_file()
    )

    manifest = {
        "ran_at": clock.iso(now),
        "dry_run": not commit,
        "account": account.to_dict(),
        "coupons": counts["coupons"],
        "media": counts["media"],
        "files_deleted": files,
        "directories_deleted": [str(media_dir), str(inbox_dir)],
        "totals": {"files": len(files), "bytes_freed": bytes_freed},
    }

    if not commit:
        return manifest

    # DB first: a failure here must leave the files alone.
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM account WHERE id = ?", (account_id,))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    for directory in (media_dir, inbox_dir):
        shutil.rmtree(directory, ignore_errors=True)

    return manifest
