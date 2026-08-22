"""Swing-trading desk tools for the Hermes ``stock-analyst`` profile.

Two jobs, one bundle, because they share every expensive part -- the daily bar
cache, the indicator engine, the news dedupe table and the chart renderer:

* **Watchlist** -- scanned once a day for the compression that precedes a
  breakout, reported 30 minutes before each market's open.
* **Portfolio** -- positions watched for the corporate events and the news that
  change the case for holding, alerted on the event and never on a schedule.

There is no Telegram code anywhere in this package, and there will not be.
Hermes owns the channel; these tools return JSON and file paths, and the agent
decides what is worth saying. The same split the other bundles use.

The detection maths (:mod:`indicators`, :mod:`compression`, :mod:`setups`) is
pure stdlib over lists of floats -- no pandas, no network, no clock. Everything
that touches the outside world lives in :mod:`providers`, :mod:`bars`,
:mod:`news` and :mod:`events`.
"""

__version__ = "0.1.0"
