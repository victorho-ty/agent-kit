"""Turn one inbound Telegram message into persisted expense rows."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import categories, config, db, parser


def parse_timestamp(value: str | None) -> tuple[str, str]:
    """Accept an ISO8601 string or a unix epoch; return (local ISO, UTC ISO).

    The local value is what every month/day bucket is computed from, so a
    message sent at 00:30 HKT lands on the right day.
    """
    tz = config.timezone()
    if value is None or value == "":
        moment = datetime.now(tz)
    elif value.isdigit():
        moment = datetime.fromtimestamp(int(value), tz)
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    local = moment.astimezone(tz)
    return (
        local.replace(tzinfo=None).isoformat(timespec="seconds"),
        moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def ingest_message(
    conn: sqlite3.Connection,
    *,
    member: str,
    text: str,
    timestamp: str | None = None,
    message_id: str | None = None,
    currency: str | None = None,
) -> dict:
    """Parse, categorise and store every expense item in the message.

    Items whose keyword is not yet mapped are stored as Uncategorized and
    returned under "unmapped" so the agent can classify and `learn` them.
    """
    canonical = db.resolve_member(conn, member)
    ts_local, ts_utc = parse_timestamp(timestamp)
    currency = currency or config.currency()

    items, ignored = parser.parse_message(text)
    mapping = categories.load_mapping(conn)

    stored, duplicates, unmapped, matched_keywords = [], [], [], []
    for index, item in enumerate(items):
        category, keyword = categories.resolve(mapping, item.description)
        if category is None:
            category = categories.UNCATEGORIZED
            unmapped.append(item.description)
        else:
            matched_keywords.append(keyword)

        row_id = db.insert_expense(
            conn,
            member=canonical,
            description=item.description,
            keyword=keyword,
            category=category,
            amount=item.amount,
            currency=currency,
            ts_local=ts_local,
            ts_utc=ts_utc,
            message_id=message_id,
            item_index=index,
            source_text=item.raw,
        )
        if row_id is None:
            duplicates.append(item.description)
            continue
        stored.append(
            {
                "id": row_id,
                "description": item.description,
                "amount": round(item.amount, 2),
                "category": category,
            }
        )

    conn.commit()
    categories.bump_hits(conn, matched_keywords)

    result = {
        "member": canonical,
        "timestamp": ts_local,
        "currency": currency,
        "items": stored,
        "total": round(sum(row["amount"] for row in stored), 2),
        "unmapped": unmapped,
        "duplicates": duplicates,
        "ignored": ignored,
    }
    if unmapped:
        result["valid_categories"] = categories.CATEGORIES
    return result
