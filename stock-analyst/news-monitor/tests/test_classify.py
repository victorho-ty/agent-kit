"""The hints, and the word boundaries that keep them from being nonsense."""

from __future__ import annotations

from news_monitor.classify import classify


def test_headline_gets_its_sector_and_its_event_kind(taxonomy):
    sectors, signals = classify(
        "Fed holds rates steady, signals one cut this year",
        "The FOMC left the federal funds target unchanged.",
        taxonomy,
    )
    assert "macro-rates" in sectors
    assert "policy-decision" in signals


def test_summary_is_read_as_well_as_the_title(taxonomy):
    sectors, _ = classify(
        "Chipmaker lifts outlook",
        "The semiconductor group said data center demand was strong.",
        taxonomy,
    )
    assert "semiconductors" in sectors
    assert "technology" in sectors


def test_a_term_inside_a_longer_word_does_not_match(taxonomy):
    """'ism' inside 'mechanism' would bucket half the wire into industrials."""
    sectors, _ = classify("A new pricing mechanism for carbon", None, taxonomy)
    assert "industrials" not in sectors


def test_terms_with_punctuation_still_match(taxonomy):
    _, signals = classify("Retailer files for Chapter 11", None, taxonomy)
    assert "corporate-action" in signals


def test_an_item_with_no_matches_gets_empty_lists_not_a_guess(taxonomy):
    sectors, signals = classify("Five ways to overwinter your tomato plants", None, taxonomy)
    assert sectors == []
    assert signals == []


def test_exclude_matches_the_term_as_a_word_stem(taxonomy):
    assert taxonomy.excluded_by("Australian dollar slides after RBA decision") == "australia"
    assert taxonomy.excluded_by("Taiwanese exporters brace for tariffs") == "taiwan"
    assert taxonomy.excluded_by("TAIEX closes at a record") == "taiex"


def test_exclude_does_not_match_a_neighbouring_word(taxonomy):
    assert taxonomy.excluded_by("Austria's central bank holds rates") is None
    assert taxonomy.excluded_by("Fed holds rates steady") is None


def test_finance_hits_names_the_terms_it_found(taxonomy):
    hits = taxonomy.finance_hits("Bond investors watch the Federal Reserve")
    assert "bond" in hits
    assert "federal reserve" in hits
