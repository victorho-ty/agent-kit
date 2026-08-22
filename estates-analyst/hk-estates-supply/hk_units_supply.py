"""HK private residential primary market supply monitor.

Scrapes the Housing Bureau quarterly, extracts headline supply figures from page 2, appends them to
history data file when the quarter is new, and email.
"""
import argparse
import io
import logging
import os
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: no display when run by the scheduler
import matplotlib.pyplot as plt

# matplotlib emits INFO-level "categorical units" chatter when plotting string x-axes;
# raise its threshold so it doesn't leak into our INFO logs.
logging.getLogger("matplotlib").setLevel(logging.WARNING)
import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup
from pretty_html_table import build_table

from trading_dev.seasonality.seasonality_utils import figure_to_bytes
from trading_dev.util.gmail_sender import GmailSender

LOGGER = logging.getLogger(__name__)

module_dir = Path(__file__).parent
DATA_DIR = os.path.join(module_dir, "data")
HISTORY_FILE = os.path.join(DATA_DIR, "hk_units_supply_history.csv")

INDEX_URL = "https://www.hb.gov.hk/tc/publications/housing/private/pshpm/index.html"
PDF_BASE_URL = "https://www.hb.gov.hk/tc/publications/housing/private/pshpm/"
SECTION_HEADING = "私人住宅一手市場供應"
PDF_HREF_RE = re.compile(r"stat(\d{4})(\d{2})\.pdf", re.IGNORECASE)

# Email configuration
EMAIL_FROM = "horace.ho.mj@gmail.com"
EMAIL_TO = "vichoty@gmail.com"
EMAIL_CC = "popohoma@hotmail.com"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
REQUEST_TIMEOUT = 30

# Page-2 label substrings. Each figure sits on the same visual row as its label,
# printed to the right of it, comma-formatted with a "伙" suffix and rounded to the
# nearest thousand, e.g.
#   已落成但未售出的單位數目            19,000 伙
# The BeingBuilt label carries a trailing "，減去已預售單位數目" that we omit here so a
# prefix match still works.
LABEL_BUILT_NOT_SOLD = "已落成但未售出的單位數目"
LABEL_BEING_BUILT = "建築中的單位數目"
LABEL_LAND_READY = "已批出土地上可隨時動工的單位數目"

# Quarter-end month -> 3-letter label used in the CSV "Quarter" column (YYYY/Mon).
MONTH_LABELS = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}

# Number of most-recent quarters shown in the e-mail summary table.
TABLE_QUARTERS = 12

# Display headers for the e-mail table.
COL_LAND_READY_ZH = "可隨時動工"
COL_BEING_BUILT_ZH = "建築中未售"
COL_BUILT_NOT_SOLD_ZH = "現樓貨尾"


