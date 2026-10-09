"""A binary JEV judgment; transport and routing remain Python's responsibility."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from . import settings
from .errors import ConfigError, ScopeError

POLICY = json.loads((Path(__file__).parent / "config" / "scope_policy.json").read_text(encoding="utf-8"))


def input_hash(title: str, description: str | None, model: str) -> str:
    data = {"title": title, "description": description or "", "model": model, "policy": POLICY}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    choice: str
    confidence: float
    model: str

    def __post_init__(self):
        if (self.choice not in POLICY["criteria"] or type(self.confidence) not in (int, float)
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
                or not isinstance(self.model, str) or not self.model):
            raise ScopeError("JEV returned an invalid scope decision")

    def to_dict(self) -> dict:
        return {
            "status": "included" if self.choice == "include" else "excluded",
            "is_target_scope": self.choice == "include",
            "choice": self.choice,
            "reason": POLICY["reasons"][self.choice],
            "confidence_score": self.confidence,
            "model": self.model,
            "source": "jev",
            "prompt_version": POLICY["version"],
        }


def validate_key() -> None:
    if not settings.typesafe_api_key():
        raise ConfigError("scope_filter is enabled; set TYPESAFE_API_KEY in the runtime environment")


class JevClassifier:
    def __init__(self, config):
        validate_key()
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        self.model = config.model
        self.client = TypeSafeClient(
            api_key=settings.typesafe_api_key(), model=config.model,
            timeout=config.timeout_seconds,
            retry=RetryPolicy(
                max_retries=config.max_attempts_per_request - 1,
                timeout=config.timeout_seconds * config.max_attempts_per_request,
                backoff_max=10.0, http_statuses={429, 500, 502, 503, 504, 529},
            ),
        )

    def close(self):
        self.client.close()

    def __call__(self, title: str, description: str | None) -> Decision:
        from typesafe_sdk import (
            TypeSafeAPIError, TypeSafeAPIConnectionError,
            TypeSafeAPIResponseValidationError, TypeSafeError,
        )

        try:
            result = self.client.system_one(
                state={"title": title, "description": description or ""},
                model=self.model,
                questions={"scope": {"type": "choice", "instructions": POLICY["instructions"],
                                     "criteria": POLICY["criteria"]}},
            )
        except TypeSafeAPIConnectionError:
            raise ScopeError("JEV connection or timeout failure", retryable=True) from None
        except TypeSafeAPIResponseValidationError:
            raise ScopeError("JEV returned an invalid scope response") from None
        except TypeSafeAPIError as exc:
            retryable = exc.status == 429 or exc.status >= 500
            raise ScopeError(f"JEV HTTP {exc.status}", retryable=retryable,
                             stop_run=exc.status in (400, 401, 403, 404, 422)) from None
        except TypeSafeError:
            raise ScopeError("JEV request or response validation failed", stop_run=True) from None

        try:
            answer = result.answers["scope"]
            probabilities = answer.probabilities
            if (answer.type != "choice" or set(probabilities) != {"include", "exclude"}
                    or any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
                           for p in probabilities.values())
                    or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6)
                    or answer.choice not in probabilities
                    or probabilities[answer.choice] < max(probabilities.values())):
                raise ScopeError("JEV returned an invalid scope response")
            return Decision(answer.choice, answer.confidence, result.model)
        except (AttributeError, KeyError, TypeError):
            raise ScopeError("JEV returned an invalid scope response") from None
