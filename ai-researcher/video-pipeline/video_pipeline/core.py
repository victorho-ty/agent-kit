from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml


class PipelineError(Exception):
    pass


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def text(value, label: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PipelineError(f"{label}: expected nonempty text, <= {limit} characters")
    return value.strip()


def settings(path: Path | None) -> dict:
    values = yaml.safe_load(path.read_text(encoding="utf-8")) if path else {}
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise PipelineError("config must be a YAML mapping")
    allowed = {"generator", "llama_url", "llama_model", "llama_token_env", "timeout", "max_tokens", "voice", "font", "width", "height", "fps"}
    unknown = values.keys() - allowed
    if unknown:
        raise PipelineError(f"unknown config settings: {', '.join(sorted(unknown))}")
    config = {
        "generator": "hermes", "llama_url": "http://127.0.0.1:8080/v1",
        "llama_model": None, "llama_token_env": "VIDEO_LLAMA_API_KEY",
        "timeout": 300, "max_tokens": 4096, "voice": "en-US-GuyNeural", "font": None,
        "width": 1080, "height": 1920, "fps": 30, **values,
    }
    for key, variable in (("llama_url", "VIDEO_LLAMA_URL"), ("llama_model", "VIDEO_LLAMA_MODEL")):
        if os.getenv(variable):
            config[key] = os.environ[variable]
    require(config["generator"] in {"hermes", "llama-cpp"}, "generator must be hermes or llama-cpp")
    for key in ("timeout", "max_tokens", "width", "height", "fps"):
        require(type(config[key]) is int and config[key] > 0, f"{key} must be a positive integer")
    for key in ("llama_url", "llama_token_env", "voice"):
        text(config[key], key)
    if config["llama_model"] is not None:
        text(config["llama_model"], "llama_model")
    return config


def data_root() -> Path:
    if os.getenv("VIDEO_PIPELINE_HOME"):
        return Path(os.environ["VIDEO_PIPELINE_HOME"]).expanduser().resolve()
    return (Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))) / "artifacts" / "video-pipeline").resolve()


class Store:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.root / "ledger.sqlite3", timeout=1)
        self.conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, story_key TEXT UNIQUE, body TEXT NOT NULL)")
        self.conn.commit()

    @contextmanager
    def transaction(self):
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            yield

    def close(self):
        self.conn.close()

    def get(self, job_id: str) -> dict:
        row = self.conn.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise PipelineError(f"unknown job: {job_id}")
        return json.loads(row[0])

    def find(self, story_key: str) -> dict | None:
        row = self.conn.execute("SELECT body FROM jobs WHERE story_key=?", (story_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, job: dict):
        job["updated_at"] = stamp()
        self.conn.execute("INSERT INTO jobs VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                          (job["id"], job["story_key"], json.dumps(job, ensure_ascii=False)))

    def directory(self, job: dict) -> Path:
        path = self.root / job["id"]
        path.mkdir(exist_ok=True)
        return path

    def artifact(self, job: dict, name: str, payload: dict) -> str:
        path = self.directory(job) / f"{name}-{uuid4().hex}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
        return str(path)


def require(condition, message: str):
    if not condition:
        raise PipelineError(message)
