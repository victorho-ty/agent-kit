from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError

import yaml

from . import media, models, publish, workflow
from .core import (
    PipelineError,
    Store,
    data_root,
    digest,
    read_json,
    require,
    settings,
    text,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="video-pipeline", description="Hermes/llama.cpp news shorts, one JSON result per command")
    root.add_argument("--data-dir", type=Path, default=data_root())
    root.add_argument("--config", type=Path)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("models")
    create = sub.add_parser("create")
    create.add_argument("--topic", required=True)
    create.add_argument("--language", default="en")
    sources = create.add_mutually_exclusive_group(required=True)
    sources.add_argument("--url", action="append")
    sources.add_argument("--evidence", type=Path, help="JSON list of fetched article evidence")
    for stage in ("draft", "review"):
        command = sub.add_parser(stage)
        command.add_argument("--job", required=True)
        command.add_argument("--generator", choices=["hermes", "llama-cpp"])
        command.add_argument("--input", type=Path, help="Hermes request_id/output JSON response")
    status = sub.add_parser("status")
    status.add_argument("--job", required=True)
    render = sub.add_parser("render")
    render.add_argument("--job", required=True)
    render.add_argument("--assets", type=Path)
    render.add_argument("--audio", type=Path)
    check = sub.add_parser("qc")
    check.add_argument("--job", required=True)
    check.add_argument("--visual-review", type=Path, help="JSON: render_hash, passed, notes")
    auth = sub.add_parser("youtube-auth")
    auth.add_argument("--client-secrets", required=True, type=Path)
    auth.add_argument("--token", required=True, type=Path)
    upload = sub.add_parser("upload")
    upload.add_argument("--job", required=True)
    upload.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    upload.add_argument("--token", required=True, type=Path)
    recovery = sub.add_parser("reconcile")
    recovery.add_argument("--job", required=True)
    recovery.add_argument("--video-id", required=True)
    install = sub.add_parser("install-skill")
    install.add_argument("--hermes-home", type=Path, default=Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))))
    install.add_argument("--source", type=Path)
    return root


def install_skill(args) -> dict:
    if args.source:
        source = args.source.resolve()
    else:
        source = Path(__file__).resolve().parents[1] / "skills" / "video-pipeline"
        if not source.is_dir():
            source = Path(sys.prefix) / "share" / "video-pipeline" / "skills" / "video-pipeline"
    require((source / "SKILL.md").is_file(), "packaged skill missing; provide --source /bundle/skills/video-pipeline")
    target = args.hermes_home.expanduser().resolve() / "skills" / "video-pipeline"
    require(not target.exists(), f"skill already exists at {target}; compare changes before updating")
    shutil.copytree(source, target)
    return {"skill_path": str(target), "command": "/video-pipeline"}


def dispatch(args) -> dict:
    if args.command == "install-skill":
        return install_skill(args)
    if args.command == "youtube-auth":
        return publish.authorize(args.client_secrets, args.token)
    config = settings(args.config)
    if args.command == "doctor":
        return {"data_dir": str(args.data_dir.resolve()), "generator": config["generator"],
                "cloud_model": "current Hermes profile agent; no cloud API credentials read by backend",
                "llama_url": models.endpoint(config)[0], "llama_model": config["llama_model"],
                "ffmpeg": os.getenv("VIDEO_FFMPEG") or shutil.which("ffmpeg"),
                "ffprobe": os.getenv("VIDEO_FFPROBE") or shutil.which("ffprobe"),
                "narration": "Edge TTS (online) or per-scene supplied audio (local)"}
    if args.command == "models":
        model, url, _ = models.local_model(config)
        return {"model": model, "base_url": url}
    store = Store(args.data_dir)
    try:
        with store.transaction():
            if args.command == "create":
                imported = read_json(args.evidence) if args.evidence else None
                return workflow.create(store, args.topic, args.language, args.url or [], imported)
            job = store.get(args.job)
            if args.command == "status":
                summary = {**job, "sources": [{key: source[key] for key in ("id", "url", "title")} for source in job["sources"]]}
                summary.pop("pending", None)
                result = {"job": summary}
                if job.get("render"):
                    result["render_hash"] = digest(job["render"])
                return result
            if args.command in {"draft", "review"}:
                default = config["generator"] if args.command == "draft" else (
                    "llama-cpp" if job.get("draft_generator") == "hermes" else "hermes")
                return workflow.generation(store, job, args.command, args.generator or default, config, args.input)
            if args.command == "render":
                return media.render(store, job, config, args.assets, args.audio)
            if args.command == "qc":
                result = media.qc(store, job)
                if args.visual_review:
                    review = read_json(args.visual_review)
                    require(isinstance(review, dict) and type(review.get("passed")) is bool
                            and review.get("render_hash") == digest(job["render"]), "visual review must name current render_hash and boolean passed")
                    text(review.get("notes"), "visual review notes", 2000)
                    job["visual_review"] = review
                    store.save(job)
                result["visual_accepted"] = job.get("visual_review", {}).get("passed") is True and job["visual_review"].get("render_hash") == digest(job["render"])
                if result["passed"] and result["visual_accepted"]:
                    job["status"] = "ready"
                    store.save(job)
                return result
            if args.command == "upload":
                return publish.publish(store, job, args.privacy, args.token)
            return publish.reconcile(store, job, args.video_id)
    finally:
        store.close()


def run(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = {"ok": True, **dispatch(args)}
    except (PipelineError, OSError, ValueError, KeyError, TypeError, sqlite3.Error, URLError,
            subprocess.SubprocessError, yaml.YAMLError) as exc:
        result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    except ModuleNotFoundError as exc:
        result = {"ok": False, "error": "missing_dependency", "message": f"Install optional dependency: {exc.name}; use uv sync --extra youtube for uploads"}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 1


def main():
    raise SystemExit(run())
