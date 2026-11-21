import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from video_pipeline import media, publish
from video_pipeline.core import PipelineError, digest, file_hash, settings


@pytest.fixture
def ready(store, reviewed, tmp_path):
    video, captions = tmp_path / "checked.mp4", tmp_path / "checked.srt"
    video.write_bytes(b"verified media fixture")
    captions.write_text("1\n00:00:00,000 --> 00:00:05,000\nSource text\n")
    reviewed["render"] = {"video": str(video), "subtitles": str(captions), "video_hash": file_hash(video),
                          "subtitles_hash": file_hash(captions), "draft_hash": digest(reviewed["draft"]),
                          "review_hash": digest(reviewed["review"])}
    fingerprint = digest(reviewed["render"])
    reviewed["qc"] = {"passed": True, "render_hash": fingerprint}
    reviewed["visual_review"] = {"passed": True, "render_hash": fingerprint, "notes": "Fixture acceptance"}
    with store.transaction():
        store.save(reviewed)
    return reviewed


def test_upload_gates_prevent_network_for_changed_artifacts_or_missing_acceptance(store, ready, tmp_path):
    calls = []

    def upload(*args):
        calls.append(args)

    ready.pop("visual_review")
    with pytest.raises(PipelineError, match="visual/editorial"):
        publish.publish(store, ready, "private", tmp_path / "token", upload)
    Path(ready["render"]["video"]).write_bytes(b"modified after review")
    with pytest.raises(PipelineError, match="artifact changed"):
        publish.publish(store, ready, "private", tmp_path / "token", upload)
    assert not calls


def test_uncertain_upload_retains_intent_and_blocks_duplicate_even_after_rollback(store, ready, tmp_path):
    def interrupted(job, privacy, token, started):
        started()
        raise OSError("connection lost after server accepted upload")

    with pytest.raises(OSError), store.transaction():
        publish.publish(store, ready, "private", tmp_path / "token", interrupted)
    recovered = store.get(ready["id"])
    assert recovered["publication"]["state"] == "unknown"
    with pytest.raises(PipelineError, match="unknown"):
        publish.publish(store, recovered, "private", tmp_path / "token", interrupted)
    with store.transaction():
        result = publish.reconcile(store, recovered, "abc12345678")
    assert result["video_id"] == "abc12345678"
    assert publish.publish(store, recovered, "private", tmp_path / "token", interrupted)["duplicate"]


def test_confirmed_upload_is_idempotent_and_preserves_files(store, ready, tmp_path):
    calls = []

    def uploaded(job, privacy, token, started):
        calls.append(privacy)
        started()
        return "abc12345678"

    with store.transaction():
        first = publish.publish(store, ready, "private", tmp_path / "token", uploaded)
    with store.transaction():
        second = publish.publish(store, store.get(ready["id"]), "private", tmp_path / "token", uploaded)
    assert first["video_id"] == second["video_id"]
    assert calls == ["private"]
    assert Path(ready["render"]["video"]).is_file()


def test_real_render_and_qc_with_provided_audio(store, reviewed, tmp_path):
    if not all(os.getenv(f"VIDEO_{name.upper()}") or shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        pytest.skip("FFmpeg/ffprobe not installed")
    wave = tmp_path / "tone.wav"
    media.run([media.tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=6", str(wave)])
    audio = tmp_path / "audio.json"
    audio.write_text(json.dumps([str(wave)]))
    config = {**settings(None), "width": 180, "height": 320, "fps": 10}
    with store.transaction():
        result = media.render(store, reviewed, config, audio_path=audio)
    assert Path(result["video"]).is_file()
    assert "00:00:06,000" in Path(result["subtitles"]).read_text()
    with store.transaction():
        assert media.qc(store, reviewed)["passed"]
    black = tmp_path / "black.png"
    Image.new("RGB", (180, 320)).save(black)
    images = tmp_path / "images.json"
    images.write_text(json.dumps([str(black)]))
    with store.transaction():
        black_result = media.render(store, reviewed, config, assets_path=images, audio_path=audio)
    with store.transaction():
        assert not media.qc(store, reviewed)["passed"]
    assert Path(result["video"]).is_file()
    assert black_result["video"] != result["video"]


def test_render_refuses_unreviewed_draft_before_tools(store, job):
    with pytest.raises(PipelineError, match="review"):
        media.render(store, job, settings(None))
