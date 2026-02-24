import subprocess
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

    flv = processing / "a.flv"
    ass = processing / "a.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\nTitle: test\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text, encoding):  # noqa: ANN001
        calls.append(list(cmd))
        cmd_str = " ".join(cmd)
        if "-init_hw_device" in cmd_str and "qsv=hw" in cmd_str:
            raise subprocess.CalledProcessError(
                returncode=244,
                cmd=cmd,
                output="",
                stderr="Device creation failed: -12.\nFailed to set value 'qsv=hw' for option 'init_hw_device': Cannot allocate memory\n",
            )

        # Simulate successful fallback encoding by creating output file.
        out = Path(cmd[-1])
        out.write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    encode_video()

    assert any("qsv=hw" in " ".join(c) for c in calls)
    assert any("subtitles=filename=" in " ".join(c) for c in calls)
    assert any("videotoolbox" in " ".join(c) or "libx264" in " ".join(c) for c in calls)
    assert (upload / "a.mp4").exists()
