"""Is this headline an event, or is it filler?

Both feeds need this and neither provides it.

Alpha Vantage ships a ``relevance_score`` that looks like the answer and is not.
Measured against a live NVDA query: every one of fifty items scored above 0.52,
and the 1.00 bucket held "Meet Madison Huang, daughter of multibillionaire Nvidia
CEO Jensen Huang" alongside "NVIDIA Corporation $NVDA Stake Increased by Paladin
Wealth LLC" -- gossip and routine 13F churn, both maximally *relevant* to NVDA
and both worth nothing. The vendor labelled that second one **Bullish**, which is
the whole argument for classifying before trusting a sentiment score rather than
after. Yahoo's per-symbol feed carries the same freight under different bylines.

So relevance is not materiality. Relevance asks "is this about the company";
materiality asks "does this change anything". Only the second one is worth
waking somebody for.

## Noise is tested first, and wins

``test_noise_wins_over_a_real_event_mentioned_inside_it``: "Better Buy: Nvidia
vs AMD After Nvidia's Earnings Beat" contains a genuine earnings event and is
still an advice column. Every noise pattern is therefore checked before any
event pattern, and a match ends the classification. The reverse order finds an
event in almost every listicle ever written, because listicles are *about*
events -- that is what makes them listicles rather than news.

## The score ranks, it does not measure

0-100, used to order a morning's stories and to cut a tail that would not be
read. It is not a probability and not a magnitude. Two stories a point apart are
indistinguishable; that is why :func:`band` exists and why the report branches
on the band rather than the number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------- classes

# What kind of event a headline reports, ordered by how much it can move a
# swing thesis. `unclassified` is not noise: it is a headline whose shape this
# module does not recognise, which on an unfamiliar issuer is common and is a
# reason to look, not to discard.
CLASS_WEIGHTS: dict[str, int] = {
    "guidance": 55,
    "earnings": 50,
    "ma": 48,
    "legal_regulatory": 45,
    "contract": 40,
    "capital": 38,
    "leadership": 33,
    "insider": 35,
    "product": 30,
    "analyst": 22,
    "price_move": 10,
    "unclassified": 20,
    "noise": 0,
}

HIGH_BAND = 65
MEDIUM_BAND = 35

# A bare price move is never worth a paragraph on its own -- "stock rises as it
# bets on robots" is the market reacting to something, not the something. Capped
# below the high band however many outlets carry it, because every outlet
# carries it.
PRICE_MOVE_CEILING = HIGH_BAND - 1


def _p(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


# Checked first, in this order. Each entry is (name, pattern).
NOISE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Advice columns and rankings. The "N Stocks To ..." shape, comparisons,
    # and the prediction genre.
    (
        "listicle",
        _p(
            r"^\s*\d+\s+\S+.{0,40}\b(stocks?|shares|picks?|reasons?|things)\b",
            r"\bbetter buy\b",
            r"\bvs\.?\s",
            r"\bshould you (buy|sell|own)\b",
            r"\bis it time to\b",
            r"\bprediction\b",
            r"\bwhere will\b.*\bbe in\b",
            r"\bbuy-rated\b",
            r"\bbest .{0,30}\bstocks?\b",
            r"\bmillionaire(s|-maker)?\b",
            r"\b(multi[- ]?)?billionaire\b",
            # Executive profiles and family pieces. "Meet Madison Huang, daughter
            # of multibillionaire Nvidia CEO Jensen Huang" is maximally relevant
            # to NVDA by any vendor score and changes nothing.
            r"^\s*meet\b",
            r"\b(daughter|son|wife|husband|family) of\b",
        ),
    ),
    # 13F churn and short-interest updates, which read like M&A to a matcher
    # that only looks for "acquired" or "stake".
    (
        "ownership_churn",
        _p(
            r"\b(shares|stake|position|holdings)\b.{0,40}\b(acquired|sold|bought|purchased|boosted|raised|lowered|trimmed|cut|increased|decreased|reduced|lifted|grew|expanded)\b",
            r"\b(acquires|buys|sells|purchases|invests|boosts|takes|exits|trims|expands|reduces|liquidates|lifts|grows)\b.{0,40}\b(shares|stake|position|holdings)\b",
            r"\b(takes|has)\s+a\s+new\s+position\b",
            r"\bmakes?\s+(a\s+)?new\s+investment\b",
            r"\bhas\b.{0,40}\b(stock\s+)?position\s+in\b",
            r"\bposition\s+in\b.{0,40}\b(llc|inc|ltd|lp|corp|s\.a\.|ag)\b",
            r"\bshort interest\b",
            r"\b(52[- ]week (high|low))\b",
            r"\b(llc|lp|l\.p\.|inc\.?|n\.v\.|a/?s|s\.a\.)\s+(buys|sells|acquires|purchases|invests|boosts)\b",
        ),
    ),
    # Earnings previews, countdowns and recaps -- published around the event and
    # containing none of it.
    (
        "preview_or_recap",
        _p(
            r"\b(earnings )?preview\b",
            r"\bcountdown to\b",
            r"\bahead of\b.{0,30}\bearnings\b",
            r"\bwhat to (expect|watch)\b",
            r"\bnears? buy point\b",
            r"\b(weekly|daily|monthly) (recap|wrap|roundup)\b",
            r"\bdeep dive\b",
            r"\bstock forecasts?\b",
            r"\bthings to know\b",
            r"\bcould (send|push|drive|take|make|turn)\b.{0,50}\b(price|shares?|stock|投資)\b",
            r"^\s*an event on\b",
        ),
    ),
    # An interrogative opener is the reliable signature of an opinion column in
    # financial media -- "Does NVIDIA's AI Dominance Leave Any Room for ...",
    # "Is Blackstone Quietly Redefining ...". News states; columns ask.
    (
        "rhetorical_question",
        _p(r"^\s*(is|are|does|do|should|will|can|could|would|which|what|why)\b"),
    ),
    # Syndicated market-research PR. "Market to reach $X billion by 2032" is an
    # advertisement for a report, not guidance from an issuer.
    (
        "market_research_pr",
        _p(
            r"\bmarket\b.{0,40}\b(size|share|forecast|outlook|report|analysis)\b.{0,40}\b(20\d\d|\$|cagr)",
            r"\bcagr\b",
            r"\bmarket (size )?(to reach|forecast to|expected to reach|projected)\b",
            r"^\s*\$?[\d.]+\+?\s*(billion|trillion|million)\b.{0,40}\bmarket\b",
            r"\bglobal\b.{0,40}\bmarket report\b",
        ),
    ),
)

EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Guidance before earnings: "raises its guidance" and "cuts full-year
    # outlook" are forward statements even though both mention results.
    (
        "guidance",
        _p(
            r"\b(raises?|lifts?|cuts?|lowers?|trims?|reaffirms?|withdraws?|issues?)\b.{0,30}\b(guidance|outlook|forecast|full[- ]year)\b",
            r"\bguidance\b.{0,20}\b(raised|cut|lowered|reaffirmed)\b",
            r"\bwarns? on\b",
            r"\bprofit warning\b",
        ),
    ),
    (
        "earnings",
        _p(
            r"\b(beats?|misses?|tops?|falls short of)\b.{0,30}\b(estimates?|expectations?|consensus|forecasts?)\b",
            r"\bq[1-4]\b.{0,30}\b(results?|revenue|earnings|profit)\b",
            r"\b(reports?|posts?|delivers?|books?)\b.{0,25}\b(results?|revenue|earnings|profit|loss|sales)\b",
            r"\brecord (sales|revenue|quarter|profit|earnings)\b",
            r"\b(revenue|earnings|profit|eps)\b.{0,20}\b(rose|fell|jumped|surged|climbed|declined)\b",
        ),
    ),
    (
        "ma",
        _p(
            r"\b(acquires?|acquisition|to acquire|merger|merges? with|takeover|buyout)\b",
            r"\bagrees? to buy\b",
            r"\b(in talks|talks)\b.{0,70}\b(deal|acquisition|merger|stake|buy)\b",
            r"\bdiscloses?\b.{0,30}\bstake in\b",
            r"\bspin[- ]?off\b",
            r"\bdivests?\b",
        ),
    ),
    (
        "legal_regulatory",
        _p(
            r"\b(sec|doj|ftc|cma|eu|regulators?)\b.{0,30}\b(probe|investigation|inquiry|sues?|lawsuit|charges?)\b",
            r"\b(opens?|launches?)\b.{0,20}\b(probe|investigation)\b",
            r"\bexport controls?\b",
            r"\b(sanctions?|tariffs?|antitrust)\b",
            r"\b(lawsuit|sued|settlement|fined?|penalt(y|ies))\b",
            r"\b(approval|approves?|rejects?)\b.{0,25}\b(fda|regulator|antitrust)\b",
            r"\b(ban|bans|banned|restricts?)\b",
        ),
    ),
    (
        "contract",
        _p(
            r"\b(wins?|awarded|secures?|lands?|signs?)\b.{0,40}\b(contract|deal|order|award|agreement|application|deployment|customer)\b",
            r"\b\$[\d.]+\s*(billion|bn|million|m)\b.{0,30}\b(contract|deal|order)\b",
            r"\bpartners? with\b",
            r"\bpartnership\b",
            r"\bsupply agreement\b",
        ),
    ),
    (
        "capital",
        _p(
            r"\b(buyback|repurchase)\b",
            r"\b(dividend)\b.{0,25}\b(raises?|increases?|cuts?|suspends?|declares?|initiat)",
            r"\b(offering|raises?)\b.{0,25}\b(\$[\d.]+|equity|debt|notes|capital)\b",
            r"\b(stock )?split\b",
            r"\block[- ]?up\b",
            r"\bshares?\b.{0,40}\bsubject to a\b",
            r"\bipo\b",
            r"\bconvertible notes?\b",
        ),
    ),
    (
        "insider",
        _p(
            r"\b(ceo|cfo|cto|coo|chairman|chairwoman|director|officers?|executives?|insiders?)\b.{0,50}\b(sells?|sold|files? to sell|offloads?|unloads?|dumps?|buys?|purchases?|acquires?)\b",
            r"\binsider (selling|buying|sales?|purchases?|transactions?)\b",
            r"\bform 4\b",
        ),
    ),
    (
        "leadership",
        _p(
            r"\b(ceo|cfo|coo|cto|chair(man|woman|person)?|president)\b.{0,40}\b(steps? down|resigns?|departs?|to leave|out|fired|ousted|named|appoints?|appointed|succeeds?)\b",
            r"\b(names?|appoints?|hires?)\b.{0,25}\b(ceo|cfo|coo|cto|chief)\b",
            r"\bexecutive (shake[- ]?up|departure)\b",
        ),
    ),
    (
        "product",
        _p(
            r"\b(launches?|unveils?|introduces?|announces?|releases?|debuts?)\b.{0,40}\b(chip|chipset|product|platform|model|service|device|line|version|generation|accelerator|processor|gpu|cpu|silicon|engine|system|satellite|rocket|vehicle)\b",
            r"\bnext[- ]gen\b",
            r"\brolls? out\b",
            r"\b(begins?|starts?) (production|shipping)\b",
        ),
    ),
    (
        "analyst",
        _p(
            r"\b(upgrades?|downgrades?|initiates?)\b",
            r"\bprice target\b",
            r"\b(raises?|cuts?|lowers?)\b.{0,25}\bpt\b",
            r"\b(buy|sell|hold|overweight|underweight|neutral) rating\b",
        ),
    ),
    (
        "price_move",
        _p(
            r"\b(stock|shares)\b.{0,30}\b(rises?|falls?|jumps?|slides?|surges?|sinks?|plunges?|soars?|drops?|climbs?|tumbles?|rallies|gains?|slips?)\b",
            r"\b(rises?|falls?|jumps?|slides?|surges?|sinks?|plunges?|soars?|drops?|climbs?|tumbles?)\b.{0,20}\b\d+(\.\d+)?%",
            r"\bhits? (a )?(record|all[- ]time) high\b",
            r"\b(more than )?(tripled|doubled|quadrupled|halved)\b",
            r"\btrades?\b.{0,20}\b\d+% (below|above)\b",
        ),
    ),
)

# ---------------------------------------------------------------------- outlets

# Primary reporting. A wire carrying a story is evidence the story is real,
# which is a different claim from the story being important.
WIRES = frozenset(
    {
        "reuters", "bloomberg", "associated press", "ap", "dow jones",
        "the wall street journal", "wall street journal", "wsj",
        "financial times", "ft", "barron's", "barrons", "cnbc",
        "the information", "nikkei", "south china morning post", "scmp",
        "the new york times", "washington post", "axios", "the economist",
    }
)

# Republishers and content farms. One alongside a wire is unremarkable; a story
# carried *only* by these is usually SEO output rather than reporting.
AGGREGATORS = frozenset(
    {
        "marketbeat", "marketbeat.com", "simply wall st", "simplywall.st",
        "24/7 wall st.", "24/7 wall st", "247wallst.com", "zacks",
        "zacks investment research", "insider monkey", "gurufocus",
        "tipranks", "invezz", "stocktwits", "the motley fool", "motley fool",
        "investorplace", "benzinga", "etf daily news", "americanbankingnews",
        "defense world", "modern readers", "ticker report", "msn",
    }
)


def _norm_outlet(name: str) -> str:
    """Lower-cased, with both apostrophes folded to one.

    Yahoo writes ``Barron's`` with U+2019 and Alpha Vantage with U+0027. Left
    alone, one of the two silently stops counting as a wire.
    """
    return name.strip().lower().replace("’", "'").replace("ʼ", "'")


# --------------------------------------------------------------------- verdict


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why this headline did or did not earn attention.

    ``reason`` names every factor that moved the score. It exists so a surprising
    ranking can be argued with -- a score with no derivation is a number nobody
    can revise a threshold against.
    """

    event_class: str
    score: int
    band: str  # low | medium | high
    reason: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_noise(self) -> bool:
        return self.event_class == "noise"

    def to_dict(self) -> dict:
        return {
            "event_class": self.event_class,
            "score": self.score,
            "band": self.band,
            "reason": list(self.reason),
        }


