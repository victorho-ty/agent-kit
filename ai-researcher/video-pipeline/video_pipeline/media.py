"""Adapted Verticals pan/zoom, concat assembly and subtitle timing; see third-party notices."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .core import PipelineError, digest, file_hash, read_json, require
from .workflow import editable, require_review


def tool(name: str) -> str:
    executable = os.getenv(f"VIDEO_{name.upper()}") or shutil.which(name)
    require(executable, f"{name} missing; install FFmpeg or set VIDEO_{name.upper()}")
    return str(executable)


def run(command: list[str], cwd: Path | None = None, timeout=600):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if result.returncode:
        raise PipelineError(f"{Path(command[0]).name} failed: {result.stderr[-1800:]}")
    return result


def probe(path: Path) -> dict:
    result = run([tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(result.stdout)


def duration(path: Path) -> float:
    value = float(probe(path)["format"]["duration"])
    require(math.isfinite(value) and value > 0, "media duration must be finite and positive")
    return value


def font_path(config: dict) -> str:
    if config.get("font"):
        path = Path(config["font"]).expanduser().resolve()
        require(path.is_file(), "configured font file missing")
        return str(path)
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
             "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
    found = next((path for path in paths if Path(path).is_file()), None)
    require(found, "set font to an installed TTF/OTF supporting requested language")
    return found


def card(label: str, path: Path, config: dict):
    width, height = config["width"], config["height"]
    image = Image.new("RGB", (width, height), (35, 53, 78))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path(config), max(12, width // 15))
    max_width = width * 0.82
    lines, line = [], ""
    for char in label:
        if draw.textlength(line + char, font=font) > max_width:
            lines.append(line)
            line = char
        else:
            line += char
    lines.append(line)
    spacing = max(4, width // 60)
    value = "\n".join(lines)
    bbox = draw.multiline_textbbox((0, 0), value, font=font, spacing=spacing, align="center")
    require(bbox[3] - bbox[1] < height * 0.45, "visual label too long for card; revise draft")
    draw.rectangle((width * 0.1, height * 0.16, width * 0.9, height * 0.17), fill=(66, 202, 189))
    draw.multiline_text((width / 2, height * 0.35), value, font=font, fill="white", anchor="mm", align="center", spacing=spacing)
    image.save(path)


def srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def subtitles(scenes: list[dict], durations: list[float], output: Path):
    entries, offset = [], 0.0
    for scene, seconds in zip(scenes, durations, strict=True):
        narration = scene["narration"].replace("\r", " ").replace("\n", " ").replace("-->", "→")
        chunks = textwrap.wrap(narration, width=38, break_long_words=True, break_on_hyphens=False)
        total = sum(len(chunk) for chunk in chunks)
        elapsed = 0.0
        for chunk in chunks:
            end = elapsed + seconds * len(chunk) / total
            safe = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            entries.append(f"{len(entries) + 1}\n{srt_time(offset + elapsed)} --> {srt_time(offset + end)}\n{safe}\n")
            elapsed = end
        offset += seconds
    output.write_text("\n".join(entries), encoding="utf-8")


async def edge_audio(narration: str, voice: str, path: Path):
    import aiohttp
    import edge_tts

    try:
        await edge_tts.Communicate(narration, voice).save(str(path))
    except (aiohttp.ClientError, edge_tts.exceptions.NoAudioReceived) as exc:
        raise PipelineError(f"Edge TTS failed: {exc}; no narration fallback used") from exc


def animate_frame(image: Path, output: Path, seconds: float, config: dict, index: int):
    width, height, fps = config["width"], config["height"], config["fps"]
    frames = math.ceil(seconds * fps)
    zoom = f"1.08-0.08*on/{frames}" if index % 2 else f"1+0.08*on/{frames}"
    vf = (f"scale={width * 2}:{height * 2},"
          f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={fps}")
    run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", str(image), "-vf", vf,
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", str(output)])


def render(store, job: dict, config: dict, assets_path=None, audio_path=None) -> dict:
    editable(job)
    require_review(job)
    for key in ("width", "height", "fps"):
        require(type(config[key]) is int and config[key] > 0, f"{key} must be positive integer")
    require(config["width"] % 2 == 0 and config["height"] % 2 == 0, "video dimensions must be even")
    filters = run([tool("ffmpeg"), "-hide_banner", "-filters"]).stdout
    require(any(line.split()[1:2] == ["subtitles"] for line in filters.splitlines()), "FFmpeg needs libass subtitles filter")
    scenes = job["draft"]["scenes"]
    assets = read_json(assets_path) if assets_path else [None] * len(scenes)
    audio = read_json(audio_path) if audio_path else None
    require(isinstance(assets, list) and len(assets) == len(scenes), "assets JSON must list one image path/null per scene")
    require(audio is None or isinstance(audio, list) and len(audio) == len(scenes), "audio JSON must list one narration path per scene")
    output = store.directory(job) / f"render-{uuid4().hex}"
    output.mkdir()
    fonts = output / "fonts"
    fonts.mkdir()
    selected_font = font_path(config)
    shutil.copyfile(selected_font, fonts / Path(selected_font).name)
    family = ImageFont.truetype(selected_font, 16).getname()[0]
    require(re.fullmatch(r"[\w .-]+", family), "font family contains unsupported subtitle style characters")
    durations = []
    for index, scene in enumerate(scenes):
        image = output / f"frame-{index}.png"
        if assets[index] is None:
            card(scene["visual"], image, config)
        else:
            source = Path(assets[index]).expanduser().resolve()
            with Image.open(source) as asset:
                ImageOps.fit(asset.convert("RGB"), (config["width"], config["height"])).save(image)
        wav = output / f"audio-{index}.wav"
        if audio is not None:
            source_audio = Path(audio[index]).expanduser().resolve()
            require(source_audio.is_file(), "scene narration file missing")
        else:
            source_audio = output / f"speech-{index}.mp3"
            asyncio.run(edge_audio(scene["narration"], config["voice"], source_audio))
        run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", str(source_audio), "-ar", "48000", "-ac", "1", str(wav)])
        seconds = duration(wav)
        require(seconds <= 45, "scene exceeds 45 seconds; shorten narration")
        durations.append(seconds)
        animate_frame(image, output / f"scene-{index}.mp4", seconds, config, index)
    require(5 <= sum(durations) <= 180, "short must last 5-180 seconds")
    subtitles(scenes, durations, output / "captions.srt")
    for prefix, suffix in (("scene", "mp4"), ("audio", "wav")):
        (output / f"{prefix}-concat.txt").write_text("\n".join(f"file '{prefix}-{i}.{suffix}'" for i in range(len(scenes))), encoding="utf-8")
    run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-f", "concat", "-safe", "0", "-i", "scene-concat.txt",
         "-c", "copy", "merged.mp4"], cwd=output)
    run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-f", "concat", "-safe", "0", "-i", "audio-concat.txt",
         "-c", "copy", "narration.wav"], cwd=output)
    run([tool("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", "merged.mp4", "-i", "narration.wav",
         "-vf", f"subtitles=filename=captions.srt:fontsdir=fonts:force_style='FontName={family},FontSize=18,MarginV=35'",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-shortest", "-movflags", "+faststart", "video.mp4"], cwd=output)
    result = {"video": str(output / "video.mp4"), "subtitles": str(output / "captions.srt"),
              "duration": sum(durations), "scene_durations": durations, "width": config["width"], "height": config["height"],
              "draft_hash": digest(job["draft"]), "review_hash": digest(job["review"]),
              "caption_timing": "character-weighted within measured scene audio; inspect sync",
              "narration": "provided" if audio is not None else "edge-tts"}
    result["video_hash"] = file_hash(result["video"])
    result["subtitles_hash"] = file_hash(result["subtitles"])
    job["render"] = result
    job.pop("qc", None)
    job.pop("visual_review", None)
    job["status"] = "rendered"
    store.save(job)
    return {"job_id": job["id"], **result}


def require_render(job: dict):
    require_review(job)
    rendered = job.get("render", {})
    require(rendered and rendered["draft_hash"] == digest(job["draft"])
            and rendered["review_hash"] == digest(job["review"]), "render current reviewed draft first")
    require(file_hash(rendered["video"]) == rendered["video_hash"]
            and file_hash(rendered["subtitles"]) == rendered["subtitles_hash"], "rendered artifact changed; rerender and rerun QC")
    return rendered


def qc(store, job: dict) -> dict:
    editable(job)
    rendered = require_render(job)
    metadata = probe(Path(rendered["video"]))
    streams = metadata.get("streams", [])
    video = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    audio = next((stream for stream in streams if stream["codec_type"] == "audio"), None)
    require(video and audio, "QC failed: missing audio/video stream")
    seconds = float(metadata["format"]["duration"])
    require(abs(seconds - rendered["duration"]) < 0.6, "QC failed: narration/video duration mismatch")
    require(video["width"] == rendered["width"] and video["height"] == rendered["height"], "QC failed: dimensions mismatch")
    detection = run([tool("ffmpeg"), "-nostdin", "-hide_banner", "-i", rendered["video"],
                     "-vf", "blackdetect=d=0.5:pix_th=0.1", "-af", "silencedetect=noise=-45dB:d=2", "-f", "null", "-"])
    issues = []
    if re.search(r"black_start:", detection.stderr):
        issues.append("black footage >=0.5 seconds")
    if re.search(r"silence_start:", detection.stderr):
        issues.append("silence >=2 seconds")
    result = {"passed": not issues, "issues": issues, "duration": seconds, "video_hash": rendered["video_hash"],
              "subtitles_hash": rendered["subtitles_hash"], "render_hash": digest(rendered)}
    job["qc"] = result
    job["status"] = "checked" if result["passed"] else "qc_failed"
    store.save(job)
    return {"job_id": job["id"], **result, "ok": result["passed"]}
