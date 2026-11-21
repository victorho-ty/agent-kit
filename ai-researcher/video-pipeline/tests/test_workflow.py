import json
from pathlib import Path

import pytest

from video_pipeline import cli, workflow
from video_pipeline.core import PipelineError, settings


def respond(store, job, stage, output, tmp_path):
    with store.transaction():
        task = workflow.generation(store, job, stage, "hermes", settings(None))
    path = tmp_path / "response.json"
    path.write_text(json.dumps({"request_id": task["request"]["request_id"], "output": output}))
    with store.transaction():
        return workflow.generation(store, job, stage, "hermes", settings(None), path)


def test_hermes_task_roundtrip_uses_current_agent_without_backend_model_call(store, job, draft, tmp_path, monkeypatch):
    def no_call(*args):
        raise AssertionError("Hermes mode must not call backend LLM")

    monkeypatch.setattr(workflow, "generate_local", no_call)
    result = respond(store, job, "draft", draft, tmp_path)
    assert result["identity"] == "hermes-profile"
    assert store.get(job["id"])["draft"] == draft
    with pytest.raises(PipelineError, match="independent"):
        workflow.generation(store, job, "review", "hermes", settings(None))


def test_stale_task_response_refused(store, job, draft, tmp_path):
    with store.transaction():
        old = workflow.generation(store, job, "draft", "hermes", settings(None))
        workflow.generation(store, job, "draft", "hermes", settings(None))
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"request_id": old["request"]["request_id"], "output": draft}))
    with pytest.raises(PipelineError, match="stale"):
        workflow.generation(store, job, "draft", "hermes", settings(None), path)


def test_rejected_claims_and_fabricated_quotes_cannot_pass(reviewed, review):
    review["checks"][0]["quotes"][0]["text"] = "Unpublished performance is excellent."
    with pytest.raises(PipelineError, match="quotation not found"):
        workflow.validate_review(review, reviewed)
    review["checks"][0]["quotes"] = []
    review["checks"][0]["supported"] = False
    with pytest.raises(PipelineError, match="passing review"):
        workflow.validate_review(review, reviewed)


def test_review_covers_every_claim(reviewed, review):
    reviewed["draft"]["claims"].append({"text": "Testing not published", "source_ids": ["S1"]})
    review["checks"] *= 2
    with pytest.raises(PipelineError, match="exactly once"):
        workflow.validate_review(review, reviewed)


def test_revision_invalidates_downstream_and_preserves_artifacts(store, reviewed, draft, tmp_path):
    reviewed.update(render={"video": "old.mp4"}, qc={"passed": True})
    artifact = store.artifact(reviewed, "old", {"kept": True})
    result = respond(store, reviewed, "draft", draft, tmp_path)
    saved = store.get(reviewed["id"])
    assert result["status"] == "draft"
    assert not {"review", "render", "qc"} & saved.keys()
    assert json.loads(Path(artifact).read_text()) == {"kept": True}
    assert saved["revision"] == 2


def test_sources_dedupe_across_tracking_parameters(store, job, evidence):
    evidence[0]["url"] = "https://example.org/chip#section"
    with store.transaction():
        result = workflow.create(store, "Different title", "en", [], evidence)
    assert result["duplicate"]
    assert result["job"]["id"] == job["id"]


def test_invalid_evidence_and_unfetched_references_refused(store, evidence, job, draft):
    evidence[0]["text"] = "blocked"
    with pytest.raises(PipelineError, match="100 characters"):
        workflow.create(store, "News", "en", [], evidence)
    draft["scenes"][0]["source_ids"] = ["S99"]
    with pytest.raises(PipelineError, match="supplied evidence"):
        workflow.validate_draft(draft, job)


def test_changed_draft_cannot_reuse_review(reviewed):
    reviewed["draft"]["title"] = "Changed claim"
    with pytest.raises(PipelineError, match="current draft"):
        workflow.require_review(reviewed)


def test_skill_install_preserves_existing_skill(tmp_path, capsys):
    assert cli.run(["install-skill", "--hermes-home", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    target = Path(result["skill_path"])
    assert (target / "references" / "workflow.md").is_file()
    before = (target / "SKILL.md").read_bytes()
    assert cli.run(["install-skill", "--hermes-home", str(tmp_path)]) == 1
    assert (target / "SKILL.md").read_bytes() == before


def test_local_revision_and_review_routes(store, job, draft, review, tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "generate_local", lambda *_: (draft, "llama-cpp:server-alias"))
    with store.transaction():
        workflow.generation(store, job, "draft", "llama-cpp", settings(None))
    assert respond(store, job, "review", review, tmp_path)["status"] == "reviewed"
    workflow.require_review(store.get(job["id"]))
    job["revision"] = 3
    with pytest.raises(PipelineError, match="exhausted"):
        workflow.generation(store, job, "draft", "hermes", settings(None))
