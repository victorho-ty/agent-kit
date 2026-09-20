from expense_tracker.parser import parse_message


def test_parses_semicolon_separated_message():
    items, ignored = parse_message("haircut $300; dinner $50; Bus $4.8; MTR $5.6; Books $150")

    assert [(i.description, i.amount) for i in items] == [
        ("haircut", 300.0),
        ("dinner", 50.0),
        ("Bus", 4.8),
        ("MTR", 5.6),
        ("Books", 150.0),
    ]
    assert ignored == []


def test_comma_separates_items_but_not_thousands():
    items, _ = parse_message("rent $12,500, groceries $340")

    assert [(i.description, i.amount) for i in items] == [("rent", 12500.0), ("groceries", 340.0)]


def test_currency_marked_number_wins_over_a_count():
    items, _ = parse_message("2 coffees $96")

    assert items[0].description == "2 coffees"
    assert items[0].amount == 96.0


def test_amount_before_description_and_noise_words_stripped():
    items, _ = parse_message("paid $88 for taxi")

    assert items[0].description == "taxi"
    assert items[0].amount == 88.0


def test_chunk_without_an_amount_is_ignored():
    items, ignored = parse_message("dinner $50\nremember to buy milk")

    assert len(items) == 1
    assert ignored == ["remember to buy milk"]
