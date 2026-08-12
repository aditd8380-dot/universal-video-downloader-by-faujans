import asyncio
import os
import re
import shutil
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


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Universal Video Downloader")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "downloads"
FRONTEND_DIR = BASE_DIR / "frontend"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# CONFIG
# ============================================================

MAX_FILE_SIZE_MB = int(
    os.getenv("MAX_FILE_SIZE_MB", "2048")
)

MAX_CONCURRENT_DOWNLOADS = int(
    os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")
)

MAX_CONCURRENT_ANALYSIS = int(
    os.getenv("MAX_CONCURRENT_ANALYSIS", "4")
)


# ============================================================
# ALLOWED DOMAINS
# ============================================================

ALLOWED_DOMAINS = {
    # TikTok
    "tiktok.com",
    "www.tiktok.com",
    "vt.tiktok.com",
    "vm.tiktok.com",

    # Instagram
    "instagram.com",
    "www.instagram.com",

    # YouTube
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

download_sem = asyncio.Semaphore(
    MAX_CONCURRENT_DOWNLOADS
)

analysis_sem = asyncio.Semaphore(
    MAX_CONCURRENT_ANALYSIS
)


# ============================================================
# REQUEST MODELS
# ============================================================

class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DownloadRequest(BaseModel):
    url: HttpUrl
    format_id: str | None = None
    container: str = "mp4"


# ============================================================
# JOB MODEL
# ============================================================

class JobInfo:

    def __init__(self, job_id: str):

        self.id = job_id

        self.status = "QUEUED"

        self.progress = 0.0

        self.message = "Queued"

        self.filename = None

        self.path = None

        self.error = None


# ============================================================
# URL HELPERS
# ============================================================

def normalize_host(host: str) -> str:

    return (
        host
        .lower()
        .rstrip(".")
        .split(":")[0]
    )


def is_allowed_url(url: str) -> bool:

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https"
    }:
        return False

    host = normalize_host(
        parsed.netloc
    )

    return any(
        host == domain
        or host.endswith("." + domain)
        for domain in ALLOWED_DOMAINS
    )


def platform_for(url: str) -> str:

    host = normalize_host(
        urlparse(url).netloc
    )

    if host.endswith("tiktok.com"):
        return "TikTok"

    if host.endswith("instagram.com"):
        return "Instagram"

    if (
        host.endswith("youtube.com")
        or host == "youtu.be"
    ):
        return "YouTube"

    return "Unknown"


# ============================================================
# FILE HELPERS
# ============================================================

def safe_filename(
    name: str,
    ext: str = "mp4"
) -> str:

    name = re.sub(
        r"[^\w\s.-]",
        "",
        name,
        flags=re.UNICODE
    )

    name = re.sub(
        r"\s+",
        "-",
        name
    ).strip("-._")

    name = name[:120] or "video"

    return f"{name}.{ext}"


def ffmpeg_available() -> bool:

    return shutil.which(
        "ffmpeg"
    ) is not None


# ============================================================
# YT-DLP EXTRACTION
# ============================================================

def extract_info(url: str) -> dict:

    if not is_allowed_url(url):

        raise ValueError(
            "Platform tidak didukung."
        )

    opts = {

        "quiet": True,

        "no_warnings": True,

        "skip_download": True,

        "noplaylist": True,

        "extract_flat": False,

        # Help YouTube use installed JS runtime/EJS.
        "js_runtimes": {
            "deno": {}
        },

        "remote_components": {
            "ejs:github"
        },
    }

    with yt_dlp.YoutubeDL(opts) as ydl:

        return ydl.extract_info(
            url,
            download=False
        )


# ============================================================
# FORMAT SERIALIZER
# ============================================================

def serialize_formats(
    info: dict
) -> list[dict]:

    result = []

    for f in info.get("formats") or []:

        # Ignore audio-only formats.
        if (
            not f.get("vcodec")
            or f.get("vcodec") == "none"
        ):
            continue

        height = f.get("height")

        if not height:
            continue

        result.append({

            "format_id":
                str(f.get("format_id")),

            "height":
                height,

            "width":
                f.get("width"),

            "ext":
                f.get("ext"),

            "fps":
                f.get("fps"),

            "filesize":
                f.get("filesize")
                or f.get("filesize_approx"),

            "has_audio":
                bool(
                    f.get("acodec")
                    and
                    f.get("acodec") != "none"
                ),
        })


    # Deduplicate
    seen = set()

    clean = []

    for f in sorted(
        result,
        key=lambda x: (
            x["height"],
            x["has_audio"]
        ),
        reverse=True
    ):

        key = (
            f["height"],
            f["ext"],
            f["has_audio"]
        )

        if key in seen:
            continue

        seen.add(key)

        clean.append(f)

    return clean[:40]


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "ok": True,
        "ffmpeg": ffmpeg_available(),
    }


