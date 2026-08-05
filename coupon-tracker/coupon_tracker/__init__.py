"""Deterministic coupon storage, lifecycle and alerting behind a Hermes skill.

The agent owns ingest judgement only. Everything in this package is
deterministic: no LLM call sits on the query path or the alert path.
"""

__version__ = "1.0.0"
