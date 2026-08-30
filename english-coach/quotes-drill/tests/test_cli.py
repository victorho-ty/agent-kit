from __future__ import annotations

import json

from quotes_drill import cli


def run(capsys, db_path, *argv):
    """Run one command the way the agent does, and parse its single object."""
    code = cli.main(["--db", str(db_path), *argv])
    return code, json.loads(capsys.readouterr().out)


def test_a_drill_against_an_empty_store_says_so_with_its_own_code(capsys, db_path):
    code, payload = run(capsys, db_path, "next")

    assert code == 31
    assert payload["code"] == "ERR_NO_ENTRIES"
    assert payload["ok"] is False


def test_next_hands_over_a_style_and_writes_nothing(capsys, db_path):
    run(capsys, db_path, "add", "--text", "the flavour is unbelievable", "--category", "Food")

    _, first = run(capsys, db_path, "--now", "2026-08-30T09:00:00+08:00", "next")
    _, second = run(capsys, db_path, "--now", "2026-08-30T09:05:00+08:00", "next")

    item = first["items"][0]
    assert item["reason"] == "never_tested"
    assert item["style"]["name"] == "Mark Wiens"
    assert item["style"]["source"] == "category"
    assert item["last_attempt"] is None
    # An unanswered drill costs the entry nothing: it is still untested.
    assert second["items"][0]["entry"]["times_tested"] == 0


def test_a_recorded_drill_moves_on_to_the_next_entry(capsys, db_path):
    run(capsys, db_path, "add", "--text", "tuck in", "--category", "Food")
    run(capsys, db_path, "add", "--text", "ship it", "--category", "Computer")

    _, drill = run(capsys, db_path, "--now", "2026-08-30T09:00:00+08:00", "next")
    entry_id = drill["items"][0]["entry"]["id"]

    code, recorded = run(
        capsys,
        db_path,
        "--now",
        "2026-08-30T09:02:00+08:00",
        "record",
        "--entry",
        str(entry_id),
        "--score",
        "4",
        "--transcript",
        "we tucked in before the rain started",
        "--feedback",
        "natural, and the past tense held up",
        "--error-kind",
        "none",
        "--style",
        "Mark Wiens",
    )

    assert code == 0
    assert recorded["interval_days"] == 2
    assert recorded["entry"]["times_tested"] == 1

    _, after = run(capsys, db_path, "--now", "2026-08-30T09:03:00+08:00", "next")
    assert after["items"][0]["entry"]["id"] != entry_id
    assert after["pool"]["cooling"] == 1


def test_an_unknown_category_falls_back_to_the_general_styles(capsys, db_path):
    run(capsys, db_path, "add", "--text", "a fair few", "--category", "Weather")

    _, drill = run(capsys, db_path, "next")

    assert drill["items"][0]["style"]["source"] == "default"


def test_one_bad_item_rejects_the_whole_import(capsys, db_path, tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            [
                {"text": "tuck in", "category": "Food", "kind": "phrase"},
                {"text": "ship it", "kind": "quote"},
            ]
        ),
        encoding="utf-8",
    )

    code, payload = run(capsys, db_path, "import", "--file", str(batch))
    assert code == 10
    assert payload["details"]["index"] == 1

    code, listing = run(capsys, db_path, "list")
    assert listing["count"] == 0


def test_import_stores_the_batch_and_reports_duplicates(capsys, db_path, tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "entries": [
                    {"text": "tuck in", "category": "Food", "kind": "phrase"},
                    {"text": "Tuck in!", "category": "Food"},
                ]
            }
        ),
        encoding="utf-8",
    )

    code, payload = run(capsys, db_path, "import", "--file", str(batch))

    assert code == 0
    assert (payload["added"], payload["duplicates"]) == (1, 1)


def test_stats_reports_the_facts_a_session_can_quote(capsys, db_path):
    run(capsys, db_path, "add", "--text", "tuck in", "--category", "Food")
    run(capsys, db_path, "add", "--text", "ship it", "--category", "Computer")
    for day, score in (("29", 1), ("30", 2)):
        run(
            capsys, db_path, "--now", f"2026-08-{day}T09:00:00+08:00",
            "record", "--entry", "1", "--score", str(score),
        )

    _, payload = run(capsys, db_path, "--now", "2026-08-30T21:00:00+08:00", "stats")

    # Only the untouched entry is due: a 2 this morning puts the other one
    # back on the bottom rung, which is tomorrow.
    assert payload["entries"] == {
        "active": 2, "retired": 0, "never_tested": 1, "due_now": 1
    }
    assert payload["day_streak"] == 2
    assert payload["attempts"]["mean_score_last_20"] == 1.5
    assert payload["weakest"][0]["id"] == 1