# ============================================================
# ANALYZE
# ============================================================

@app.post("/api/analyze")
async def analyze(
    req: AnalyzeRequest
):

    url = str(req.url)

    if not is_allowed_url(url):

        raise HTTPException(
            400,
            "URL harus berasal dari TikTok, Instagram, atau YouTube."
        )

    async with analysis_sem:

        try:

            info = await asyncio.to_thread(
                extract_info,
                url
            )

        except Exception as e:

            # Keep a useful server-side error.
            print(
                f"[ANALYZE ERROR] {url}: {e}"
            )

            raise HTTPException(
                422,
                "Video tidak dapat dianalisis. Pastikan URL publik dan masih tersedia."
            ) from e


    return {

        "success": True,

        "platform":
            platform_for(url),

        "id":
            info.get("id"),

        "title":
            info.get("title")
            or "Untitled video",

        "thumbnail":
            info.get("thumbnail"),

        "duration":
            info.get("duration"),

        "uploader":
            info.get("uploader")
            or info.get("channel"),

        "formats":
            serialize_formats(info),
    }


# ============================================================
# PROGRESS HOOK
# ============================================================

def progress_hook(
    job: JobInfo
):

    def hook(d):

        status = d.get("status")


        if status == "downloading":

            total = (
                d.get("total_bytes")
                or
                d.get("total_bytes_estimate")
            )

            done = d.get(
                "downloaded_bytes",
                0
            )

            if total:

                job.progress = min(
                    95.0,
                    (done / total) * 95.0
                )

            speed = (
                d.get("_speed_str")
                or ""
            )

            eta = (
                d.get("_eta_str")
                or ""
            )

            parts = [
                f"Downloading {job.progress:.0f}%"
            ]

            if speed:
                parts.append(speed)

            if eta:
                parts.append(
                    f"ETA {eta}"
                )

            job.message = " ".join(parts)


        elif status == "finished":

            job.progress = max(
                job.progress,
                96.0
            )

            job.message = (
                "Merging/finalizing media..."
            )


    return hook


# ============================================================
# DOWNLOAD ENGINE
# ============================================================

def download_sync(
    job: JobInfo,
    url: str,
    requested_format_id: str | None
):

    if not ffmpeg_available():

        raise RuntimeError(
            "FFmpeg belum terpasang di server."
        )


    job.status = "DOWNLOADING"

    job.message = (
        "Starting download..."
    )


    work = Path(
        tempfile.mkdtemp(
            prefix=f"{job.id}-",
            dir=TEMP_DIR
        )
    )


    try:

        # ----------------------------------------------------
        # FORMAT SELECTION
        # ----------------------------------------------------

        if requested_format_id:

            # Selected video + best audio.
            fmt = (
                f"{requested_format_id}"
                "+bestaudio/best"
            )

        else:

            # Best available video + audio.
            fmt = (
                "bestvideo*"
                "+bestaudio/best"
            )


        # ----------------------------------------------------
        # OUTPUT TEMPLATE
        # ----------------------------------------------------

        outtmpl = str(
            work /
            "%(title).100s-%(id)s.%(ext)s"
        )


        # ----------------------------------------------------
        # YT-DLP OPTIONS
        # ----------------------------------------------------

        opts = {

            "quiet": True,

            "no_warnings": True,

            "noplaylist": True,

            "format": fmt,

            "merge_output_format": "mp4",

            "outtmpl": outtmpl,

            "progress_hooks": [
                progress_hook(job)
            ],

            "retries": 5,

            "fragment_retries": 5,

            "socket_timeout": 60,

            "restrictfilenames": True,

            "paths": {
                "home": str(work)
            },

            # YouTube JS/EJS support.
            "js_runtimes": {
                "deno": {}
            },

            "remote_components": {
                "ejs:github"
            },
        }


        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )


            # Expected filename.
            requested = Path(
                ydl.prepare_filename(info)
            )


        # ----------------------------------------------------
        # FIND OUTPUT
        # ----------------------------------------------------

        candidates = [
            p
            for p in work.glob("*")
            if p.is_file()
        ]


        mp4s = [
            p
            for p in candidates
            if p.suffix.lower() == ".mp4"
        ]


        if mp4s:

            # Prefer the largest MP4.
            source = max(
                mp4s,
                key=lambda p: p.stat().st_size
            )

        elif requested.exists():

            source = requested

        elif candidates:

            source = max(
                candidates,
                key=lambda p: p.stat().st_size
            )

        else:

            raise RuntimeError(
                "File hasil tidak ditemukan."
            )


        # ----------------------------------------------------
        # VALIDATE FILE
        # ----------------------------------------------------

        if (
            not source.exists()
            or source.stat().st_size <= 0
        ):

            raise RuntimeError(
                "File hasil kosong atau tidak tersedia."
            )


        # ----------------------------------------------------
        # MAX FILE SIZE
        # ----------------------------------------------------

        max_bytes = (
            MAX_FILE_SIZE_MB
            * 1024
            * 1024
        )


        if source.stat().st_size > max_bytes:

            raise RuntimeError(
                "File hasil melebihi batas ukuran server."
            )


        # ----------------------------------------------------
        # MOVE/COPY TO FINAL STORAGE
        # ----------------------------------------------------

        final_name = safe_filename(
            info.get("title")
            or "video",
            "mp4"
        )


        final_path = (
            OUTPUT_DIR
            /
            f"{job.id}-{final_name}"
        )


        shutil.copy2(
            source,
            final_path
        )


        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        job.path = str(
            final_path
        )

        job.filename = final_name

        job.progress = 100.0

        job.status = "COMPLETED"

        job.message = (
            "Download completed."
        )


    finally:

        shutil.rmtree(
            work,
            ignore_errors=True
        )


