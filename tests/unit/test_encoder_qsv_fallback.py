import os
import subprocess
import sys
from pathlib import Path


def test_encode_video_fallback_when_qsv_init_fails(monkeypatch, tmp_path: Path):
    import config
    from encoder import encode_video

    processing = tmp_path / "processing"
    upload = tmp_path / "upload"
    processing.mkdir()
    upload.mkdir()

    monkeypatch.setattr(config, "PROCESSING_FOLDER", str(processing))
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(upload))
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config, "FFMPEG_PATH", "ffmpeg")
    monkeypatch.setattr(sys, "platform", "linux")

    flv = processing / "a.flv"
    ass = processing / "a.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\nTitle: test\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text, encoding, env=None, errors=None):  # noqa: ANN001
        calls.append(list(cmd))
        cmd_str = " ".join(cmd)
        if "-init_hw_device" in cmd_str and "qsv=hw" in cmd_str:
            raise subprocess.CalledProcessError(
                returncode=244,
                cmd=cmd,
                output="",
                stderr="Device creation failed: -12.\nFailed to set value 'qsv=hw' for option 'init_hw_device': Cannot allocate memory\n",
            )

        if "libx264" in cmd_str:
            raise AssertionError("CPU fallback libx264 should not be attempted")

        # Simulate successful fallback encoding by creating output file.
        out = Path(cmd[-1])
        out.write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    encode_video()

    assert any("qsv=hw" in " ".join(c) for c in calls)
    assert any("subtitles=filename=" in " ".join(c) for c in calls)
    assert not any("libx264" in " ".join(c) for c in calls)
    assert not any("videotoolbox" in " ".join(c) for c in calls)
    assert not (upload / "a.mp4").exists()


def test_encode_video_passes_qsv_runtime_env_and_device(monkeypatch, tmp_path: Path):
    import config
    from encoder import encode_video

    processing = tmp_path / "processing"
    upload = tmp_path / "upload"
    processing.mkdir()
    upload.mkdir()

    monkeypatch.setattr(config, "PROCESSING_FOLDER", str(processing))
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(upload))
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config, "FFMPEG_PATH", "ffmpeg")
    monkeypatch.setattr(config, "FFMPEG_QSV_INIT_DEVICE", "/dev/dri/renderD128", raising=False)
    monkeypatch.setattr(config, "FFMPEG_QSV_LD_LIBRARY_PATH", "/usr/trim/lib/mediasrv", raising=False)
    monkeypatch.setattr(config, "FFMPEG_QSV_LIBVA_DRIVERS_PATH", "/usr/trim/lib/mediasrv/dri", raising=False)
    monkeypatch.setattr(config, "FFMPEG_QSV_LIBVA_DRIVER_NAME", "iHD", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/path")

    flv = processing / "b.flv"
    ass = processing / "b.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\nTitle: test\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(cmd, check, capture_output, text, encoding, env, errors=None):  # noqa: ANN001
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    encode_video()

    cmd = " ".join(captured["cmd"])
    env = captured["env"]
    assert "-init_hw_device qsv=hw:/dev/dri/renderD128" in cmd
    assert env["LIBVA_DRIVERS_PATH"] == "/usr/trim/lib/mediasrv/dri"
    assert env["LIBVA_DRIVER_NAME"] == "iHD"
    assert env["LD_LIBRARY_PATH"] == "/usr/trim/lib/mediasrv:/existing/path"
    assert (upload / "b.mp4").exists()


def test_encode_video_tolerates_non_utf8_ffmpeg_output(monkeypatch, tmp_path: Path):
    import config
    from encoder import encode_video

    processing = tmp_path / "processing"
    upload = tmp_path / "upload"
    processing.mkdir()
    upload.mkdir()

    monkeypatch.setattr(config, "PROCESSING_FOLDER", str(processing))
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(upload))
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config, "FFMPEG_PATH", "ffmpeg")
    monkeypatch.setattr(sys, "platform", "linux")

    flv = processing / "c.flv"
    ass = processing / "c.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\nTitle: test\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(cmd, check, capture_output, text, encoding, env=None, errors=None):  # noqa: ANN001
        captured["errors"] = errors
        if errors != "replace":
            raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\ufffd", stderr="warn\ufffd")

    monkeypatch.setattr(subprocess, "run", fake_run)

    encode_video()

    assert captured["errors"] == "replace"
    assert (upload / "c.mp4").exists()