def find_latest_pdf_href() -> str:
    """Return the latest quarter's PDF filename (e.g. ``stat202603.pdf``).

    Locates the first ``stat<YYYYMM>.pdf`` anchor that follows the
    "私人住宅一手市場供應" heading, falling back to the first such anchor on the page.
    """
    resp = requests.get(INDEX_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    heading = soup.find(lambda tag: tag.name in ("h1", "h2", "h3")
                        and SECTION_HEADING in tag.get_text())
    if heading is not None:
        for anchor in heading.find_all_next("a", href=PDF_HREF_RE):
            return os.path.basename(anchor["href"])

    # Fallback: first matching anchor anywhere on the page.
    anchor = soup.find("a", href=PDF_HREF_RE)
    if anchor is not None:
        return os.path.basename(anchor["href"])

    raise ValueError(f"No 'stat<YYYYMM>.pdf' link found on {INDEX_URL}")


def parse_quarter_from_filename(href: str) -> tuple[str, int, int]:
    """Parse ``stat202603.pdf`` into ("2026/Mar", 2026, 3)."""
    match = PDF_HREF_RE.search(href)
    if match is None:
        raise ValueError(f"Unexpected PDF filename: {href!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if month not in MONTH_LABELS:
        raise ValueError(f"Unexpected (non quarter-end) month in {href!r}: {month}")
    return f"{year}/{MONTH_LABELS[month]}", year, month


def read_history() -> pd.DataFrame:
    """Read the history CSV (newest-first). Empty DataFrame if the file is missing."""
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    return pd.read_csv(HISTORY_FILE)


def is_new_quarter(quarter_str: str, df: pd.DataFrame | None = None) -> bool:
    """True when ``quarter_str`` is not already present in the history CSV."""
    if df is None:
        df = read_history()
    if df.empty or "Quarter" not in df.columns:
        return True
    return quarter_str not in df["Quarter"].astype(str).values


def download_pdf(href: str) -> bytes:
    resp = requests.get(PDF_BASE_URL + href, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content


# A figure and its label are typeset on the same row but their baselines differ by a
# couple of points, so reading-order text extraction puts the number before the label
# for some rows and after it for others. Group words into rows by vertical centre
# instead; rows are ~28pt apart, so this tolerance separates them cleanly.
ROW_TOLERANCE = 8.0

NUMBER_RE = re.compile(r"^[\d,]*\d$")


def _page_rows(page) -> list[tuple[str, list[dict]]]:
    """Group a page's words into visual rows, left-to-right.

    Returns ``[(row_text, words), ...]`` top-to-bottom, where ``row_text`` is the row's
    words concatenated (no separator — the Chinese text carries no spaces).
    """
    words = sorted(page.extract_words(), key=lambda w: (w["top"] + w["bottom"]) / 2)

    rows: list[list[dict]] = []
    for word in words:
        centre = (word["top"] + word["bottom"]) / 2
        if rows:
            last = rows[-1][-1]
            if centre - (last["top"] + last["bottom"]) / 2 <= ROW_TOLERANCE:
                rows[-1].append(word)
                continue
        rows.append([word])

    ordered = [sorted(row, key=lambda w: w["x0"]) for row in rows]
    return [("".join(w["text"] for w in row), row) for row in ordered]


def _extract_figure(rows: list[tuple[str, list[dict]]], label: str) -> int:
    """Return the integer printed on the same row as ``label``, to its right."""
    for row_text, words in rows:
        if label not in row_text:
            continue
        # The label may span several words; walk left-to-right until it is complete,
        # then take the first number to the right of that word.
        seen = ""
        label_end = 0.0
        for word in words:
            seen += word["text"]
            if label in seen:
                label_end = word["x1"]
                break
        for word in words:
            if word["x0"] >= label_end and NUMBER_RE.match(word["text"]):
                return int(word["text"].replace(",", ""))
    raise ValueError(f"Could not locate figure for label {label!r} on PDF page 2")


def parse_supply_figures(pdf_bytes: bytes) -> dict:
    """Extract the three page-2 figures and their total.

    Returns ``{"LandReady", "BeingBuilt", "BuiltNotSold", "Total"}``.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[1]  # page 2
        rows = _page_rows(page)
        text = page.extract_text() or ""

    figures = {
        "LandReady": _extract_figure(rows, LABEL_LAND_READY),
        "BeingBuilt": _extract_figure(rows, LABEL_BEING_BUILT),
        "BuiltNotSold": _extract_figure(rows, LABEL_BUILT_NOT_SOLD),
    }
    figures["Total"] = figures["LandReady"] + figures["BeingBuilt"] + figures["BuiltNotSold"]

    # Cross-check against the total printed on the page ("... 101,000 伙").
    printed = re.search(r"未來三至四年間.*?([\d,]+)\s*伙", text)
    if printed is not None:
        printed_total = int(printed.group(1).replace(",", ""))
        if printed_total != figures["Total"]:
            LOGGER.warning("Computed total %s != printed total %s (rounding?)",
                           figures["Total"], printed_total)
    return figures


def append_history(quarter_str: str, figures: dict) -> pd.DataFrame:
    """Prepend a new newest-first row to the history CSV and return the full DataFrame.

    Columns are mapped positionally to the existing header so we never depend on
    typing the Chinese column names: col0=Quarter, col1=LandReady, col2=BeingBuilt,
    col3=BuiltNotSold, col4=Total.
    """
    df = read_history()
    if df.empty:
        raise ValueError(f"History file missing or empty: {HISTORY_FILE}")

    cols = df.columns.tolist()
    new_row = pd.DataFrame(
        [[quarter_str, figures["LandReady"], figures["BeingBuilt"],
          figures["BuiltNotSold"], figures["Total"]]],
        columns=cols,
    )
    updated = pd.concat([new_row, df], ignore_index=True)
    updated.to_csv(HISTORY_FILE, index=False, encoding="utf-8")
    LOGGER.info("Appended %s to %s", quarter_str, HISTORY_FILE)
    return updated


def _line_chart(chrono: pd.DataFrame, value_col: str, title: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(chrono["Quarter"], chrono[value_col], marker="o")
    ax.set_title(title)
    ax.set_ylabel("Units")
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig


def _format_mom(series: pd.Series) -> list[str]:
    pct = series.pct_change() * 100
    return [f"{v:+.2f}%" if pd.notnull(v) else "—" for v in pct]


def send_alert_email(df: pd.DataFrame, quarter_str: str) -> None:
    """Email two trend charts plus a summary table of the last TABLE_QUARTERS quarters."""
    cols = df.columns.tolist()
    quarter_col, land_col, build_col, sold_col, total_col = cols[:5]

    # Chronological (earliest -> latest) view for charts and QoQ.
    chrono = df.iloc[::-1].reset_index(drop=True)
    for c in (land_col, build_col, sold_col, total_col):
        chrono[c] = pd.to_numeric(chrono[c], errors="coerce")

    # --- Part 1: line charts ---
    images = []
    fig_sold = _line_chart(chrono, sold_col, "Completed but Unsold (BuiltNotSold)")
    images.append((figure_to_bytes(fig_sold), "chart_builtnotsold"))
    plt.close(fig_sold)

    fig_build = _line_chart(chrono, build_col, "Under Construction (BeingBuilt)")
    images.append((figure_to_bytes(fig_build), "chart_beingbuilt"))
    plt.close(fig_build)

    # --- Part 2: table (newest first) ---
    mom_land = _format_mom(chrono[land_col])
    mom_build = _format_mom(chrono[build_col])
    mom_sold = _format_mom(chrono[sold_col])

    rows = []
    for i in range(len(chrono) - 1, -1, -1):  # newest first
        rows.append([
            chrono.loc[i, quarter_col],
            f"{chrono.loc[i, land_col]:,.0f}", mom_land[i],
            f"{chrono.loc[i, build_col]:,.0f}", mom_build[i],
            f"{chrono.loc[i, sold_col]:,.0f}", mom_sold[i],
            f"{chrono.loc[i, total_col]:,.0f}",
        ])
        if len(rows) >= TABLE_QUARTERS:
            break

    table_df = pd.DataFrame(
        rows,
        columns=["Quarter",
                 COL_LAND_READY_ZH, "QoQ%",
                 COL_BEING_BUILT_ZH, "QoQ%",
                 COL_BUILT_NOT_SOLD_ZH, "QoQ%",
                 "Total(unit)"],
    )
    html_table = build_table(table_df, "blue_light")

    body = f"""
    <html><body>
      <h3>現樓貨尾 (BuiltNotSold)</h3>
      <img src="cid:chart_builtnotsold" style="width:100%; max-width:720px; height:auto;">
      <h3>建築中未售 (BeingBuilt)</h3>
      <img src="cid:chart_beingbuilt" style="width:100%; max-width:720px; height:auto;">
      <h3>Last {TABLE_QUARTERS} quarters</h3>
      {html_table}
    </body></html>
    """
    subject = f"HK Residential Supply — {quarter_str}"
    GmailSender().send_email(EMAIL_FROM, EMAIL_TO, subject, body, images=images, cc=EMAIL_CC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HK private residential primary market supply monitor")
    parser.add_argument("--force", action="store_true",
                        help="Re-send the email even if the quarter was already processed "
                             "(does not duplicate the CSV row).")
    args = parser.parse_args()

    href = find_latest_pdf_href()
    quarter, _year, _month = parse_quarter_from_filename(href)
    LOGGER.info("Latest published quarter: %s (%s)", quarter, href)

    existing = read_history()
    new_quarter = is_new_quarter(quarter, existing)

    if not new_quarter and not args.force:
        LOGGER.info("%s already processed; nothing to do.", quarter)
    else:
        figures = parse_supply_figures(download_pdf(href))
        LOGGER.info("Parsed figures for %s: %s", quarter, figures)

        if new_quarter:
            df = append_history(quarter, figures)
        else:
            LOGGER.info("--force: re-sending email without appending (quarter exists).")
            df = existing

        send_alert_email(df, quarter)