# ============================================================
# BACKGROUND JOB
# ============================================================

async def run_download(
    job: JobInfo,
    url: str,
    format_id: str | None
):

    async with download_sem:

        try:

            await asyncio.to_thread(
                download_sync,
                job,
                url,
                format_id
            )


        except asyncio.CancelledError:

            job.status = "CANCELLED"

            job.message = (
                "Download cancelled."
            )

            raise


        except Exception as e:

            print(
                f"[DOWNLOAD ERROR] {url}: {e}"
            )

            job.status = "FAILED"

            job.error = (
                "Download gagal. "
                "Cek URL, ketersediaan video, "
                "FFmpeg, atau konfigurasi yt-dlp."
            )

            job.message = job.error


# ============================================================
# CREATE DOWNLOAD JOB
# ============================================================

@app.post("/api/download")
async def create_download(
    req: DownloadRequest
):

    url = str(req.url)


    if not is_allowed_url(url):

        raise HTTPException(
            400,
            "URL platform tidak didukung."
        )


    # --------------------------------------------------------
    # IMPORTANT:
    # Do NOT call extract_info() here again.
    #
    # The analyze endpoint already did the metadata work.
    # Create the job immediately so the frontend gets job_id
    # without waiting for another YouTube extraction.
    # --------------------------------------------------------

    job = JobInfo(
        uuid.uuid4().hex
    )


    jobs[job.id] = job


    asyncio.create_task(
        run_download(
            job,
            url,
            req.format_id
        )
    )


    return {

        "success": True,

        "job_id":
            job.id
    }


# ============================================================
# DOWNLOAD STATUS
# ============================================================

@app.get(
    "/api/download/{job_id}"
)
async def download_status(
    job_id: str
):

    job = jobs.get(job_id)


    if not job:

        raise HTTPException(
            404,
            "Job tidak ditemukan."
        )


    return {

        "job_id":
            job.id,

        "status":
            job.status,

        "progress":
            round(
                job.progress,
                1
            ),

        "message":
            job.message,

        "filename":
            job.filename,

        "error":
            job.error,
    }


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.get(
    "/api/download/{job_id}/file"
)
async def get_file(
    job_id: str
):

    job = jobs.get(job_id)


    if (
        not job
        or job.status != "COMPLETED"
        or not job.path
    ):

        raise HTTPException(
            404,
            "File belum siap."
        )


    path = Path(
        job.path
    )


    if not path.exists():

        raise HTTPException(
            404,
            "File sudah tidak tersedia."
        )


    return FileResponse(
        path,
        media_type="video/mp4",
        filename=job.filename,
    )


# ============================================================
# CANCEL DOWNLOAD
# ============================================================

@app.post(
    "/api/download/{job_id}/cancel"
)
async def cancel_download(
    job_id: str
):

    job = jobs.get(job_id)


    if not job:

        raise HTTPException(
            404,
            "Job tidak ditemukan."
        )


    if job.status in {
        "COMPLETED",
        "FAILED",
        "CANCELLED"
    }:

        return {

            "success": False,

            "message":
                "Job sudah selesai."
        }


    job.status = "CANCELLED"

    job.message = (
        "Cancellation requested."
    )


    return {
        "success": True
    }


# ============================================================
# FRONTEND
# ============================================================

if FRONTEND_DIR.exists():

    app.mount(
        "/",
        StaticFiles(
            directory=str(
                FRONTEND_DIR
            ),
            html=True
        ),
        name="frontend"
)
