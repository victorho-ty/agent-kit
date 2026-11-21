from __future__ import annotations

import os
from pathlib import Path

from .core import PipelineError, digest, require, stamp, text
from .media import require_render


def save_token(credentials, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(credentials.to_json())


def authorize(client_secrets: Path, token: Path) -> dict:
    from google_auth_oauthlib.flow import InstalledAppFlow

    require(not token.exists(), "token file exists; choose another path instead of overwriting credentials")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), ["https://www.googleapis.com/auth/youtube.upload"])
    credentials = flow.run_local_server(port=8085)
    save_token(credentials, token)
    return {"token_path": str(token.resolve())}


def upload_request(job: dict, privacy: str, token: Path, started):
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    require(token.is_file(), "YouTube token missing; run youtube-auth first")
    credentials = Credentials.from_authorized_user_file(str(token))
    if not credentials.valid:
        require(credentials.refresh_token, "YouTube token needs fresh authorization")
        try:
            credentials.refresh(Request())
        except (RefreshError, TransportError) as exc:
            raise PipelineError("YouTube token refresh failed; check authorization/connectivity") from exc
        save_token(credentials, token)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    description = "\n".join(source["url"] for source in job["sources"])
    request = youtube.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": job["draft"]["title"], "description": description, "categoryId": "28"},
              "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}},
        media_body=MediaFileUpload(job["render"]["video"], mimetype="video/mp4", resumable=True),
    )
    started()
    response = None
    while response is None:
        try:
            _, response = request.next_chunk(num_retries=0)
        except HttpError as exc:
            raise PipelineError(f"YouTube upload HTTP {exc.resp.status}; outcome unknown, inspect channel before reconciliation") from exc
    return text(response.get("id"), "YouTube video ID", 100)


def publish(store, job: dict, privacy: str, token: Path, uploader=upload_request) -> dict:
    rendered = require_render(job)
    qc = job.get("qc", {})
    require(qc.get("passed") and qc.get("render_hash") == digest(rendered), "passing current QC required")
    require(job.get("visual_review", {}).get("render_hash") == digest(rendered)
            and job["visual_review"].get("passed") is True, "current render requires visual/editorial acceptance")
    publication = job.get("publication")
    if publication:
        require(publication.get("state") == "uploaded", "upload outcome unknown; inspect channel and use reconcile, never retry blindly")
        require(publication["privacy"] == privacy, "already uploaded with different privacy; edit on YouTube explicitly")
        return {"job_id": job["id"], "duplicate": True, **publication}
    require(privacy in {"private", "unlisted", "public"}, "invalid upload privacy")

    def started():
        job["publication"] = {"state": "unknown", "privacy": privacy, "started_at": stamp()}
        job["status"] = "upload_unknown"
        store.save(job)
        store.conn.commit()

    video_id = uploader(job, privacy, token, started)
    job["publication"] = {"state": "uploaded", "privacy": privacy, "video_id": video_id,
                          "url": f"https://www.youtube.com/watch?v={video_id}", "confirmed_at": stamp()}
    job["status"] = "uploaded"
    store.save(job)
    return {"job_id": job["id"], **job["publication"]}


def reconcile(store, job: dict, video_id: str) -> dict:
    require(job.get("publication", {}).get("state") == "unknown", "only unknown uploads need reconciliation")
    require(len(video_id) == 11 and all(char.isalnum() or char in "_-" for char in video_id), "invalid YouTube video ID")
    job["publication"].update(state="uploaded", video_id=video_id,
                               url=f"https://www.youtube.com/watch?v={video_id}", confirmed_at=stamp())
    job["status"] = "uploaded"
    store.save(job)
    return {"job_id": job["id"], **job["publication"]}
