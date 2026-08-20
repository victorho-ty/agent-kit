"""Watchlist configuration: what is watched, and how the report is paced."""

from __future__ import annotations

from .watchlist import (
    CONFIG_FILE,
    WatchlistConfig,
    add_ticker,
    load,
    remove_ticker,
    save,
    update_ticker,
)

__all__ = [
    "CONFIG_FILE",
    "WatchlistConfig",
    "add_ticker",
    "load",
    "remove_ticker",
    "save",
    "update_ticker",
]
