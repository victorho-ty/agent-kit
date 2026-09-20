"""Probe v5: select outbound via keyboard activation, dump return-grid labels."""
import json
import tempfile
from datetime import date
from pathlib import Path

from plane_ticket_prices.config.scope import TravelScope
from plane_ticket_prices.crawler import Crawler, build_search_url

SCOPE = TravelScope(
    name="HKG-PEN-Probe", from_airport="HKG", to_airport="PEN",
    depart_from=date(2026, 12, 18), depart_to=date(2026, 12, 20),
    return_from=date(2026, 12, 22), return_to=date(2026, 12, 26),
    max_stops=0, seat="economy", currency="HKD",
)

url = build_search_url(SCOPE, date(2026, 12, 18), date(2026, 12, 22))
profile = Path(tempfile.mkdtemp(prefix="gf-probe5-")) / "profile"

with Crawler(headless=True, profile_dir=profile, delay=1.0, timeout_ms=40_000) as crawler:
    crawler._goto(url)
    labels = crawler._wait_for_options(SCOPE, timeout_ms=40_000)

    target = next(l for l in labels if l.startswith("From ") and "Leaves" in l)
    sig = target.split("Leaves")[1].split(" on ")[0]  # "Hong Kong International Airport at 11:55 AM"
    print("sig:", json.dumps(sig), flush=True)

    locator = crawler._page.locator(f"[aria-label*='{sig}']").first
    locator.focus()
    crawler._page.keyboard.press("Enter")
    crawler._page.wait_for_timeout(6000)

    new_labels = crawler._aria_labels()
    print(f"labels after select: {len(new_labels)}", flush=True)
    seen = set()
    for label in new_labels:
        low = label.lower()
        if ("from " in low and ("leaves" in low or "arrives" in low)) or low.startswith("flight details"):
            if label in seen:
                continue
            seen.add(label)
            print("  ", json.dumps(label)[:230], flush=True)
            if len(seen) >= 25:
                break
