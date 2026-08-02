from expense_tracker import categories, db, ingest, queries


def test_message_is_categorised_and_stored_with_the_message_timestamp(conn):
    result = ingest.ingest_message(
        conn,
        member="Alice",
        text="haircut $300; dinner $50; MTR $5.6",
        timestamp="2026-07-19T21:30:00+08:00",
    )

    assert result["total"] == 355.6
    assert [i["category"] for i in result["items"]] == ["Beauty", "Food & Drinks", "Transportation"]
    assert result["timestamp"] == "2026-07-19T21:30:00"
    assert queries.month_summary(conn, "2026-07")["total"] == 355.6


def test_utc_timestamp_is_bucketed_into_the_local_day(conn):
    ingest.ingest_message(conn, member="Alice", text="taxi $90", timestamp="2026-07-31T17:10:00Z")

    assert queries.top_days(conn, "2026-08")[0]["day"] == "2026-08-01"


def test_unknown_keyword_is_flagged_then_backfilled_by_learn(conn):
    result = ingest.ingest_message(conn, member="Bob", text="poke bowl $128", timestamp="2026-07-02T12:00:00")

    assert result["unmapped"] == ["poke bowl"]
    assert result["items"][0]["category"] == categories.UNCATEGORIZED
    assert "valid_categories" in result

    categories.learn(conn, {"poke bowl": "Food & Drinks"})
    assert db.recategorize(conn) == [{"id": 1, "description": "poke bowl", "category": "Food & Drinks"}]
    assert db.unmapped(conn) == []


def test_learn_rejects_a_category_outside_the_closed_set(conn):
    outcome = categories.learn(conn, {"nft": "Crypto"})

    assert outcome["learned"] == []
    assert outcome["rejected"][0]["keyword"] == "nft"


def test_message_id_makes_ingestion_idempotent(conn):
    for _ in range(2):
        result = ingest.ingest_message(
            conn, member="Alice", text="dinner $50", timestamp="2026-07-02T20:00:00", message_id="tg:1"
        )

    assert result["items"] == []
    assert result["duplicates"] == ["dinner"]
    assert queries.month_summary(conn, "2026-07")["total"] == 50.0


def test_alias_resolves_a_handle_to_the_display_name(conn):
    db.set_alias(conn, "@alice_hk", "Alice")

    result = ingest.ingest_message(conn, member="@alice_hk", text="bus $4.8", timestamp="2026-07-02T09:00:00")

    assert result["member"] == "Alice"


def test_longest_keyword_wins(conn):
    categories.learn(conn, {"car": "Transportation", "car park": "Transportation"})
    mapping = categories.load_mapping(conn)

    assert categories.resolve(mapping, "monthly car park")[1] == "car park"
