"""Scoped CRUD, dedupe, media registration and the ingest commit.

Every function takes an ``AccountScope``. There is no variant that takes a bare
connection, and every statement carries ``account_id = ?``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from ulid import ULID

from . import clock, predicates
from .accounts import AccountScope
from .errors import AccountError, CandidateMismatchError, NotFoundError
from .models import Coupon, MediaRef, Predicate

_WHITESPACE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get(scope: AccountScope, coupon_id: str) -> Coupon:
    """Fetch one coupon within the scope.

    Another account's id raises NotFoundError — deliberately identical to a
    nonexistent id, so this cannot be used to probe what exists elsewhere.
    """
    row = scope.conn.execute(
        "SELECT * FROM coupon WHERE id = ? AND account_id = ?",
        (coupon_id, scope.account_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no such coupon: {coupon_id}", {"id": coupon_id})
    return Coupon.from_row(row)


def find(scope: AccountScope, coupon_id: str) -> Coupon | None:
    try:
        return get(scope, coupon_id)
    except NotFoundError:
        return None


def get_media(scope: AccountScope, media_id: str) -> MediaRef | None:
    row = scope.conn.execute(
        "SELECT * FROM media WHERE id = ? AND account_id = ?",
        (media_id, scope.account_id),
    ).fetchone()
    return MediaRef.from_row(row) if row else None


def media_ref_count(scope: AccountScope, media_id: str) -> int:
    return scope.conn.execute(
        "SELECT COUNT(*) FROM coupon WHERE media_id = ? AND account_id = ?",
        (media_id, scope.account_id),
    ).fetchone()[0]


# --------------------------------------------------------------------------- #
# Dedupe
# --------------------------------------------------------------------------- #


def normalize(value: str | None) -> str:
    """Fold case, width and whitespace so 'Cafe  DE  Coral' == 'cafe de coral'."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).strip().casefold()
    return _WHITESPACE.sub(" ", folded)


