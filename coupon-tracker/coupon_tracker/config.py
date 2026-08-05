"""Load and validate config.yaml.

Every relative path resolves against the directory holding the config file —
the skill root. That directory is also where ``coupons.db``, ``inbox/``,
``media/`` and ``logs/`` live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .errors import ConfigError

CONFIG_NAME = "config.yaml"
HOME_ENV = "COUPON_TRACKER_HOME"

DEFAULTS: dict = {
    "db_path": "coupons.db",
    "media_dir": "media",
    "inbox_dir": "inbox",
    "logs_dir": "logs",
    "timezone": "Asia/Hong_Kong",
    "review_threshold": 0.75,
    "undo_window_hours": 48,
    "alert_days_before": 1,
    "accounts": {"allowlist": []},
}

_KNOWN_KEYS = set(DEFAULTS)
_KNOWN_ACCOUNT_KEYS = set(DEFAULTS["accounts"])


@dataclass(frozen=True)
class AccountsConfig:
    """Onboarding policy. Telegram ids here may auto-create an account on first contact."""

    allowlist: tuple[str, ...] = ()

    def permits(self, telegram_user_id: str | int) -> bool:
        return str(telegram_user_id) in self.allowlist


@dataclass(frozen=True)
class Config:
    root: Path
    source: Path | None
    db_path: Path
    media_dir: Path
    inbox_dir: Path
    logs_dir: Path
    timezone: str
    review_threshold: float
    undo_window_hours: int
    alert_days_before: int
    accounts: AccountsConfig = field(default_factory=AccountsConfig)

    def ensure_dirs(self) -> None:
        for d in (self.media_dir, self.inbox_dir, self.logs_dir, self.db_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def account_media_dir(self, account_id: str) -> Path:
        return self.media_dir / account_id

    def account_inbox_dir(self, account_id: str) -> Path:
        return self.inbox_dir / account_id


def find_config(explicit: str | os.PathLike | None = None) -> Path | None:
    """Resolution order: --config, $COUPON_TRACKER_HOME, cwd, ~/.hermes skill dir."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        return path if path.is_file() else path / CONFIG_NAME

    env_home = os.environ.get(HOME_ENV)
    if env_home:
        return Path(env_home).expanduser() / CONFIG_NAME

    for candidate in (
        Path.cwd() / CONFIG_NAME,
        Path.home() / ".hermes" / "skills" / "coupon-tracker" / CONFIG_NAME,
    ):
        if candidate.is_file():
            return candidate
    return None


def load(explicit: str | os.PathLike | None = None, *, required: bool = True) -> Config:
    """Load config, applying defaults for anything absent.

    With ``required=False`` a missing file yields the pure-default config rooted
    at the location it would have occupied — that is what ``init`` needs.
    """
    path = find_config(explicit)
    if path is None:
        raise ConfigError(
            "no config.yaml found. Run `couponctl init` in the skill root, "
            f"or set ${HOME_ENV}."
        )

    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("config.yaml must contain a mapping at the top level")
        source: Path | None = path
    elif required:
        raise ConfigError(f"config not found: {path}. Run `couponctl init`.")
    else:
        raw, source = {}, None

    return _build(path.parent, source, raw)


def _build(root: Path, source: Path | None, raw: dict) -> Config:
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")

    merged = {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}

    acct_raw = raw.get("accounts") or {}
    if not isinstance(acct_raw, dict):
        raise ConfigError("config key `accounts` must be a mapping")
    unknown_acct = set(acct_raw) - _KNOWN_ACCOUNT_KEYS
    if unknown_acct:
        raise ConfigError(f"unknown accounts config keys: {sorted(unknown_acct)}")
    allowlist_raw = acct_raw.get("allowlist") or []
    if not isinstance(allowlist_raw, list):
        raise ConfigError("accounts.allowlist must be a list of telegram user ids")
    allowlist = tuple(str(item).strip() for item in allowlist_raw if str(item).strip())

    threshold = _number(merged, "review_threshold", float)
    if not 0.0 <= threshold <= 1.0:
        raise ConfigError(f"review_threshold must be within 0..1, got {threshold}")

    undo_hours = _number(merged, "undo_window_hours", int)
    if undo_hours < 0:
        raise ConfigError("undo_window_hours must be >= 0")

    alert_days = _number(merged, "alert_days_before", int)
    if alert_days < 0:
        raise ConfigError("alert_days_before must be >= 0")

    tz_name = str(merged["timezone"])
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"unknown timezone: {tz_name}") from exc

    root = root.resolve()
    return Config(
        root=root,
        source=source,
        db_path=_resolve(root, merged["db_path"]),
        media_dir=_resolve(root, merged["media_dir"]),
        inbox_dir=_resolve(root, merged["inbox_dir"]),
        logs_dir=_resolve(root, merged["logs_dir"]),
        timezone=tz_name,
        review_threshold=threshold,
        undo_window_hours=undo_hours,
        alert_days_before=alert_days,
        accounts=AccountsConfig(allowlist=allowlist),
    )


def _number(merged: dict, key: str, cast):
    value = merged[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"config key `{key}` must be a number, got {value!r}")
    return cast(value)


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve(root: Path, value) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (root / path)


def write_default(root: Path) -> Path:
    """Write a config.yaml with the documented defaults. Never overwrites."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / CONFIG_NAME
    if target.exists():
        return target
    target.write_text(_TEMPLATE, encoding="utf-8")
    return target


_TEMPLATE = """# coupon-tracker configuration.
# All relative paths resolve against the directory holding this file.

db_path: coupons.db
media_dir: media          # partitioned per account: media/<account_id>/
inbox_dir: inbox          # partitioned per account: inbox/<account_id>/
logs_dir: logs

# App-wide. There are no per-account settings.
timezone: Asia/Hong_Kong

# Candidates below this extraction confidence route to needs_review.
review_threshold: 0.75

# `couponctl unuse` without --force is refused beyond this window.
undo_window_hours: 48

# How many days ahead to alert. This is also the repeat-rate control: there is
# no sent-ledger, so a coupon inside the window is reported once per daily run.
# 1 = at most two messages, the day before and the day it expires.
alert_days_before: 1

accounts:
  # Telegram user ids permitted to auto-create an account on first contact.
  # Anyone else gets one "this bot is private" reply and is ignored.
  allowlist: []

# No telegram section: Hermes owns the channel. Delivery destinations live on
# each account (account.chat_id); this package never talks to the network.
"""
