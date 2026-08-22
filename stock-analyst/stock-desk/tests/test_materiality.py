"""The classifier is the thing standing between two noisy feeds and the report.

Every headline in here is real, or a lightly anonymised copy of one that
arrived during development. The ugly cases are the point: a matcher that only
sees well-formed news is a matcher that has never met a syndication feed.
"""

from __future__ import annotations

import pytest

from stock_desk.materiality import (
    HIGH_BAND,
    MEDIUM_BAND,
    PRICE_MOVE_CEILING,
    assess,
    band,
    classify,
)

WIRE = ("Reuters",)


class TestClassification:
    @pytest.mark.parametrize(
        "headline,expected",
        [
            ("Acme beats Q3 estimates on cloud strength", "earnings"),
            ("Acme raises its guidance for 2027", "guidance"),
            ("Nvidia cuts full-year outlook", "guidance"),
            ("Beta Corp acquires Gamma for $4bn", "ma"),
            ("Nvidia discloses $21 billion stake in SpaceX", "ma"),
            ("SEC opens probe into Acme accounting", "legal_regulatory"),
            ("US expands export controls on AI chips", "legal_regulatory"),
            ("Acme wins $2bn defence contract", "contract"),
            ("Acme announces $10bn buyback", "capital"),
            ("Acme CFO steps down after four years", "leadership"),
            ("Cerebras launches next-gen inference chip", "product"),
            ("Analysts raise price target on Microsoft", "analyst"),
            ("Nvidia stock rises as it bets on robots", "price_move"),
        ],
    )
    def test_headlines_land_in_the_right_class(self, headline, expected):
        assert classify(headline) == expected

    @pytest.mark.parametrize(
        "headline",
        [
            "3 AI Stocks to Buy Now",
            "Better Buy: Nvidia vs AMD",
            "Prediction: Where Will Nvidia Be In 5 Years?",
            "Should You Buy Nvidia Stock Before August?",
            "This Billionaire Just Sold Nvidia",
        ],
    )
    def test_listicles_and_advice_pieces_are_noise(self, headline):
        assert classify(headline) == "noise"

    @pytest.mark.parametrize(
        "headline",
        [
            "Acme Corp Shares Sold by Vanguard Group Inc",
            "ASML Holding N.V. $ASML Shares Acquired by DSM Capital Partners LLC",
            "Beta Fund Boosts Its Stake in Nvidia",
            "Gamma Advisors Takes a New Position in Microsoft",
            "Short Interest Update on Acme Corp",
            "Acme Sets New 52-Week High",
        ],
    )
    def test_ownership_churn_is_noise_not_an_acquisition(self, headline):
        assert classify(headline) == "noise"

    @pytest.mark.parametrize(
        "headline",
        [
            "Nvidia earnings preview: What's next for Nvidia stock",
            "Countdown to Lam Research (LRCX) Q4 Earnings",
            "Nvidia Nears Buy Point Ahead Of Fiscal Q2 Earnings",
            "Weekly Recap: AI accelerator demand",
            "TEAM Q2 Deep Dive: AI Acceleration and Enterprise Expansion",
            "KLA Corporation (KLAC) Stock Forecasts",
        ],
    )
    def test_previews_and_recaps_are_noise(self, headline):
        assert classify(headline) == "noise"

    @pytest.mark.parametrize(
        "headline",
        [
            "Semiconductor Market Size to Reach $1.2 Trillion",
            "Global AI Accelerator Market Report 2026",
            "$30+ Billion Extreme Ultraviolet (EUV) Lithography Market Forecast to 2032",
            "Inference Chip Market Analysis and CAGR Outlook",
        ],
    )
    def test_market_research_press_releases_are_noise_not_guidance(self, headline):
        assert classify(headline) == "noise"

    def test_noise_wins_over_a_real_event_mentioned_inside_it(self):
        # There is a genuine earnings beat in here. It is still an advice column,
        # and ordering event patterns first would classify most listicles as news.
        assert classify("Better Buy: Nvidia vs AMD After Nvidia's Earnings Beat") == "noise"

    def test_actual_results_are_not_confused_with_a_preview(self):
        assert classify("Nvidia earnings preview: what to expect") == "noise"
        assert classify("Nvidia reports Q2 revenue of $30 billion") == "earnings"

    def test_an_unmatched_headline_is_unclassified_not_noise(self):
        # An unrecognised shape on an unfamiliar issuer is a reason to look,
        # not a reason to discard.
        verdict = assess("Acme opens its Guangzhou facility", WIRE)
        assert verdict.event_class == "unclassified"
        assert not verdict.is_noise

    def test_an_empty_headline_is_noise(self):
        assert classify("") == "noise"
        assert classify("   ") == "noise"


