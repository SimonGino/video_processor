"""Tests for failure quarantine and orphan FLV passthrough logic."""
import os
import subprocess
import sys
from pathlib import Path


def _setup_dirs(monkeypatch, tmp_path):
    """Create processing/upload/failed dirs and patch config."""
    from douyu2bilibili import config

    processing = tmp_path / "processing"
    upload = tmp_path / "upload"
    failed = tmp_path / "failed"
    processing.mkdir()
    upload.mkdir()
    failed.mkdir()

    monkeypatch.setattr(config, "PROCESSING_FOLDER", str(processing))
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(upload))
    monkeypatch.setattr(config, "FAILED_FOLDER", str(failed))
    monkeypatch.setattr(config, "FFMPEG_PATH", "ffmpeg")
    monkeypatch.setattr(config, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(sys, "platform", "linux")

    return processing, upload, failed


def _reset_encoder_state():
    """Clear module-level state between tests."""
    from douyu2bilibili import encoder
    encoder._failure_counts.clear()
    encoder._orphan_seen.clear()


def _reset_danmaku_state():
    """Clear module-level state between tests."""
    from douyu2bilibili import danmaku
    danmaku._failure_counts.clear()


# --- encoder.py failure quarantine tests ---


def test_encoder_quarantines_after_max_retries(monkeypatch, tmp_path: Path):
    """Files should be moved to failed/ after MAX_RETRY_COUNT failures."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 2)
    _reset_encoder_state()

    flv = processing / "bad.flv"
    ass = processing / "bad.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\n", encoding="utf-8")

    def fake_run_fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="encoding error")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)

    # First run: failure count = 1, files stay in processing
    encode_video()
    assert flv.exists()
    assert ass.exists()

    # Second run: failure count = 2 (threshold), files moved to failed
    encode_video()
    assert not flv.exists()
    assert not ass.exists()
    assert (failed / "bad.flv").exists()
    assert (failed / "bad.ass").exists()


def test_encoder_clears_failure_on_success(monkeypatch, tmp_path: Path):
    """Successful encoding should reset the failure counter."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video, _failure_counts

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 3)
    _reset_encoder_state()

    flv = processing / "recover.flv"
    ass = processing / "recover.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\n", encoding="utf-8")

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="temporary error")
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # First run: fails, count = 1
    encode_video()
    assert str(flv) in _failure_counts

    # Second run: succeeds, count cleared
    encode_video()
    assert str(flv) not in _failure_counts
    assert (upload / "recover.mp4").exists()


def test_encoder_skip_mode_quarantines(monkeypatch, tmp_path: Path):
    """SKIP_VIDEO_ENCODING mode should also quarantine on repeated move failures."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", True)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 2)
    _reset_encoder_state()

    flv = processing / "stuck.flv"
    flv.write_bytes(b"fake-flv")

    # Make upload dir read-only to force move failures
    upload.chmod(0o444)

    try:
        # Run twice to reach threshold
        encode_video()
        encode_video()
    finally:
        upload.chmod(0o755)

    assert not flv.exists()
    assert (failed / "stuck.flv").exists()


# --- danmaku.py failure quarantine tests ---


def test_danmaku_quarantines_after_max_retries(monkeypatch, tmp_path: Path):
    """XML conversion failures should quarantine after threshold."""
    from douyu2bilibili import config
    from douyu2bilibili.danmaku import convert_danmaku

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 2)
    _reset_danmaku_state()

    xml = processing / "bad.xml"
    flv = processing / "bad.flv"
    xml.write_text("<d></d>", encoding="utf-8")
    flv.write_bytes(b"fake-flv")

    # Mock get_video_resolution to return valid resolution
    import douyu2bilibili.danmaku as danmaku_mod
    monkeypatch.setattr(danmaku_mod, "get_video_resolution", lambda f: (1920, 1080))

    # Mock convert_xml_to_ass to always raise
    monkeypatch.setattr(danmaku_mod, "convert_xml_to_ass", lambda **kw: (_ for _ in ()).throw(RuntimeError("bad xml")))

    # First run: count = 1
    convert_danmaku()
    assert xml.exists()

    # Second run: count = 2 (threshold), quarantined
    convert_danmaku()
    assert not xml.exists()
    assert (failed / "bad.xml").exists()
    assert (failed / "bad.flv").exists()


# --- orphan FLV passthrough tests ---


def test_orphan_flv_processed_on_second_sighting(monkeypatch, tmp_path: Path):
    """Orphan FLVs (no XML/ASS) should be processed after two pipeline cycles."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 3)
    _reset_encoder_state()

    # Create orphan FLV (no XML, no ASS, no .part)
    orphan = processing / "orphan.flv"
    orphan.write_bytes(b"fake-flv")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # First run: orphan is seen but not processed
    encode_video()
    assert orphan.exists()
    assert not (upload / "orphan.mp4").exists()

    # Second run: orphan confirmed, encoded without subtitles
    encode_video()
    assert (upload / "orphan.mp4").exists()


def test_orphan_flv_skipped_when_xml_exists(monkeypatch, tmp_path: Path):
    """FLVs with existing XML should not be treated as orphans."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video, _orphan_seen

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 3)
    _reset_encoder_state()

    flv = processing / "has_xml.flv"
    xml = processing / "has_xml.xml"
    flv.write_bytes(b"fake-flv")
    xml.write_text("<d></d>", encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))

    encode_video()

    # Should NOT be in orphan tracking
    assert str(flv) not in _orphan_seen


def test_orphan_flv_skipped_when_part_exists(monkeypatch, tmp_path: Path):
    """FLVs still being recorded (.part exists) should not be treated as orphans."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video, _orphan_seen

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 3)
    _reset_encoder_state()

    flv = processing / "recording.flv"
    part = processing / "recording.flv.part"
    flv.write_bytes(b"fake-flv")
    part.write_bytes(b"")

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))

    encode_video()
    encode_video()

    # Should NOT be in orphan tracking or processed
    assert str(flv) not in _orphan_seen
    assert not (upload / "recording.mp4").exists()


def test_encoder_retains_count_when_quarantine_fails(monkeypatch, tmp_path: Path):
    """If moving to failed/ fails, failure count should be retained so file is skipped."""
    from douyu2bilibili import config
    from douyu2bilibili.encoder import encode_video, _failure_counts

    processing, upload, failed = _setup_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config, "MAX_RETRY_COUNT", 2)
    _reset_encoder_state()

    flv = processing / "stuck.flv"
    ass = processing / "stuck.ass"
    flv.write_bytes(b"fake-flv")
    ass.write_text("[Script Info]\n", encoding="utf-8")

    encode_call_count = 0

    def fake_run_fail(cmd, **kwargs):
        nonlocal encode_call_count
        encode_call_count += 1
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="encoding error")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)

    # Make failed/ dir read-only so quarantine move fails
    failed.chmod(0o444)
    try:
        # Two runs to reach threshold
        encode_video()
        encode_video()
    finally:
        failed.chmod(0o755)

    # File stays in processing (quarantine failed)
    assert flv.exists()
    # Counter should be retained (not cleared)
    assert _failure_counts.get(str(flv), 0) >= config.MAX_RETRY_COUNT

    # Third run: file should be skipped (count >= threshold)
    encode_call_count = 0
    encode_video()
    # No new FFmpeg calls should have been made for this file
    assert encode_call_count == 0
