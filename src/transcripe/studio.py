import os
import re
import glob
import random
import shutil
import secrets
import sys
import tempfile
import threading
import subprocess
import time
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel

app = FastAPI(title="Transcripe Studio API")

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_web_dist() -> str:
    """Locate the built studio UI.

    Wheels can ship it inside the package; a git checkout builds it into
    web/dist. Either way the API runs fine on its own if neither exists.
    """
    override = os.environ.get("TRANSCRIPE_WEB_DIST")
    candidates = [override] if override else []
    candidates.append(os.path.join(_HERE, "web_dist"))
    # src/transcripe/studio.py → repo root
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(_HERE)), "web", "dist")
    )
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return ""


WEB_DIST = _find_web_dist()

# Same-origin in production (served under alabed.work/transcripe/ + /api/),
# plus localhost for dev. allow_credentials with "*" is invalid, so we list origins.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "TRANSCRIPE_ORIGINS",
        # site, Vite dev, Expo web dev (native apps aren't subject to CORS)
        "https://alabed.work,https://www.alabed.work,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8081,http://localhost:19006",
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- Access control -------------------------------------------------------
# Loopback-only (the default) needs no ceremony. The moment the studio is
# reachable from the network, work is gated on a shared token: these endpoints
# fetch arbitrary URLs — with your browser cookies — and write to your disk,
# so an open port on a café network is not something to hand out by accident.
AUTH_TOKEN = os.environ.get("TRANSCRIPE_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(float(os.environ.get("TRANSCRIPE_MAX_UPLOAD_MB", "2048")) * 1024 * 1024)

# ffmpeg and yt-dlp are slow by nature: a two-hour recording is a normal input,
# so the old flat two minutes turned ordinary work into a 504.
JOB_TIMEOUT = int(os.environ.get("TRANSCRIPE_TIMEOUT", "1800"))

# x264 speed/size trade. "veryfast" is ~25% quicker than "medium" at a similar
# file size for the kind of material people convert here.
X264_PRESET = os.environ.get("TRANSCRIPE_PRESET", "veryfast")

# Heavy work runs in worker threads (see run_blocking) so the event loop stays
# free to answer heartbeats. This bounds how many run at once: ffmpeg already
# uses every core, so piling jobs on only makes them all slower.
MAX_PARALLEL_JOBS = max(1, int(os.environ.get(
    "TRANSCRIPE_PARALLEL", str(max(2, (os.cpu_count() or 4) // 2)))))
_work_slots = threading.BoundedSemaphore(MAX_PARALLEL_JOBS)


async def run_blocking(fn, *args, **kwargs):
    """Run blocking work in a thread, one of MAX_PARALLEL_JOBS at a time."""
    import asyncio
    import functools

    def guarded():
        with _work_slots:
            return fn(*args, **kwargs)

    return await asyncio.get_running_loop().run_in_executor(
        None, functools.partial(guarded))


def require_token(request: Request) -> None:
    """Gate an endpoint on the shared token, when one is configured."""
    if not AUTH_TOKEN:
        return
    supplied = (
        request.headers.get("x-transcripe-token")
        or request.query_params.get("token")
        or ""
    )
    if not secrets.compare_digest(supplied, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Missing or wrong studio token")

# --- Scalable Scraper Pool & Rotator ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

COOKIE_PROFILES = [
    "chrome",
    "firefox",
    "brave",
    "edge",
    "chromium"
]

class UrlConvertRequest(BaseModel):
    url: str
    format: str = "mp3"
    useBrowserCookies: bool = True
    # "best" takes the highest resolution available; "compatible" sticks to
    # H.264, which older players need but which YouTube caps at 1080p.
    quality: str = "best"
    # "stream" hands the bytes back on this request (browser default).
    # "link" parks the result and returns a one-shot URL, so native clients
    # can stream it straight to disk instead of buffering it in memory.
    deliver: str = "stream"


# --- One-shot result handoff for native clients ---
RESULT_TTL_SECONDS = 900
_results: dict = {}
_results_lock = threading.Lock()


def _reap_results() -> None:
    now = time.time()
    with _results_lock:
        stale = [t for t, r in _results.items() if r["expires"] < now]
        for token in stale:
            shutil.rmtree(_results.pop(token)["dir"], ignore_errors=True)


def stash_result(path: str, temp_dir: str, filename: str) -> dict:
    """Park a finished file behind a single-use token."""
    _reap_results()
    token = secrets.token_urlsafe(24)
    with _results_lock:
        _results[token] = {
            "path": path,
            "dir": temp_dir,
            "name": filename,
            "expires": time.time() + RESULT_TTL_SECONDS,
        }
    return {"download": f"/api/result/{token}", "filename": filename}


@app.get("/api/result/{token}")
def fetch_result(token: str, request: Request):
    require_token(request)
    with _results_lock:
        entry = _results.pop(token, None)
    if not entry or not os.path.exists(entry["path"]):
        raise HTTPException(status_code=404, detail="Result expired or already downloaded")
    return FileResponse(
        path=entry["path"],
        filename=entry["name"],
        media_type="application/octet-stream",
        background=BackgroundTask(shutil.rmtree, entry["dir"], ignore_errors=True),
    )

def get_rotated_user_agent() -> str:
    return random.choice(USER_AGENTS)


# Files yt-dlp leaves beside the media: thumbnails, metadata, partial fragments.
SIDECAR_EXTS = {".part", ".ytdl", ".json", ".info", ".description", ".annotations",
                ".jpg", ".jpeg", ".png", ".webp", ".lrc", ".txt", ".temp"}


def pick_download(temp_dir: str) -> str:
    """The media file among whatever the downloader dropped in the directory.

    Sorting by size beats taking the first name back from glob: a thumbnail or
    a leftover .part would otherwise get handed to the user as their video.
    """
    candidates = []
    for path in glob.glob(os.path.join(temp_dir, "*")):
        if not os.path.isfile(path):
            continue
        if os.path.splitext(path)[1].lower() in SIDECAR_EXTS:
            continue
        size = os.path.getsize(path)
        if size > 0:
            candidates.append((size, path))
    if not candidates:
        return ""
    return max(candidates)[1]

def tool_path(name: str) -> str:
    """Resolve a helper binary: our own interpreter's dir, project venv, PATH."""
    own_bin = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(own_bin):
        return own_bin
    repo_root = os.path.dirname(os.path.dirname(_HERE))
    for env in (".venv", "venv"):
        cand = os.path.join(repo_root, env, "bin", name)
        if os.path.exists(cand):
            return cand
    return name

def get_rotated_cookie_flag(allow_browser: bool = True) -> list:
    """Cookie flags for the retry pass.

    An exported cookie file is an explicit, deliberate opt-in, so it is always
    honored. Reading cookies straight out of a live browser profile is not, so
    it happens only when the caller asked for it.
    """
    config_cookies = glob.glob(os.path.expanduser("~/.config/transcripe/cookies*.txt"))
    if config_cookies:
        selected_file = random.choice(config_cookies)
        return ["--cookies", selected_file]

    if not allow_browser:
        return []

    profile = random.choice(COOKIE_PROFILES)
    return ["--cookies-from-browser", profile]

# --- Telemetry -------------------------------------------------------------
# Small, in-memory, and reset with the process: enough for the studio to show
# what the engine is actually doing rather than a decorative animation.
_started_at = time.time()
_stats_lock = threading.Lock()
_stats = {"jobs": 0, "failed": 0, "bytes_in": 0, "bytes_out": 0, "seconds": 0.0}
_recent: list = []
_active: dict = {}


def record_job(kind: str, name: str, target: str, bytes_in: int, bytes_out: int,
               seconds: float, ok: bool) -> None:
    with _stats_lock:
        _stats["jobs"] += 1
        if not ok:
            _stats["failed"] += 1
        _stats["bytes_in"] += max(0, bytes_in)
        _stats["bytes_out"] += max(0, bytes_out)
        _stats["seconds"] += max(0.0, seconds)
        _recent.insert(0, {
            "kind": kind,
            "name": name[:60],
            "target": target,
            "bytes": bytes_out,
            "seconds": round(seconds, 2),
            "ok": ok,
            "at": time.time(),
        })
        del _recent[24:]


class track_job:
    """Time a unit of work and publish it while it runs."""

    def __init__(self, kind: str, name: str, target: str, bytes_in: int = 0):
        self.kind, self.name, self.target, self.bytes_in = kind, name, target, bytes_in
        self.id = secrets.token_urlsafe(6)
        self.bytes_out = 0

    def __enter__(self):
        self.t0 = time.time()
        with _stats_lock:
            _active[self.id] = {"kind": self.kind, "name": self.name[:60],
                                "target": self.target, "since": self.t0}
        return self

    def __exit__(self, exc_type, exc, tb):
        with _stats_lock:
            _active.pop(self.id, None)
        record_job(self.kind, self.name, self.target, self.bytes_in,
                   self.bytes_out, time.time() - self.t0, exc_type is None)
        return False


@app.get("/api/stats")
def stats(request: Request):
    """What this engine has done since it started."""
    require_token(request)
    with _stats_lock:
        totals = dict(_stats)
        recent = list(_recent)
        active = [
            {**job, "elapsed": round(time.time() - job["since"], 1)}
            for job in _active.values()
        ]
    return {
        "uptime": round(time.time() - _started_at),
        "cores": os.cpu_count(),
        "parallel": MAX_PARALLEL_JOBS,
        "totals": totals,
        "active": active,
        "recent": recent,
    }


def engine_features() -> dict:
    """What this particular engine can actually do.

    A studio on a small server may have ffmpeg but no Whisper; clients use
    this to stop offering a conversion that is certain to fail. find_spec
    avoids importing these heavy modules just to answer a heartbeat.
    """
    import importlib.util

    return {
        "media": bool(shutil.which("ffmpeg")),
        "images": importlib.util.find_spec("PIL") is not None,
        "transcribe": importlib.util.find_spec("faster_whisper") is not None,
        "download": bool(shutil.which(tool_path("yt-dlp")) or tool_path("yt-dlp") != "yt-dlp"),
        # Without a JS runtime YouTube drops formats and often refuses outright.
        "js_runtime": bool(js_runtime_args()),
    }


@app.get("/api/health")
def health_check(request: Request):
    """Open on purpose, so a client can tell a stopped engine from a locked
    one. Says whether a token is required and whether this one passes."""
    return {
        "status": "ok",
        "engine": "Transcripe Studio",
        "auth_required": bool(AUTH_TOKEN),
        "authorized": _token_ok(request),
        "max_upload_mb": round(MAX_UPLOAD_BYTES / 1024 / 1024),
        "features": engine_features(),
    }


# Which codecs each container can legally hold. Used to decide whether a
# conversion is really just a change of wrapper.
CONTAINER_CODECS = {
    "mp4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "ac3", "alac"}),
    "mov": ({"h264", "hevc", "prores", "mpeg4"}, {"aac", "pcm_s16le", "alac"}),
    "mkv": ({"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "theora"},
            {"aac", "mp3", "opus", "vorbis", "flac", "ac3", "pcm_s16le"}),
    "webm": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
    "m4a": (set(), {"aac", "alac"}),
    "mp3": (set(), {"mp3"}),
    "flac": (set(), {"flac"}),
    "wav": (set(), {"pcm_s16le", "pcm_s24le", "pcm_f32le"}),
    "ogg": (set(), {"vorbis", "opus", "flac"}),
    "opus": (set(), {"opus"}),
}

FFPROBE_TIMEOUT = 20


def probe_streams(path: str) -> list:
    """[(codec_type, codec_name)] for a media file, empty if ffprobe can't read it."""
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        res = subprocess.run(
            [probe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    streams = []
    for line in res.stdout.splitlines():
        parts = [p for p in line.strip().split(",") if p]
        if len(parts) == 2:
            # ffprobe emits "name,type" or "type,name" depending on order; both
            # orders appear in the wild, so accept either.
            a, b = parts
            streams.append((b, a) if b in ("video", "audio") else (a, b))
    return streams


def can_remux(input_path: str, target_fmt: str, want_audio_only: bool) -> bool:
    """True when the target container already accepts what's inside.

    Re-encoding a file whose streams are already in the right codec costs
    minutes and *loses* quality; copying the streams is near-instant and
    lossless. This is the difference between 2.8 s and 0.03 s on a 60 s clip.
    """
    allowed = CONTAINER_CODECS.get(target_fmt)
    if not allowed:
        return False
    video_ok, audio_ok = allowed
    streams = probe_streams(input_path)
    if not streams:
        return False

    seen_audio = False
    for kind, codec in streams:
        if kind == "video":
            # An audio target drops video, so a video stream doesn't block it;
            # for a video target the codec has to fit the container.
            if want_audio_only:
                continue
            if codec not in video_ok:
                return False
        elif kind == "audio":
            seen_audio = True
            if codec not in audio_ok:
                return False
    # A silent file in an audio container would produce nothing at all.
    return seen_audio or not want_audio_only


AUDIO_TARGETS = {"mp3", "m4a", "wav", "aac", "opus", "flac", "ogg"}

# YouTube now hands out a JavaScript challenge that yt-dlp has to run, and it
# only enables Deno by default. Without a runtime it warns, drops formats, and
# often fails outright — so hand it whichever engine this machine already has.
JS_RUNTIMES = ("deno", "node", "bun", "qjs")


def js_runtime_args() -> list:
    for runtime in JS_RUNTIMES:
        path = shutil.which(runtime)
        if path:
            return ["--js-runtimes", f"{runtime}:{path}"]
    return []


def ytdlp_quality_args(target_fmt: str, quality: str = "best") -> list:
    """Which stream yt-dlp should take, and how well to keep it.

    The old selector demanded avc1, which on YouTube means H.264 — and H.264
    tops out at 1080p there. Measured on a 4K source it fetched 1920x1080 when
    3840x2160 AV1 was sitting right next to it. Quality is the point of this
    tool, so sort by resolution first and merely *prefer* the widely-playable
    codecs, rather than refusing everything else.

    quality="compatible" restores the old behaviour for anyone feeding an old
    TV or editor that only speaks H.264.
    """
    if target_fmt in AUDIO_TARGETS:
        # yt-dlp's default --audio-quality is 5: roughly 130 kbps VBR for mp3.
        # 0 is the best the encoder offers.
        return ["-f", "ba/b", "-S", "abr,acodec", "--audio-quality", "0"]

    if quality == "compatible":
        return ["-f", ("bv*[vcodec^=avc1]+ba[ext=m4a]/b[vcodec^=avc1]/"
                       "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"),
                "-S", "res,fps"]

    # Highest resolution wins; among equals prefer AV1 then H.264 (both sit in
    # an mp4 happily) over VP9, and the richer audio track.
    return ["-f", "bv*+ba/b", "-S", "res,fps,hdr:12,vcodec:av01:avc1,channels,acodec"]


def ffmpeg_reason(stderr: str) -> str:
    """The line that actually explains the failure.

    ffmpeg opens with a version banner and its whole build configuration, so
    taking the first 200 characters showed users a wall of compile flags and
    cut off the real message, which comes last.
    """
    noise = ("ffmpeg version", "built with", "configuration:", "lib", "  ")
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    meaningful = [
        ln for ln in lines
        if not ln.startswith(noise) and not re.match(r"^\s*(Input|Output) #", ln)
    ]
    for line in reversed(meaningful):
        if any(word in line.lower() for word in
               ("error", "invalid", "unable", "no such", "not found",
                "unsupported", "does not", "failed", "denied")):
            return line[:180]
    return meaningful[-1][:180] if meaningful else ""


async def save_upload(file: UploadFile, temp_dir: str) -> tuple[str, str]:
    """Stream an upload to disk under the size cap. Returns (name, path).

    Copying in chunks with a running total keeps an unbounded upload from
    being a way to fill the disk of whoever is running the studio.
    """
    # Never trust the client filename — strip any path components (traversal).
    safe_in = os.path.basename(file.filename or "upload")
    # Keep room for the "_converted.<ext>" suffix inside the 255-byte limit
    # most filesystems enforce, so a long name can't fail the write itself.
    stem, dot_ext = os.path.splitext(safe_in)
    if len(safe_in) > 120:
        safe_in = stem[:100] + dot_ext[:20]
    input_path = os.path.join(temp_dir, safe_in)
    written = 0
    with open(input_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                f.close()
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File is larger than the {round(MAX_UPLOAD_BYTES / 1024 / 1024)} MB limit "
                           f"(raise TRANSCRIPE_MAX_UPLOAD_MB to allow more).",
                )
            f.write(chunk)
    if written == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"{safe_in} is empty — nothing to convert.")
    return safe_in, input_path


def _token_ok(request: Request) -> bool:
    try:
        require_token(request)
        return True
    except HTTPException:
        return False


# --- Transcription jobs ---------------------------------------------------
# Whisper takes minutes, not milliseconds, and the first run may download a
# multi-gigabyte model, so this can't ride on a single request. Start a job,
# poll it, collect the file through the same one-shot handoff as everything else.
JOB_TTL_SECONDS = 3600
_jobs: dict = {}
_jobs_lock = threading.Lock()
# One Whisper model instance is shared and cached across jobs; running two
# transcriptions through it at once is not safe, so they queue.
_whisper_lock = threading.Lock()


def _job_update(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields, touched=time.time())


def _reap_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        for job_id in [j for j, r in _jobs.items() if r.get("touched", 0) < cutoff]:
            _jobs.pop(job_id, None)


def _reap_orphan_temp_dirs() -> None:
    """Delete our own temp directories that nothing is waiting on.

    A client that disconnects mid-conversion leaves the response — and its
    cleanup task — unsent, so the directory would otherwise sit there until
    the machine reboots.
    """
    with _results_lock:
        live = {entry["dir"] for entry in _results.values()}
    cutoff = time.time() - max(3600, JOB_TIMEOUT * 2)
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "transcripe_*")):
        if path in live or not os.path.isdir(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _sweeper() -> None:
    """Expire abandoned results, job records, and stranded temp dirs on a timer.

    Reaping only when new work arrives meant a result nobody collected kept its
    temp directory for as long as the studio stayed idle.
    """
    while True:
        time.sleep(300)
        try:
            _reap_results()
            _reap_jobs()
            _reap_orphan_temp_dirs()
        except Exception:
            pass


threading.Thread(target=_sweeper, daemon=True, name="transcripe-sweeper").start()


def _run_transcribe(job_id: str, input_path: str, temp_dir: str, fmt: str, translate: bool) -> None:
    import io
    from pathlib import Path

    from rich.console import Console

    from transcripe.engines.audio_video import MODEL_SIZE, transcribe

    def cancelled() -> bool:
        with _jobs_lock:
            return bool(_jobs.get(job_id, {}).get("cancelled"))

    try:
        if not _whisper_lock.acquire(blocking=False):
            _job_update(job_id, status="running", stage="waiting for the transcriber")
            _whisper_lock.acquire()
        try:
            # Someone gave up while this waited its turn — don't start at all.
            if cancelled():
                shutil.rmtree(temp_dir, ignore_errors=True)
                _job_update(job_id, status="cancelled", stage="")
                return
            _job_update(job_id, status="running",
                        stage=f"loading Whisper ({MODEL_SIZE}) — the first run downloads it")
            src = Path(input_path)
            out_path = src.with_suffix(f".{fmt}")
            # The engine narrates to a Rich console; give it one that goes nowhere.
            with track_job("transcribe", src.name, fmt, src.stat().st_size) as job:
                transcribe(src, fmt, Console(file=io.StringIO()), output_path=out_path,
                           translate=translate)
                if out_path.exists():
                    job.bytes_out = out_path.stat().st_size
        finally:
            _whisper_lock.release()
        if not out_path.exists():
            raise RuntimeError("Whisper produced no output")
        if cancelled():
            # Free the disk now instead of parking a transcript nobody wants.
            shutil.rmtree(temp_dir, ignore_errors=True)
            _job_update(job_id, status="cancelled", stage="")
            return
        _job_update(job_id, status="done", stage="", **stash_result(
            str(out_path), temp_dir, out_path.name))
    except Exception as e:  # surfaced verbatim to the client
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = str(e) or e.__class__.__name__
        if "faster_whisper" in detail or isinstance(e, ImportError):
            detail = ("Transcription needs Whisper — "
                      "pip install 'transcripe[whisper]'")
        _job_update(job_id, status="error", stage="", detail=detail[:300])


@app.post("/api/transcribe")
async def start_transcribe(
    request: Request,
    file: UploadFile = File(...),
    targetFormat: str = Form("srt"),
    translate: bool = Form(False),
):
    """Kick off a transcription and hand back a job id to poll."""
    require_token(request)
    fmt = "srt" if targetFormat.lower().strip().lstrip(".") == "srt" else "txt"

    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Transcription needs Whisper — pip install 'transcripe[whisper]'")

    temp_dir = tempfile.mkdtemp(prefix="transcripe_stt_")
    _, input_path = await save_upload(file, temp_dir)

    _reap_jobs()
    job_id = secrets.token_urlsafe(12)
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "stage": "queued", "touched": time.time()}
    threading.Thread(
        target=_run_transcribe,
        args=(job_id, input_path, temp_dir, fmt, translate),
        daemon=True,
    ).start()

    from transcripe.engines.audio_video import MODEL_SIZE
    return {"job": job_id, "model": MODEL_SIZE}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    require_token(request)
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job (it may have expired)")
    return {k: v for k, v in job.items() if k != "touched"}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    """Give up on a transcription.

    A job still waiting its turn never starts; one already inside Whisper runs
    to the end (the model can't be interrupted mid-file) but throws its output
    away instead of parking a transcript nobody asked for.
    """
    require_token(request)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job (it may have expired)")
        job["cancelled"] = True
        job["touched"] = time.time()
        status = job.get("status")
    return {"status": "cancelled" if status in ("queued", "running") else status}


@app.post("/api/convert/url")
async def convert_url(req: UrlConvertRequest, request: Request):
    require_token(request)
    url = req.url.strip()
    target_fmt = req.format.lower().replace(".", "").strip() or "mp3"
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    temp_dir = tempfile.mkdtemp(prefix="transcripe_dl_")

    def download() -> str:
        music_domains = ["spotify.com", "music.apple.com", "deezer.com", "tidal.com", "soundcloud.com", "bandcamp.com"]
        if any(domain in url.lower() for domain in music_domains):
            # spotdl high-fidelity resolution
            cmd = [
                tool_path("spotdl"),
                url,
                "--output", temp_dir,
                "--format", target_fmt if target_fmt in ["mp3", "m4a", "flac", "ogg", "opus"] else "mp3"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=JOB_TIMEOUT)
            picked = pick_download(temp_dir)

            # Fallback to rotated yt-dlp audio extractor
            if not picked:
                out_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
                yt_cmd = [
                    tool_path("yt-dlp"),
                    "-x", "--audio-format", target_fmt if target_fmt in ["mp3", "m4a", "wav", "aac", "opus", "flac"] else "mp3",
                    "--audio-quality", "0",
                    *js_runtime_args(),
                    "-o", out_template,
                    "--user-agent", get_rotated_user_agent(),
                    "--geo-bypass",
                    url
                ]
                subprocess.run(yt_cmd, capture_output=True, text=True, timeout=JOB_TIMEOUT)
                picked = pick_download(temp_dir)

            if not picked:
                raise HTTPException(status_code=500, detail=f"Music extraction failed for {url}")
            return picked
        else:
            # Scalable yt-dlp extraction with credential & browser profile rotation
            out_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
            
            common_flags = js_runtime_args() + [
                "--user-agent", get_rotated_user_agent(),
                "--referer", url,
                "--no-check-certificates",
                "--geo-bypass",
                "--ignore-errors",
                "--no-playlist",
                "--concurrent-fragments", "4",
            ]

            yt_bin = tool_path("yt-dlp")
            
            quality_args = ytdlp_quality_args(target_fmt, req.quality)
            if target_fmt in AUDIO_TARGETS:
                yt_opts = ([yt_bin, "-x", "--audio-format", target_fmt, "-o", out_template]
                           + quality_args + common_flags + [url])
            else:
                # --remux-video rewraps into the asked-for container when the
                # codec allows it, which is lossless; it only re-encodes when
                # there's no other way.
                yt_opts = ([yt_bin, "-o", out_template]
                           + quality_args
                           + ["--merge-output-format", target_fmt,
                              "--remux-video", f"{target_fmt}/mkv"]
                           + common_flags + [url])

            res = subprocess.run(yt_opts, capture_output=True, text=True, timeout=JOB_TIMEOUT)
            picked = pick_download(temp_dir)

            # Pass 2: Retry with credential & cookie profile rotation if rate-limited or private
            if not picked:
                rotated_cookie = get_rotated_cookie_flag(req.useBrowserCookies)
                fallback_opts = [yt_bin, "-o", out_template] + common_flags + rotated_cookie + [url]
                subprocess.run(fallback_opts, capture_output=True, text=True, timeout=JOB_TIMEOUT)
                picked = pick_download(temp_dir)

            if not picked:
                clean_err = re.sub(r'\s+', ' ', res.stderr).strip()[:180] if res.stderr else ""
                err_msg = f"Unable to extract media from link. {clean_err if clean_err else 'Verify link accessibility.'}"
                raise HTTPException(status_code=500, detail=err_msg)
            return picked

    try:
        with track_job("link", url, target_fmt) as job:
            output_file = await run_blocking(download)
            job.bytes_out = os.path.getsize(output_file)

        filename = os.path.basename(output_file)
        ext = os.path.splitext(filename)[1] or f".{target_fmt}"
        
        time_str = datetime.now().strftime("%H-%M")
        default_fallback = f"Transcripe_{time_str}{ext}"
        
        clean_name = re.sub(r'[^\w\s-]', '', filename).strip()
        safe_ascii = f"{clean_name}{ext}" if clean_name else default_fallback
        
        encoded_filename = quote(filename)

        if req.deliver == "link":
            return stash_result(output_file, temp_dir, filename)

        headers = {
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Content-Disposition": f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded_filename}'
        }

        return FileResponse(
            path=output_file,
            filename=safe_ascii,
            headers=headers,
            media_type="application/octet-stream",
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Download process timed out")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/convert/file")
async def convert_file(
    request: Request,
    file: UploadFile = File(...),
    targetFormat: str = Form("txt"),
    deliver: str = Form("stream"),
):
    require_token(request)
    target_fmt = re.sub(r"[^\w]", "", targetFormat.lower()) or "txt"
    temp_dir = tempfile.mkdtemp(prefix="transcripe_file_")
    safe_in, input_path = await save_upload(file, temp_dir)

    base_name = os.path.splitext(safe_in)[0] or "output"
    output_filename = f"{base_name}_converted.{target_fmt}"
    output_path = os.path.join(temp_dir, output_filename)

    # Codec-smart ffmpeg args so output actually plays everywhere, instead of a
    # valid-but-unplayable file (yuv444 H.264, AV1, no faststart, video muxed
    # into an audio target, etc.).
    AUDIO_FMTS = {"mp3", "wav", "flac", "aac", "ogg", "m4a", "opus", "wma"}
    VIDEO_FMTS = {"mp4", "mkv", "mov", "avi", "webm", "flv", "wmv"}

    def build_cmd(fmt):
        base = ["ffmpeg", "-y", "-i", input_path]
        audio_only = fmt in AUDIO_FMTS

        # Nothing to re-encode when the streams already suit the container:
        # copy them and keep both the time and the original quality.
        if can_remux(input_path, fmt, audio_only):
            if audio_only:
                base += ["-vn"]
            base += ["-c", "copy"]
            if fmt == "mp4":
                base += ["-movflags", "+faststart"]
            return base + [output_path]

        if audio_only:
            base += ["-vn"]  # drop any video stream from an audio target
            base += {
                "mp3": ["-codec:a", "libmp3lame", "-q:a", "2"],
                "flac": ["-codec:a", "flac"],
                "ogg": ["-codec:a", "libvorbis", "-q:a", "6"],
                "opus": ["-codec:a", "libopus", "-b:a", "128k"],
                "aac": ["-codec:a", "aac", "-b:a", "192k"],
                "wav": ["-codec:a", "pcm_s16le"],
            }.get(fmt, [])
        elif fmt == "mp4" or fmt in ("mov", "mkv", "avi"):
            base += ["-codec:v", "libx264", "-preset", X264_PRESET, "-crf", "23",
                     "-pix_fmt", "yuv420p", "-codec:a", "aac", "-b:a", "192k"]
            if fmt == "mp4":
                base += ["-movflags", "+faststart"]
        elif fmt == "webm":
            # row-mt + cpu-used are the difference between VP9 running at 2x
            # real time and something you'd actually wait for.
            base += ["-codec:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
                     "-row-mt", "1", "-cpu-used", "4", "-deadline", "good",
                     "-pix_fmt", "yuv420p", "-codec:a", "libopus"]
        return base + [output_path]

    IMAGE_FMTS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif", "ico"}
    src_ext = os.path.splitext(safe_in)[1].lower().lstrip(".")

    is_image_work = (
        target_fmt in IMAGE_FMTS or src_ext in IMAGE_FMTS | {"heic", "heif", "avif"})
    if is_image_work and target_fmt not in IMAGE_FMTS:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=422,
                            detail=f"Can't turn an image into .{target_fmt}.")

    def encode():
        # Images go through Pillow, not ffmpeg: ffmpeg can't read HEIC/AVIF at
        # all, and those are exactly what phones hand over.
        if is_image_work:
            import io
            from pathlib import Path

            from rich.console import Console

            from transcripe.engines.images import convert_image

            convert_image(Path(input_path), target_fmt, Console(file=io.StringIO()),
                          output_path=Path(output_path))
            return None
        return subprocess.run(build_cmd(target_fmt), capture_output=True,
                              text=True, timeout=JOB_TIMEOUT)

    try:
        with track_job("convert", safe_in, target_fmt,
                       os.path.getsize(input_path)) as job:
            res = await run_blocking(encode)
            if os.path.exists(output_path):
                job.bytes_out = os.path.getsize(output_path)
            if not job.bytes_out:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not convert {safe_in} to .{target_fmt}. "
                           f"{ffmpeg_reason(res.stderr if res else '') or 'That combination of formats is not supported.'}")

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            if deliver == "link":
                return stash_result(output_path, temp_dir, output_filename)
            return FileResponse(
                path=output_path,
                filename=output_filename,
                media_type="application/octet-stream",
                background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
            )
        # Honest failure — do NOT fabricate a stub file that pretends success.
        shutil.rmtree(temp_dir, ignore_errors=True)
        err = ffmpeg_reason(res.stderr if res else "")
        raise HTTPException(
            status_code=422,
            detail=f"Could not convert {safe_in} to .{target_fmt}. "
                   f"{err or 'That combination of formats is not supported.'}")
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Conversion timed out")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

# Serve the built studio from this same process — one command, one app.
# Mounted last so /api/* routes always win.
if os.path.isdir(WEB_DIST):
    app.mount("/transcripe", StaticFiles(directory=WEB_DIST, html=True), name="studio")

    @app.get("/", include_in_schema=False)
    def studio_redirect():
        return RedirectResponse("/transcripe/")

def _lan_ip() -> str:
    """Best-effort local address, by asking the routing table (no packets sent)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def serve(host: str | None = None, port: int | None = None, open_browser: bool | None = None) -> None:
    """Run the studio. Defaults come from the environment so `python -m` and
    `transcripe studio` behave identically."""
    import webbrowser

    import uvicorn

    global AUTH_TOKEN

    port = port or int(os.environ.get("TRANSCRIPE_PORT", "8000"))
    # 0.0.0.0 exposes the studio to your LAN (phones, Expo Go dev builds).
    host = host or os.environ.get("TRANSCRIPE_HOST", "127.0.0.1")
    if open_browser is None:
        open_browser = os.environ.get("TRANSCRIPE_NO_OPEN") != "1"

    # Reachable from the network and nobody set a token? Mint one rather than
    # leave the door open — and print exactly what the phone app needs.
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    if exposed and not AUTH_TOKEN:
        AUTH_TOKEN = secrets.token_urlsafe(18)
        lan_ip = _lan_ip()
        print(
            "\n  Studio is open to your network, so it's token-protected.\n"
            f"  Put these two lines in mobile/.env:\n\n"
            f"    EXPO_PUBLIC_API_URL={f'http://{lan_ip}:{port}' if lan_ip else f'http://<this-machine>:{port}'}\n"
            f"    EXPO_PUBLIC_API_TOKEN={AUTH_TOKEN}\n\n"
            "  Set TRANSCRIPE_TOKEN yourself to keep one across restarts.\n"
        )

    if WEB_DIST:
        if open_browser:
            # Hand the browser the token once; the app stores it and cleans the URL.
            suffix = f"?token={AUTH_TOKEN}" if AUTH_TOKEN else ""
            threading.Timer(
                0.9,
                lambda: webbrowser.open(f"http://127.0.0.1:{port}/transcripe/{suffix}"),
            ).start()
    else:
        print(
            "UI not built — serving the API only.\n"
            "Build it once with:  cd web && npm install && npm run build"
        )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()
