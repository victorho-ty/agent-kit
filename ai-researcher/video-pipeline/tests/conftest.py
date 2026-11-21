from copy import deepcopy

import pytest

from video_pipeline.core import Store, digest
from video_pipeline.workflow import create, validate_draft, validate_review


@pytest.fixture
def evidence():
    return [{"url": "https://example.org/chip?utm_source=feed", "title": "Chip announcement",
             "text": "Example Labs announced a new chip on Monday. The company says the chip is designed for research. Independent performance testing has not been published."}]


@pytest.fixture
def draft():
    return {"title": "Example Labs announces research chip", "scenes": [
        {"narration": "Example Labs announced a new chip. Independent performance testing has not been published.",
         "visual": "Research chip announced", "source_ids": ["S1"]}],
        "claims": [{"text": "Example Labs announced a new chip", "source_ids": ["S1"]}]}


@pytest.fixture
def review():
    return {"verdict": "pass", "script_supported": True, "adult_content": False,
            "checks": [{"claim_index": 0, "supported": True, "reason": "Announcement explicitly stated",
                        "quotes": [{"source_id": "S1", "text": "Example Labs announced a new chip on Monday."}]}]}


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "state")
    yield value
    value.close()


@pytest.fixture
def job(store, evidence):
    with store.transaction():
        return create(store, "Research chip", "en", [], evidence)["job"]


@pytest.fixture
def reviewed(store, job, draft, review):
    value = deepcopy(job)
    value.update(draft=validate_draft(draft, value), draft_generator="llama-cpp", draft_identity="llama-cpp:local", revision=1)
    value["review"] = {"output": validate_review(review, value), "generator": "hermes",
                       "identity": "hermes-profile", "draft_hash": digest(value["draft"])}
    value["status"] = "reviewed"
    with store.transaction():
        store.save(value)
    return value
