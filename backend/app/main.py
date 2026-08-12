import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

BASE_DIR = Path(__file__).resolve().parents[2]
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "downloads"
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2048"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
MAX_CONCURRENT_ANALYSIS = int(os.getenv("MAX_CONCURRENT_ANALYSIS", "4"))

ALLOWED_DOMAINS = {
    "tiktok.com", "www.tiktok.com", "vt.tiktok.com", "vm.tiktok.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
}

jobs = {}
download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
analysis_sem = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DownloadRequest(BaseModel):
    url: HttpUrl
    format_id: str | None = None
    container: str = "mp4"


class JobInfo:
    def __init__(self, job_id: str):
        self.id = job_id
        self.status = "QUEUED"
        self.progress = 0.0
        self.message = "Queued"
        self.filename = None
        self.path = None
        self.error = None


def normalize_host(host: str) -> str:
    return host.lower().rstrip(".").split(":")[0]


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = normalize_host(parsed.netloc)
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def platform_for(url: str) -> str:
    host = normalize_host(urlparse(url).netloc)
    if host.endswith("tiktok.com"):
        return "TikTok"
    if host.endswith("instagram.com"):
        return "Instagram"
    if host.endswith("youtube.com") or host == "youtu.be":
        return "YouTube"
    return "Unknown"


def safe_filename(name: str, ext: str = "mp4") -> str:
    name = re.sub(r"[^\w\s.-]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "-", name).strip("-._")
    name = name[:120] or "video"
    return f"{name}.{ext}"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_info(url: str) -> dict:
    if not is_allowed_url(url):
        raise ValueError("Platform tidak didukung atau URL tidak diizinkan.")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def serialize_formats(info: dict) -> list[dict]:
    result = []
    for f in info.get("formats") or []:
        # Only expose formats that have a video stream. Audio-only formats
        # are not presented as video choices in this MVP.
        if not f.get("vcodec") or f.get("vcodec") == "none":
            continue
        height = f.get("height")
        if not height:
            continue
        result.append({
            "format_id": str(f.get("format_id")),
            "height": height,
            "width": f.get("width"),
            "ext": f.get("ext"),
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "has_audio": bool(f.get("acodec") and f.get("acodec") != "none"),
        })

    # Deduplicate by resolution/ext/audio availability.
    seen = set()
    clean = []
    for f in sorted(result, key=lambda x: (x["height"], x["has_audio"]), reverse=True):
        key = (f["height"], f["ext"], f["has_audio"])
        if key not in seen:
            seen.add(key)
            clean.append(f)
    return clean[:40]


@app.get("/api/health")
async def health():
    return {"ok": True, "ffmpeg": ffmpeg_available()}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    url = str(req.url)
    if not is_allowed_url(url):
        raise HTTPException(400, "URL harus berasal dari TikTok, Instagram, atau YouTube.")

    async with analysis_sem:
        try:
            info = await asyncio.to_thread(extract_info, url)
        except Exception as e:
            raise HTTPException(422, "Video tidak dapat dianalisis. Pastikan URL publik dan masih tersedia.") from e

    return {
        "success": True,
        "platform": platform_for(url),
        "id": info.get("id"),
        "title": info.get("title") or "Untitled video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "formats": serialize_formats(info),
    }


def progress_hook(job: JobInfo):
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            if total:
                job.progress = min(95.0, (done / total) * 95.0)
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            job.message = f"Downloading {job.progress:.0f}% {speed} ETA {eta}".strip()
        elif status == "finished":
            job.progress = 96.0
            job.message = "Merging/finalizing media..."
    return hook


def download_sync(job: JobInfo, url: str, requested_format_id: str | None):
    if not ffmpeg_available():
        raise RuntimeError("FFmpeg belum terpasang di server.")

    job.status = "DOWNLOADING"
    job.message = "Starting download..."

    work = Path(tempfile.mkdtemp(prefix=f"{job.id}-", dir=TEMP_DIR))
    try:
        # If the user chooses a format id, prefer that video stream with
        # best available audio. Otherwise use the best compatible MP4 output.
        if requested_format_id:
            fmt = f"{requested_format_id}+bestaudio/best"
        else:
            fmt = "bestvideo*+bestaudio/best"

        outtmpl = str(work / "%(title).100s-%(id)s.%(ext)s")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "progress_hooks": [progress_hook(job)],
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "restrictfilenames": True,
            "paths": {"home": str(work)},
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = Path(ydl.prepare_filename(info))
            candidates = list(work.glob("*"))
            mp4s = [p for p in candidates if p.suffix.lower() == ".mp4"]
            source = mp4s[0] if mp4s else (requested if requested.exists() else candidates[0])

        if not source.exists() or source.stat().st_size <= 0:
            raise RuntimeError("File hasil tidak ditemukan atau kosong.")

        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        if source.stat().st_size > max_bytes:
            raise RuntimeError("File hasil melebihi batas ukuran server.")

        final_name = safe_filename(info.get("title") or "video", "mp4")
        final_path = OUTPUT_DIR / f"{job.id}-{final_name}"
        shutil.copy2(source, final_path)

        job.path = str(final_path)
        job.filename = final_name
        job.progress = 100.0
        job.status = "COMPLETED"
        job.message = "Download completed."
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def run_download(job: JobInfo, url: str, format_id: str | None):
    async with download_sem:
        try:
            await asyncio.to_thread(download_sync, job, url, format_id)
        except asyncio.CancelledError:
            job.status = "CANCELLED"
            job.message = "Download cancelled."
            raise
        except Exception:
            job.status = "FAILED"
            job.error = "Download gagal. Cek URL, ketersediaan video, dan instalasi FFmpeg."
            job.message = job.error


@app.post("/api/download")
async def create_download(req: DownloadRequest):
    url = str(req.url)
    if not is_allowed_url(url):
        raise HTTPException(400, "URL platform tidak didukung.")

    # Verify metadata before creating a job.
    try:
        info = await asyncio.to_thread(extract_info, url)
    except Exception as e:
        raise HTTPException(422, "Video tidak dapat diproses dari URL tersebut.") from e

    allowed_ids = {str(f.get("format_id")) for f in info.get("formats") or []}
    if req.format_id and req.format_id not in allowed_ids:
        raise HTTPException(400, "Format video yang dipilih tidak tersedia.")

    job = JobInfo(uuid.uuid4().hex)
    jobs[job.id] = job
    asyncio.create_task(run_download(job, url, req.format_id))

    return {"success": True, "job_id": job.id}


@app.get("/api/download/{job_id}")
async def download_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": round(job.progress, 1),
        "message": job.message,
        "filename": job.filename,
        "error": job.error,
    }


@app.get("/api/download/{job_id}/file")
async def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job.status != "COMPLETED" or not job.path:
        raise HTTPException(404, "File belum siap.")
    path = Path(job.path)
    if not path.exists():
        raise HTTPException(404, "File sudah tidak tersedia.")
    return FileResponse(path, media_type="video/mp4", filename=job.filename)


@app.post("/api/download/{job_id}/cancel")
async def cancel_download(job_id: str):
    # This MVP marks the job cancelled. A production deployment should keep
    # a Task reference per job and terminate the underlying process safely.
    job = jobs.get(job_id)

    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    if job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return {"success": False, "message": "Job sudah selesai."}
    job.status = "CANCELLED"
    job.message = "Cancellation requested."
    return {"success": True}

from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")



