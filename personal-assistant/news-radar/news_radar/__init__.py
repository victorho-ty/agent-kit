"""Deterministic tools behind the news-radar Hermes skill.

Scans a configured list of news sources, remembers what it has already seen, and
hands back everything published since the last digest -- clustered so one story
carried by five outlets reads as one story, and grouped into the categories the
human assigned to each source.

There is no model call anywhere on the scan path, and no Telegram client
anywhere in the package. Python decides what is new; the agent decides how to
say it.
"""

__version__ = "0.1.0"