def dedupe_key(merchant: str, title: str, expires_on: str) -> str:
    payload = f"{normalize(merchant)}|{normalize(title)}|{expires_on}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def find_duplicate(scope: AccountScope, key: str) -> Coupon | None:
    """Dedupe is per account: the same voucher in two accounts is not a collision."""
    row = scope.conn.execute(
        "SELECT * FROM coupon WHERE dedupe_key = ? AND account_id = ?"
        " ORDER BY created_at LIMIT 1",
        (key, scope.account_id),
    ).fetchone()
    return Coupon.from_row(row) if row else None


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_media(
    scope: AccountScope,
    source_path: str | Path,
    now: datetime,
    *,
    mime: str = "image/jpeg",
) -> MediaRef:
    """Copy a file into this account's media dir, reusing the row on hash match.

    Reuse is keyed on (account_id, sha256): the same photo forwarded twice gives
    one file and a correct reference count, while an identical photo in another
    account stays a separate row and a separate file.
    """
    source = Path(source_path)
    if not source.is_file():
        raise NotFoundError(f"no such file: {source}", {"path": str(source)})

    digest = sha256_file(source)
    existing = scope.conn.execute(
        "SELECT * FROM media WHERE account_id = ? AND sha256 = ?",
        (scope.account_id, digest),
    ).fetchone()
    if existing is not None:
        return MediaRef.from_row(existing)

    scope.ensure_dirs()
    relative = f"{digest[:16]}{source.suffix.lower()}"
    target = scope.media_path(relative)
    shutil.copy2(source, target)

    media_id = str(ULID())
    scope.conn.execute(
        "INSERT INTO media (id, account_id, sha256, path, mime, bytes, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            media_id,
            scope.account_id,
            digest,
            relative,
            mime,
            target.stat().st_size,
            clock.iso(now),
        ),
    )
    ref = get_media(scope, media_id)
    assert ref is not None
    return ref


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def add_coupon(
    scope: AccountScope,
    now: datetime,
    *,
    merchant: str,
    title: str,
    expires_on: str,
    status: str = "active",
    expiry_precision: str = "exact",
    expiry_assumed: bool = False,
    uses_total: int = 1,
    uses_remaining: int | None = None,
    conditions: list[Predicate] | list[dict] | None = None,
    code: str | None = None,
    value_text: str | None = None,
    notes: str | None = None,
    raw_text: str | None = None,
    source_kind: str = "manual",
    source_ref: str | None = None,
    media_id: str | None = None,
    extraction_confidence: float | None = None,
) -> Coupon:
    validated = _as_predicates(conditions)

    if media_id is not None and get_media(scope, media_id) is None:
        # Belt-and-braces: a coupon may never point at another account's media.
        raise AccountError(
            f"media {media_id} does not belong to account {scope.account_id}",
            {"media_id": media_id, "account_id": scope.account_id},
        )

    clock.parse_date(expires_on)  # raises ValueError on a malformed date
    stamp = clock.iso(now)
    coupon_id = str(ULID())
    scope.conn.execute(
        """
        INSERT INTO coupon (
            id, account_id, merchant, title, code, value_text, expires_on,
            expiry_precision, expiry_assumed, status, uses_total, uses_remaining,
            conditions_json, notes, raw_text, source_kind, source_ref, media_id,
            extraction_confidence, dedupe_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            coupon_id,
            scope.account_id,
            merchant,
            title,
            code,
            value_text,
            expires_on,
            expiry_precision,
            int(bool(expiry_assumed)),
            status,
            uses_total,
            uses_total if uses_remaining is None else uses_remaining,
            json.dumps([p.to_dict() for p in validated], ensure_ascii=False),
            notes,
            raw_text,
            source_kind,
            source_ref,
            media_id,
            extraction_confidence,
            dedupe_key(merchant, title, expires_on),
            stamp,
            stamp,
        ),
    )
    return get(scope, coupon_id)


def set_notes(scope: AccountScope, coupon_id: str, notes: str, now: datetime) -> Coupon:
    get(scope, coupon_id)  # scope check
    scope.conn.execute(
        "UPDATE coupon SET notes = ?, updated_at = ? WHERE id = ? AND account_id = ?",
        (notes, clock.iso(now), coupon_id, scope.account_id),
    )
    return get(scope, coupon_id)


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


def commit_candidates(
    scope: AccountScope,
    path: str | Path,
    now: datetime,
    *,
    auto_confirm: bool = False,
) -> dict:
    """Commit an agent-produced candidates file. All-or-nothing.

    Order matters: the account check comes first, then predicate validation, so
    a bad file produces zero partial writes.
    """
    candidates_path = Path(path)
    if not candidates_path.is_file():
        raise NotFoundError(f"no such candidates file: {path}", {"path": str(path)})

    # Step 0 — this file must belong to this account, and live in its inbox.
    scope.assert_owns_path(candidates_path)
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    stated_account = payload.get("account_id")
    if stated_account and stated_account != scope.account_id:
        raise AccountError(
            f"candidates file belongs to account {stated_account}, not {scope.account_id}",
            {"file_account_id": stated_account, "account_id": scope.account_id},
        )

    source = payload.get("source") or {}
    source_kind = source.get("kind", "file")
    raw_text = source.get("raw_text")
    raw_candidates = payload.get("candidates") or []

    # Step 1 — validate every predicate before writing anything.
    validated: list[tuple[dict, list[Predicate]]] = [
        (candidate, predicates.validate(candidate.get("conditions")))
        for candidate in raw_candidates
    ]

    # Step 2 — a count mismatch means the extraction is not trustworthy.
    stated_count = payload.get("coupon_count_stated")
    count_mismatch = stated_count is not None and stated_count != len(raw_candidates)

    media_id = None
    media_sha = source.get("media_sha256")
    if media_sha:
        row = scope.conn.execute(
            "SELECT id FROM media WHERE account_id = ? AND sha256 = ?",
            (scope.account_id, media_sha),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"media not registered for this account: {media_sha}",
                {"sha256": media_sha},
            )
        media_id = row["id"]

    threshold = scope.config.review_threshold
    committed: list[dict] = []

    for candidate, conditions in validated:
        merchant = (candidate.get("merchant") or "").strip()
        title = (candidate.get("title") or "").strip()
        expires_on = candidate.get("expires_on")
        confidence = candidate.get("confidence")
        expiry_assumed = bool(candidate.get("expiry_assumed"))

        key = dedupe_key(merchant, title, expires_on or "")
        duplicate = find_duplicate(scope, key)

        reasons: list[str] = []
        if count_mismatch:
            reasons.append(
                f"stated {stated_count} coupons but extracted {len(raw_candidates)}"
            )
        if not merchant:
            reasons.append("merchant missing")
        if confidence is not None and confidence < threshold:
            reasons.append(f"confidence {confidence:.2f} below {threshold:.2f}")
        if expiry_assumed:
            reasons.append("expiry date was assumed, not printed")
        if duplicate is not None:
            reasons.append(f"looks like a duplicate of {duplicate.id}")

        status = "active" if (auto_confirm or not reasons) else "needs_review"
        notes = candidate.get("notes")
        if reasons:
            flagged = "needs review: " + "; ".join(reasons)
            notes = f"{notes}\n{flagged}" if notes else flagged

        coupon = add_coupon(
            scope,
            now,
            merchant=merchant,
            title=title,
            expires_on=expires_on,
            status=status,
            expiry_precision=candidate.get("expiry_precision", "exact"),
            expiry_assumed=expiry_assumed,
            uses_total=candidate.get("uses_total", 1),
            conditions=conditions,
            code=candidate.get("code"),
            value_text=candidate.get("value_text"),
            notes=notes,
            raw_text=raw_text,
            source_kind=source_kind,
            source_ref=source.get("ref"),
            media_id=media_id,
            extraction_confidence=confidence,
        )
        committed.append(
            {
                "id": coupon.id,
                "merchant": coupon.merchant,
                "title": coupon.title,
                "status": coupon.status,
                "expires_on": coupon.expires_on,
                "review_reasons": reasons,
            }
        )

    return {
        "account_id": scope.account_id,
        "committed": committed,
        "coupon_count_stated": stated_count,
        "count_mismatch": count_mismatch,
        "media_id": media_id,
        "totals": {
            "committed": len(committed),
            "needs_review": sum(1 for c in committed if c["status"] == "needs_review"),
        },
    }


def _as_predicates(conditions) -> list[Predicate]:
    if not conditions:
        return []
    if all(isinstance(c, Predicate) for c in conditions):
        return list(conditions)
    return predicates.validate([c.to_dict() if isinstance(c, Predicate) else c for c in conditions])
