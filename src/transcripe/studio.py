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
    }


def _token_ok(request: Request) -> bool:
    try:
        require_token(request)
        return True
    except HTTPException:
        return False


@app.post("/api/convert/url")
async def convert_url(req: UrlConvertRequest, request: Request):
    require_token(request)
    url = req.url.strip()
    target_fmt = req.format.lower().replace(".", "").strip() or "mp3"
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    temp_dir = tempfile.mkdtemp(prefix="transcripe_dl_")

    try:
        music_domains = ["spotify.com", "music.apple.com", "deezer.com", "tidal.com", "soundcloud.com", "bandcamp.com"]
        if any(domain in url.lower() for domain in music_domains):
            # spotdl high-fidelity resolution
            cmd = [
                tool_path("spotdl"),
                url,
                "--output", temp_dir,
                "--format", target_fmt if target_fmt in ["mp3", "m4a", "flac", "ogg", "opus"] else "mp3"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            files = glob.glob(os.path.join(temp_dir, "*.*"))
            
            # Fallback to rotated yt-dlp audio extractor
            if not files:
                out_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
                yt_cmd = [
                    tool_path("yt-dlp"),
                    "-x", "--audio-format", target_fmt if target_fmt in ["mp3", "m4a", "wav", "aac", "opus", "flac"] else "mp3",
                    "-o", out_template,
                    "--user-agent", get_rotated_user_agent(),
                    "--geo-bypass",
                    url
                ]
                subprocess.run(yt_cmd, capture_output=True, text=True, timeout=120)
                files = glob.glob(os.path.join(temp_dir, "*.*"))

            if not files:
                raise HTTPException(status_code=500, detail=f"Music extraction failed for {url}")
            output_file = files[0]
        else:
            # Scalable yt-dlp extraction with credential & browser profile rotation
            out_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
            
            common_flags = [
                "--user-agent", get_rotated_user_agent(),
                "--referer", url,
                "--no-check-certificates",
                "--geo-bypass",
                "--ignore-errors",
                "--no-playlist",
                "--concurrent-fragments", "4",
            ]

            yt_bin = tool_path("yt-dlp")
            
            if target_fmt in ["mp3", "m4a", "wav", "aac", "opus", "flac"]:
                yt_opts = [yt_bin, "-x", "--audio-format", target_fmt, "-o", out_template] + common_flags + [url]
            else:
                # Prefer H.264 (avc1) over AV1/VP9 — AV1 won't play in most players
                # (default OS players, older browsers, many phones show black video).
                # Fall back through mp4, then re-mux anything to mp4 for compatibility.
                h264_fmt = ("bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
                            "best[vcodec^=avc1]/"
                            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                            "best[ext=mp4]/best")
                yt_opts = [yt_bin, "-f", h264_fmt, "--merge-output-format", "mp4",
                           "-o", out_template] + common_flags + [url]

            res = subprocess.run(yt_opts, capture_output=True, text=True, timeout=120)
            files = glob.glob(os.path.join(temp_dir, "*.*"))
            
            # Pass 2: Retry with credential & cookie profile rotation if rate-limited or private
            if not files:
                rotated_cookie = get_rotated_cookie_flag(req.useBrowserCookies)
                fallback_opts = [yt_bin, "-o", out_template] + common_flags + rotated_cookie + [url]
                subprocess.run(fallback_opts, capture_output=True, text=True, timeout=120)
                files = glob.glob(os.path.join(temp_dir, "*.*"))

            if not files:
                clean_err = re.sub(r'\s+', ' ', res.stderr).strip()[:180] if res.stderr else ""
                err_msg = f"Unable to extract media from link. {clean_err if clean_err else 'Verify link accessibility.'}"
                raise HTTPException(status_code=500, detail=err_msg)
            output_file = files[0]

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

    # Never trust the client filename — strip any path components (path-traversal).
    safe_in = os.path.basename(file.filename or "upload")
    input_path = os.path.join(temp_dir, safe_in)
    # Copy in chunks with a running total: an unbounded upload would otherwise
    # be a way to fill the disk of whoever is running the studio.
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
        if fmt in AUDIO_FMTS:
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
            base += ["-codec:v", "libx264", "-preset", "medium", "-crf", "23",
                     "-pix_fmt", "yuv420p", "-codec:a", "aac", "-b:a", "192k"]
            if fmt == "mp4":
                base += ["-movflags", "+faststart"]
        elif fmt == "webm":
            base += ["-codec:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
                     "-pix_fmt", "yuv420p", "-codec:a", "libopus"]
        return base + [output_path]

    try:
        cmd = build_cmd(target_fmt)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

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
        err = re.sub(r"\s+", " ", res.stderr or "").strip()[:200]
        raise HTTPException(
            status_code=422,
            detail=f"Could not convert to .{target_fmt}. {err or 'Unsupported format for this input.'}")
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