def classify(title: str) -> str:
    """The event class of one headline, or ``noise``, or ``unclassified``.

    Noise patterns are checked first and win outright -- see the module
    docstring. Event patterns are checked in the order declared, so guidance
    beats earnings on a headline that mentions both, which is the right way
    round: an outlook change outlives the quarter that prompted it.
    """
    text = (title or "").strip()
    if not text:
        return "noise"
    for _name, pattern in NOISE_PATTERNS:
        if pattern.search(text):
            return "noise"
    for name, pattern in EVENT_PATTERNS:
        if pattern.search(text):
            return name
    return "unclassified"


def band(score: int) -> str:
    if score >= HIGH_BAND:
        return "high"
    if score >= MEDIUM_BAND:
        return "medium"
    return "low"


def assess(
    title: str,
    sources: tuple[str, ...] | list[str] = (),
    held: bool = False,
    peer_of: str | None = None,
) -> Verdict:
    """Score one story 0-100 and say what drove it.

    ``sources`` is every outlet carrying the story, so corroboration and the
    aggregator markdown are judged over the cluster rather than over whichever
    copy happened to arrive first.
    """
    event_class = classify(title)
    if event_class == "noise":
        return Verdict("noise", 0, "low", ("headline is advice, churn or PR filler",))

    score = CLASS_WEIGHTS[event_class]
    reason: list[str] = [f"{event_class} ({CLASS_WEIGHTS[event_class]:+d})"]

    outlets = [_norm_outlet(s) for s in sources if s and s.strip()]
    unique = sorted(set(outlets))

    if any(o in WIRES for o in unique):
        score += 12
        reason.append("carried by a wire (+12)")

    # Corroboration, with a deliberately flattening curve: the second outlet is
    # the informative one, the fifth is the syndication feed doing its job.
    extra = max(0, len(unique) - 1)
    if extra:
        bonus = min(15, 6 * extra - (extra - 1) * 2)
        score += bonus
        reason.append(f"{len(unique)} outlets (+{bonus})")

    if unique and all(o in AGGREGATORS for o in unique):
        score -= 18
        reason.append("aggregators only (-18)")

    if held:
        score += 15
        reason.append("open position (+15)")
    if peer_of:
        score -= 12
        reason.append(f"about peer {peer_of} (-12)")

    if event_class == "price_move":
        score = min(score, PRICE_MOVE_CEILING)
        reason.append(f"bare price move, capped at {PRICE_MOVE_CEILING}")

    score = max(0, min(100, score))
    return Verdict(event_class, score, band(score), tuple(reason))