class TestBands:
    def test_bands_follow_the_thresholds(self):
        assert band(HIGH_BAND) == "high"
        assert band(HIGH_BAND - 1) == "medium"
        assert band(MEDIUM_BAND) == "medium"
        assert band(MEDIUM_BAND - 1) == "low"

    def test_a_bare_price_move_never_reaches_high_on_its_own(self):
        # Everything that can lift a score, all at once, on a story that is only
        # the market reacting to something else.
        verdict = assess(
            "Nvidia stock rises as it bets on robots",
            ("Reuters", "Bloomberg", "CNBC", "Barron's"),
            held=True,
        )
        assert verdict.event_class == "price_move"
        assert verdict.score <= PRICE_MOVE_CEILING
        assert verdict.band != "high"


class TestScoring:
    def test_guidance_clears_the_high_band(self):
        verdict = assess("Acme raises its guidance for 2027", ("Reuters", "Bloomberg"))
        assert verdict.band == "high"

    def test_a_listicle_lands_in_the_low_band(self):
        verdict = assess("3 AI Stocks to Buy Now", ("Reuters", "Bloomberg"), held=True)
        assert verdict.band == "low"
        assert verdict.score == 0

    def test_a_wire_lifts_the_score(self):
        bare = assess("Acme wins $2bn defence contract", ("Modern Readers",))
        wired = assess("Acme wins $2bn defence contract", ("Reuters",))
        assert wired.score > bare.score

    def test_corroboration_by_four_outlets_adds_more_than_by_two(self):
        two = assess("Acme wins $2bn defence contract", ("Reuters", "Bloomberg"))
        four = assess(
            "Acme wins $2bn defence contract",
            ("Reuters", "Bloomberg", "CNBC", "Financial Times"),
        )
        assert four.score > two.score

    def test_an_all_aggregator_story_is_marked_down(self):
        farmed = assess("Acme wins $2bn defence contract", ("MarketBeat", "Zacks"))
        reported = assess("Acme wins $2bn defence contract", ("Reuters", "Bloomberg"))
        assert farmed.score < reported.score
        assert any("aggregators only" in r for r in farmed.reason)

    def test_one_aggregator_alongside_a_wire_is_not_marked_down(self):
        mixed = assess("Acme wins $2bn defence contract", ("Reuters", "MarketBeat"))
        assert not any("aggregators only" in r for r in mixed.reason)

    def test_barrons_matches_whichever_way_the_apostrophe_goes(self):
        curly = assess("Acme wins $2bn defence contract", ("Barron’s",))
        straight = assess("Acme wins $2bn defence contract", ("Barron's",))
        assert curly.score == straight.score
        assert any("wire" in r for r in curly.reason)

    def test_a_held_position_scores_above_a_watched_one(self):
        watched = assess("Acme wins $2bn defence contract", WIRE)
        held = assess("Acme wins $2bn defence contract", WIRE, held=True)
        assert held.score > watched.score

    def test_a_peer_story_scores_below_the_same_story_about_the_position(self):
        own = assess("Acme wins $2bn defence contract", WIRE)
        peer = assess("Acme wins $2bn defence contract", WIRE, peer_of="AMD")
        assert peer.score < own.score
        assert any("AMD" in r for r in peer.reason)

    def test_the_reason_names_every_contributing_factor(self):
        verdict = assess(
            "Acme raises its guidance for 2027",
            ("Reuters", "Bloomberg"),
            held=True,
            peer_of="AMD",
        )
        joined = " | ".join(verdict.reason)
        assert "guidance" in joined
        assert "wire" in joined
        assert "outlets" in joined
        assert "open position" in joined
        assert "AMD" in joined

    def test_the_score_is_bounded(self):
        loaded = assess(
            "Acme raises its guidance for 2027",
            ("Reuters", "Bloomberg", "CNBC", "Financial Times", "Barron's"),
            held=True,
        )
        assert 0 <= loaded.score <= 100
        floored = assess(
            "Analysts raise price target on Microsoft",
            ("MarketBeat",),
            peer_of="AMD",
        )
        assert floored.score >= 0

    def test_no_sources_still_scores(self):
        # Alpha Vantage sometimes returns an item with an empty source string.
        verdict = assess("Acme raises its guidance for 2027", ())
        assert verdict.score > 0


