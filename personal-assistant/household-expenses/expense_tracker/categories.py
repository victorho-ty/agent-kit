"""Canonical categories and the learnable keyword -> category mapping."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

# Order is meaningful: it is the fixed colour-slot order used by report.py.
# The chart palette carries eight distinct hues, so a ninth spending category
# would have to fold into "Other" (see README).
CATEGORIES = [
    "Food & Drinks",
    "Shopping",
    "Transportation",
    "Entertainment",
    "Beauty",
    "Health",
    "Housing & Utilities",
    "Education",
    "Other",
]

UNCATEGORIZED = "Uncategorized"

# Starter mapping. Everything beyond this is learned at runtime from the agent.
SEED_KEYWORDS: dict[str, str] = {
    # Food & Drinks
    "breakfast": "Food & Drinks",
    "brunch": "Food & Drinks",
    "lunch": "Food & Drinks",
    "dinner": "Food & Drinks",
    "supper": "Food & Drinks",
    "coffee": "Food & Drinks",
    "tea": "Food & Drinks",
    "snack": "Food & Drinks",
    "snacks": "Food & Drinks",
    "restaurant": "Food & Drinks",
    "takeaway": "Food & Drinks",
    "groceries": "Food & Drinks",
    "grocery": "Food & Drinks",
    "supermarket": "Food & Drinks",
    "bakery": "Food & Drinks",
    "cafe": "Food & Drinks",
    "starbucks": "Food & Drinks",
    "mcdonalds": "Food & Drinks",
    "beer": "Food & Drinks",
    "wine": "Food & Drinks",
    "drinks": "Food & Drinks",
    # Shopping
    "clothes": "Shopping",
    "clothing": "Shopping",
    "shirt": "Shopping",
    "shoes": "Shopping",
    "bag": "Shopping",
    "books": "Shopping",
    "book": "Shopping",
    "stationery": "Shopping",
    "electronics": "Shopping",
    "phone": "Shopping",
    "amazon": "Shopping",
    "taobao": "Shopping",
    "gift": "Shopping",
    "gifts": "Shopping",
    "household": "Shopping",
    # Transportation
    "bus": "Transportation",
    "minibus": "Transportation",
    "mtr": "Transportation",
    "subway": "Transportation",
    "metro": "Transportation",
    "train": "Transportation",
    "taxi": "Transportation",
    "uber": "Transportation",
    "ferry": "Transportation",
    "tram": "Transportation",
    "octopus": "Transportation",
    "petrol": "Transportation",
    "gas": "Transportation",
    "parking": "Transportation",
    "toll": "Transportation",
    "car park": "Transportation",
    # Entertainment
    "movie": "Entertainment",
    "movies": "Entertainment",
    "cinema": "Entertainment",
    "netflix": "Entertainment",
    "spotify": "Entertainment",
    "concert": "Entertainment",
    "game": "Entertainment",
    "games": "Entertainment",
    "karaoke": "Entertainment",
    "bar": "Entertainment",
    "museum": "Entertainment",
    # Beauty
    "haircut": "Beauty",
    "hair": "Beauty",
    "salon": "Beauty",
    "barber": "Beauty",
    "nails": "Beauty",
    "manicure": "Beauty",
    "facial": "Beauty",
    "makeup": "Beauty",
    "cosmetics": "Beauty",
    "skincare": "Beauty",
    "spa": "Beauty",
    "massage": "Beauty",
    # Health
    "doctor": "Health",
    "clinic": "Health",
    "hospital": "Health",
    "dentist": "Health",
    "medicine": "Health",
    "pharmacy": "Health",
    "vitamins": "Health",
    "gym": "Health",
    "insurance": "Health",
    "optician": "Health",
    "glasses": "Health",
    # Housing & Utilities
    "rent": "Housing & Utilities",
    "electricity": "Housing & Utilities",
    "water": "Housing & Utilities",
    "internet": "Housing & Utilities",
    "broadband": "Housing & Utilities",
    "mobile": "Housing & Utilities",
    "management fee": "Housing & Utilities",
    "cleaning": "Housing & Utilities",
    "repair": "Housing & Utilities",
    "furniture": "Housing & Utilities",
    # Education
    "tuition": "Education",
    "school": "Education",
    "course": "Education",
    "textbook": "Education",
    "workshop": "Education",
    # Other
    "flight": "Other",
    "hotel": "Other",
    "airbnb": "Other",
    "donation": "Other",
    "bank fee": "Other",
}

_NON_WORD = re.compile(r"[^0-9a-z一-鿿]+")


def normalize(text: str) -> str:
    """Lowercase and strip punctuation so lookups are stable."""
    return _NON_WORD.sub(" ", text.lower()).strip()


def is_valid(category: str) -> bool:
    return category in CATEGORIES


def seed(conn: sqlite3.Connection) -> int:
    """Insert the starter mapping. Never overwrites a learned entry."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(normalize(k), v, "seed", now, now) for k, v in SEED_KEYWORDS.items()]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO keyword_category (keyword, category, source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return cur.rowcount


def load_mapping(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["keyword"]: row["category"] for row in conn.execute("SELECT keyword, category FROM keyword_category")}


def resolve(mapping: dict[str, str], description: str) -> tuple[str | None, str]:
    """Map a free-text item to a category.

    Returns (category or None, matched-or-normalized keyword). Longest keyword
    wins so "car park" beats "car".
    """
    norm = normalize(description)
    if not norm:
        return None, norm
    if norm in mapping:
        return mapping[norm], norm

    padded = f" {norm} "
    best = ""
    for keyword in mapping:
        if len(keyword) > len(best) and f" {keyword} " in padded:
            best = keyword
    if best:
        return mapping[best], best
    return None, norm


def learn(conn: sqlite3.Connection, pairs: dict[str, str], source: str = "llm") -> dict:
    """Persist keyword -> category decisions made by the agent."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    learned, rejected = [], []
    for raw_keyword, category in pairs.items():
        keyword = normalize(raw_keyword)
        if not keyword:
            rejected.append({"keyword": raw_keyword, "reason": "empty after normalization"})
            continue
        if not is_valid(category):
            rejected.append({"keyword": raw_keyword, "reason": f"unknown category '{category}'"})
            continue
        conn.execute(
            "INSERT INTO keyword_category (keyword, category, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(keyword) DO UPDATE SET category=excluded.category, source=excluded.source, updated_at=excluded.updated_at",
            (keyword, category, source, now, now),
        )
        learned.append({"keyword": keyword, "category": category})
    conn.commit()
    return {"learned": learned, "rejected": rejected}


def bump_hits(conn: sqlite3.Connection, keywords: list[str]) -> None:
    conn.executemany("UPDATE keyword_category SET hits = hits + 1 WHERE keyword = ?", [(k,) for k in keywords])
    conn.commit()
