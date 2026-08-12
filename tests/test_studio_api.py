"""Studio API: access control, upload limits, conversion, result handoff.

Everything here talks to the FastAPI app in-process. Conversions that need a
real encoder are skipped when the encoder isn't installed, matching the rest
of the suite.
"""
import importlib
import shutil

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="studio extras not installed")
TestClient = fastapi_testclient.TestClient

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def load_studio(monkeypatch, **env):
    """Import the studio with a fresh environment (module-level config)."""
    for key in ("TRANSCRIPE_TOKEN", "TRANSCRIPE_MAX_UPLOAD_MB"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import transcripe.studio as studio
    return importlib.reload(studio)


@pytest.fixture
def open_studio(monkeypatch):
    """No token configured — the loopback default."""
    return load_studio(monkeypatch)


@pytest.fixture
def locked_studio(monkeypatch):
    return load_studio(monkeypatch, TRANSCRIPE_TOKEN="s3cret")


def wav_bytes(fixtures):
    return fixtures["wav"].read_bytes() if "wav" in fixtures else b"RIFFfake"


# ── access control ──────────────────────────────────────────────────────────

def test_open_studio_needs_no_token(open_studio):
    with TestClient(open_studio.app) as client:
        health = client.get("/api/health").json()
    assert health["auth_required"] is False
    assert health["authorized"] is True


def test_locked_studio_reports_itself_but_stays_reachable(locked_studio):
    """Health stays open so a client can tell 'locked' from 'not running'."""
    with TestClient(locked_studio.app) as client:
        health = client.get("/api/health").json()
    assert health["auth_required"] is True
    assert health["authorized"] is False


def test_locked_studio_rejects_work_without_token(locked_studio):
    with TestClient(locked_studio.app) as client:
        res = client.post("/api/convert/url", json={"url": "https://example.com/x"})
    assert res.status_code == 401


def test_locked_studio_rejects_a_wrong_token(locked_studio):
    with TestClient(locked_studio.app) as client:
        res = client.post(
            "/api/convert/url",
            json={"url": "https://example.com/x"},
            headers={"X-Transcripe-Token": "not-it"},
        )
    assert res.status_code == 401


def test_token_accepted_from_header_or_query(locked_studio):
    with TestClient(locked_studio.app) as client:
        assert client.get(
            "/api/health", headers={"X-Transcripe-Token": "s3cret"}
        ).json()["authorized"] is True
        assert client.get("/api/health?token=s3cret").json()["authorized"] is True


# ── upload limits ───────────────────────────────────────────────────────────

def test_upload_over_the_cap_is_refused(monkeypatch):
    studio = load_studio(monkeypatch, TRANSCRIPE_MAX_UPLOAD_MB="0.001")  # 1 KB
    with TestClient(studio.app) as client:
        res = client.post(
            "/api/convert/file",
            files={"file": ("big.wav", b"x" * 50_000, "audio/wav")},
            data={"targetFormat": "mp3"},
        )
    assert res.status_code == 413
    assert "larger than" in res.json()["detail"]


# ── conversion ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_audio_conversion_returns_a_real_file(open_studio, fixtures):
    with TestClient(open_studio.app) as client:
        res = client.post(
            "/api/convert/file",
            files={"file": ("clip.wav", wav_bytes(fixtures), "audio/wav")},
            data={"targetFormat": "mp3"},
        )
    assert res.status_code == 200
    assert res.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3")


def test_image_conversion_does_not_go_through_ffmpeg(open_studio, tmp_path):
    """Images route through Pillow — ffmpeg can't read HEIC/AVIF at all."""
    PIL = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    src = tmp_path / "shot.png"
    PIL.new("RGB", (32, 24), (200, 80, 42)).save(src)

    with TestClient(open_studio.app) as client:
        res = client.post(
            "/api/convert/file",
            files={"file": ("shot.png", src.read_bytes(), "image/png")},
            data={"targetFormat": "webp"},
        )
    assert res.status_code == 200
    assert res.content[:4] == b"RIFF"


def test_heic_input_is_readable(open_studio, tmp_path):
    """Regression: pillow-heif ≥ 1.0 dropped register_avif_opener, and importing
    it alongside the HEIF one used to silently disable HEIC entirely."""
    pytest.importorskip("pillow_heif", reason="pillow-heif not installed")
    PIL = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    import pillow_heif

    pillow_heif.register_heif_opener()
    src = tmp_path / "photo.heic"
    PIL.new("RGB", (40, 30), (10, 120, 90)).save(src, format="HEIF")

    with TestClient(open_studio.app) as client:
        res = client.post(
            "/api/convert/file",
            files={"file": ("photo.heic", src.read_bytes(), "image/heic")},
            data={"targetFormat": "jpg"},
        )
    assert res.status_code == 200, res.text
    assert res.content[:3] == b"\xff\xd8\xff"


def test_impossible_pairing_fails_honestly(open_studio, tmp_path):
    PIL = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    src = tmp_path / "shot.png"
    PIL.new("RGB", (8, 8)).save(src)

    with TestClient(open_studio.app) as client:
        res = client.post(
            "/api/convert/file",
            files={"file": ("shot.png", src.read_bytes(), "image/png")},
            data={"targetFormat": "mp3"},
        )
    assert res.status_code == 422
    assert "image" in res.json()["detail"].lower()


# ── result handoff ──────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_link_delivery_hands_back_a_single_use_url(open_studio, fixtures):
    with TestClient(open_studio.app) as client:
        started = client.post(
            "/api/convert/file",
            files={"file": ("clip.wav", wav_bytes(fixtures), "audio/wav")},
            data={"targetFormat": "mp3", "deliver": "link"},
        )
        assert started.status_code == 200
        job = started.json()
        assert job["download"].startswith("/api/result/")

        first = client.get(job["download"])
        assert first.status_code == 200
        assert len(first.content) > 0

        # The token is spent: a replay must not resurrect the file.
        assert client.get(job["download"]).status_code == 404


def test_unknown_result_token_is_not_found(open_studio):
    with TestClient(open_studio.app) as client:
        assert client.get("/api/result/made-up-token").status_code == 404


def test_unknown_job_is_not_found(open_studio):
    with TestClient(open_studio.app) as client:
        assert client.get("/api/jobs/made-up-job").status_code == 404


# ── download selection ──────────────────────────────────────────────────────

def test_download_pick_ignores_sidecars(open_studio, tmp_path):
    """yt-dlp drops thumbnails and .part files next to the media; taking the
    first glob hit could hand the user a JPEG instead of their video."""
    (tmp_path / "video.mp4").write_bytes(b"m" * 5000)
    (tmp_path / "video.jpg").write_bytes(b"t" * 9000)      # bigger, but a thumbnail
    (tmp_path / "video.info.json").write_text("{}")
    (tmp_path / "video.mp4.part").write_bytes(b"p" * 8000)

    assert open_studio.pick_download(str(tmp_path)).endswith("video.mp4")


def test_download_pick_prefers_the_largest_real_file(open_studio, tmp_path):
    (tmp_path / "audio.m4a").write_bytes(b"a" * 100)
    (tmp_path / "video.mp4").write_bytes(b"v" * 9000)
    assert open_studio.pick_download(str(tmp_path)).endswith("video.mp4")


def test_download_pick_returns_empty_when_nothing_landed(open_studio, tmp_path):
    (tmp_path / "only.part").write_bytes(b"x" * 10)
    assert open_studio.pick_download(str(tmp_path)) == ""


# ── cookie opt-out ──────────────────────────────────────────────────────────

def test_browser_cookies_are_not_read_when_declined(open_studio, monkeypatch):
    """Regression: the UI's privacy toggle was accepted and then ignored."""
    monkeypatch.setattr(open_studio.glob, "glob", lambda *a, **k: [])
    assert open_studio.get_rotated_cookie_flag(False) == []
    assert open_studio.get_rotated_cookie_flag(True)[0] == "--cookies-from-browser"