class TestLiveFeedRegressions:
    """Headlines taken verbatim from a live Alpha Vantage NVDA query.

    All fifty items in that response scored between 0.52 and 1.00 on the
    vendor's own ``relevance_score``, so every one of these arrived looking
    like news about Nvidia. Two of the fifty were.
    """

    @pytest.mark.parametrize(
        "headline",
        [
            # Verb-before-noun ordering, which the first pass missed entirely.
            "NVIDIA Corporation $NVDA Stake Increased by Paladin Wealth LLC",
            "Balyasny Asset Management L.P. Exits Rallybio Corp (RLYB) Stake",
            "Balyasny Asset Management L.P. Trims Talkspace Inc (TALK) Stake",
            "Paralel Advisors LLC Makes New Investment in Gen Digital Inc.",
            "Oppenheimer & Co. Inc. Buys 6,702 Shares of Micron Technology",
            "Vise Technologies Inc. Takes $19.11 Million Position in Celestica",
        ],
    )
    def test_thirteen_f_churn_in_every_phrasing_is_noise(self, headline):
        assert classify(headline) == "noise"

    def test_an_executive_family_profile_is_noise(self):
        # Scored 1.00 relevance by the vendor. Changes nothing about the stock.
        assert (
            classify(
                "Meet Madison Huang, daughter of multibillionaire Nvidia CEO Jensen Huang"
            )
            == "noise"
        )

    @pytest.mark.parametrize(
        "headline",
        [
            "Does NVIDIA Corporation (NVDA)'s AI Dominance Leave Any Room for Rivals?",
            "Is Blackstone (BX) Quietly Redefining Its Investment Story?",
            "Which Streaming Stock Would Hold Up Better in a Recession?",
        ],
    )
    def test_opinion_columns_opening_with_a_question_are_noise(self, headline):
        assert classify(headline) == "noise"

    def test_the_question_rule_also_swallows_price_explainers(self):
        # A deliberate trade: "Why Nvidia fell 5%" is an explainer built on a
        # price move, and price moves are already the least actionable class.
        # Losing them costs nothing and buys a clean rule.
        assert classify("Why Nvidia Stock Fell 5% Today") == "noise"

    def test_a_long_gap_between_talks_and_deal_still_reads_as_ma(self):
        # 43 characters separate "in Talks" from "Deal" here; the first window
        # was 40 and the story silently became unclassified.
        verdict = assess(
            "Nvidia in Talks With Chip Startup Rebellions for Potential Deal",
            ("Bloomberg",),
        )
        assert verdict.event_class == "ma"
        assert verdict.band in {"medium", "high"}

    def test_a_declarative_competitor_headline_survives(self):
        # The one genuine competitive story in the sample. Not a recognised
        # shape, and that is exactly why unclassified must not mean noise.
        verdict = assess(
            "Broadcom Steps up NVIDIA Challenge With Potential $100 Billion AI Deal",
            ("Reuters",),
        )
        assert not verdict.is_noise


class TestSecondPassRegressions:
    """Gaps found by replaying a live two-feed poll over the real watchlist."""

    def test_a_holding_phrased_as_a_position_is_still_churn(self):
        assert classify("BDF Gestion Has $49.05 Million Stock Position in Microsoft $MSFT") == "noise"

    def test_speculative_price_columns_are_noise(self):
        assert classify("An event on 6 August could send the SpaceX share price below $100") == "noise"

    def test_multi_period_price_commentary_is_a_price_move(self):
        assert classify("AMD Has More Than Tripled Off Its Low and Still Trades 20% Below Its High") == "price_move"

    def test_officer_disposals_are_insider_dealing_not_a_personnel_note(self):
        # Form 4 is the only place this appears, and for a swing trade a CTO
        # selling $153M is a fact about supply, not about the org chart.
        assert (
            classify("Cerebras CTO Files To Sell Over $153M Stock, Senior Executives Offload Small Lots")
            == "insider"
        )

    def test_insider_dealing_outranks_an_analyst_note(self):
        insider = assess("Cerebras CTO files to sell over $153M stock", ("Reuters",))
        analyst = assess("Analysts raise price target on Cerebras", ("Reuters",))
        assert insider.score > analyst.score

    def test_a_lockup_expiry_is_capital_not_noise(self):
        # Supply arriving on a known date is the whole story for a swing trade.
        assert (
            classify("Certain Class A Common Stock of Space Exploration Technologies are subject to a Lock-up")
            == "capital"
        )

    def test_a_production_win_is_a_contract_whatever_noun_the_wire_chose(self):
        assert classify("D Wave Quantum (QBTS) Lands Second Production Telecom Application") == "contract"

    def test_institutional_churn_and_insider_dealing_stay_distinct(self):
        assert classify("Vanguard Group Inc Boosts Its Stake in Nvidia") == "noise"
        assert classify("Nvidia CFO sold 50,000 shares last week") == "insider"


class TestSuppressionNearMisses:
    """Events that fell through as unclassified on a live CBRS poll.

    With `unclassified` suppressed these did not merely rank low, they vanished
    -- which is the standing cost of that policy and the reason each of these is
    pinned here rather than left to a threshold.
    """

    def test_unveiling_silicon_is_a_product_launch(self):
        # The flagship event of a chip company's year.
        assert classify("Cerebras Systems (CBRS) Unveils CS 4 AI Accelerator") == "product"

    @pytest.mark.parametrize(
        "headline",
        [
            "Advanced Micro Devices (AMD) Posts Record Sales, But Margins Slip",
            "Acme posts Q3 revenue of $4.2bn",
            "Acme delivers record quarter",
        ],
    )
    def test_results_in_every_house_style_are_earnings(self, headline):
        assert classify(headline) == "earnings"

    def test_a_product_launch_still_loses_to_a_listicle_wrapper(self):
        # Noise ordering is unchanged: an advice column about a launch is still
        # an advice column.
        assert classify("Better Buy After Cerebras Unveils Its CS-4 Accelerator?") == "noise"
