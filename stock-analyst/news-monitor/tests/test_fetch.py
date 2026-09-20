"""The User-Agent, because one publisher's 403 depends on it."""

from __future__ import annotations

from news_monitor import fetch


def test_contact_address_is_appended_when_set(monkeypatch):
    monkeypatch.setenv("NEWS_MONITOR_CONTACT", "desk@example.com")
    agent = fetch.user_agent()
    assert "desk@example.com" in agent
    assert agent.endswith(")")


def test_no_contact_leaves_the_agent_alone(monkeypatch):
    monkeypatch.delenv("NEWS_MONITOR_CONTACT", raising=False)
    assert fetch.user_agent() == fetch.USER_AGENT
