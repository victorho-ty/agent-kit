"""Deterministic half of the `news-monitor` skill.

The tools here decide *what is new* -- which feeds to poll, which headlines have
never been handed over before, and which candidate feed is a real finance feed
rather than a parked domain. They never decide what is worth saying, or what
belongs in the vault. That is the agent's job, and it is the only part of this
bundle a model touches.
"""

__version__ = "0.1.0"
