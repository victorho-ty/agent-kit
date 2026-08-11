"""The digest: sections, clustering inside them, and the ledger.

The ledger tests are the ones that matter most. `scan` and `digest` run on
separate cron entries at unrelated cadences, and the only thing making that safe
is that "since the last digest" is a column, not a time range.
"""

from __future__ import annotations

import pytest

from news_radar import db, digest, scan

from .conftest import (ALPHA, ALPHA_URL, BETA, GAMMA, LATER, MUCH_LATER, NOW,
                       fixture)


def scan_once(conn, config, web, now):
    return scan.scan(conn, config, config.select(), now,
                     fetcher=web.get, sleeper=lambda _: None)


@pytest.fixture
def three(make_config):
    return make_config(sources=[ALPHA, BETA, GAMMA])


@pytest.fixture
def pending(conn, three, web):
    """Seeded, then everything re-published so there is a real backlog."""
    scan_once(conn, three, web, NOW)                      # cold start, silent
    for url, name in ((ALPHA_URL, "alpha.xml"),):
        web.pages[url] = fixture(name).replace("<item>", "<item><title>Second wave story</title>"
                                               "<link>https://alpha.example.com/2026/08/second</link>"
                                               "<description>More.</description></item><item>", 1)
    scan_once(conn, three, web, LATER)
    return three


# --------------------------------------------------------------------------- sections


def test_sections_follow_the_config_order(conn, three, web):
    """Most-important-first is the point; alphabetical would bury it."""
    web.etags.clear()
    scan.scan(conn, three, three.select(), NOW, fetcher=web.get,
              sleeper=lambda _: None, seed=False)
    # force everything pending by clearing the seeding stamp
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    payload = digest.build(conn, three, LATER)

    assert [section["category"] for section in payload["sections"]] == ["ai", "world"]
    assert [section["label"] for section in payload["sections"]] == ["AI", "World"]


def test_a_category_with_nothing_new_is_omitted(conn, make_config, web):
    config = make_config(sources=[ALPHA])              # ai only; world has no source
    scan_once(conn, config, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    payload = digest.build(conn, config, LATER)

    assert [section["category"] for section in payload["sections"]] == ["ai"]


def test_one_story_from_two_outlets_is_one_line_with_both_domains(conn, three, web):
    """The whole reason clustering exists."""
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    payload = digest.build(conn, three, LATER)
    ai = next(section for section in payload["sections"] if section["category"] == "ai")
    gpt = next(story for story in ai["stories"] if "GPT-X" in story["title"])

    assert gpt["sources"] == ["alpha.example.com", "beta.example.org"]
    assert gpt["url"] == "https://alpha.example.com/2026/08/openai-releases-gpt-x"


def test_clustering_does_not_reach_across_sections(conn, three, web):
    """Gamma carries the same story in a different category.

    Merging across would force an arbitrary choice about which section loses it,
    so it appears in both -- once each.
    """
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    payload = digest.build(conn, three, LATER)
    by_category = {section["category"]: section for section in payload["sections"]}

    assert any("GPT-X" in story["title"] for story in by_category["ai"]["stories"])
    assert any("GPT-X" in story["title"] for story in by_category["world"]["stories"])


# --------------------------------------------------------------------------- the ledger


def test_commit_is_idempotent(conn, pending):
    """A second digest must not re-announce what the first one carried."""
    first = digest.build(conn, pending, MUCH_LATER, commit=True)
    second = digest.build(conn, pending, MUCH_LATER, commit=True)

    assert first["count"] > 0 and first["committed"] == first["count"]
    assert second["count"] == 0
    assert second["sections"] == []


def test_reading_without_commit_changes_nothing(conn, pending):
    """The on-demand peek must not eat what the scheduled digest would carry."""
    digest.build(conn, pending, MUCH_LATER)
    assert digest.build(conn, pending, MUCH_LATER)["count"] > 0


def test_a_digest_covers_every_scan_since_the_last_one(conn, three, web):
    """Three scans, one digest: the digest is defined by the ledger, not a window.

    This is what lets the two cron entries run at unrelated cadences.
    """
    # Deliberately unrelated headlines: two stories that happen to arrive in
    # different scans must survive as two, not be folded together.
    arrivals = [
        (LATER, "Quantum chip milestone reached", "quantum"),
        (MUCH_LATER, "Undersea cable severed near Taiwan", "cable"),
    ]
    scan_once(conn, three, web, NOW)                     # seeding, silent
    for moment, title, slug in arrivals:
        web.pages[ALPHA_URL] = fixture("alpha.xml").replace(
            "<item>", f"<item><title>{title}</title>"
            f"<link>https://alpha.example.com/2026/08/{slug}</link>"
            f"<description>x</description></item><item>", 1)
        scan_once(conn, three, web, moment)

    payload = digest.build(conn, three, MUCH_LATER, commit=True)
    titles = [story["title"] for section in payload["sections"] for story in section["stories"]]

    assert "Quantum chip milestone reached" in titles
    assert "Undersea cable severed near Taiwan" in titles


def test_a_missed_digest_only_makes_the_next_one_longer(conn, pending):
    """Nothing is lost by not digesting; the backlog simply waits."""
    assert digest.build(conn, pending, MUCH_LATER)["count"] == \
        digest.build(conn, pending, MUCH_LATER, commit=True)["count"]


# --------------------------------------------------------------------------- recategorising


def test_moving_a_source_moves_its_pending_items(conn, three, web, make_config):
    """Category is read from the config now, not stored on the item, so an edit
    applies to everything that has not gone out yet."""
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    moved = make_config(sources=[{**ALPHA, "category": "world"}, BETA, GAMMA])
    payload = digest.build(conn, moved, LATER)
    world = next(section for section in payload["sections"] if section["category"] == "world")

    assert any(story["sources"] == ["alpha.example.com"] or
               "alpha.example.com" in story["sources"] for story in world["stories"])


def test_a_deleted_source_surfaces_rather_than_vanishing(conn, three, web, make_config):
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    shrunk = make_config(sources=[BETA, GAMMA])          # alpha removed entirely
    payload = digest.build(conn, shrunk, LATER)
    orphans = [s for s in payload["sections"] if s["category"] == "uncategorised"]

    assert orphans and "no longer in sources.json" in orphans[0]["note"]
    assert payload["sections"][-1]["category"] == "uncategorised"   # last, not first


# --------------------------------------------------------------------------- the message


def test_the_body_carries_the_domains_and_a_real_link(conn, three, web):
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    body = digest.format_digest(digest.build(conn, three, LATER))

    assert "AI" in body and "World" in body
    assert "alpha.example.com · beta.example.org" in body
    assert "https://alpha.example.com/2026/08/openai-releases-gpt-x" in body


def test_an_empty_digest_produces_no_message_at_all(conn, three):
    payload = digest.build(conn, three, NOW)
    assert payload["count"] == 0
    assert digest.format_digest(payload) == ""


def test_filtering_by_category_narrows_the_digest(conn, three, web):
    scan_once(conn, three, web, NOW)
    conn.execute("UPDATE item SET digested_at = NULL")
    conn.commit()

    payload = digest.build(conn, three, LATER, categories=["world"])

    assert [section["category"] for section in payload["sections"]] == ["world"]
