"""Smoke test: setup_logging + modules produce logs in correct files."""

import logging
import os

from douyu2bilibili.logging_config import setup_logging


def test_all_log_files_written(tmp_path, monkeypatch):
    """Each functional domain writes to its own log file."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging()

    # Simulate logging from child loggers across all 4 domains
    logging.getLogger("upload.uploader").info("upload test")
    logging.getLogger("pipeline.encoder").info("pipeline test")
    logging.getLogger("monitor.stream").info("monitor test")
    logging.getLogger("recording.service").info("recording test")

    for name, expected in [
        ("upload.log", "upload test"),
        ("pipeline.log", "pipeline test"),
        ("monitor.log", "monitor test"),
        ("recording.log", "recording test"),
    ]:
        path = os.path.join(log_dir, name)
        assert os.path.exists(path), f"{name} not created"
        content = open(path).read()
        assert expected in content, f"'{expected}' not found in {name}"


def test_no_cross_contamination(tmp_path, monkeypatch):
    """Upload logs should NOT appear in pipeline log file."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging()

    logging.getLogger("upload.uploader").info("upload only message")

    pipeline_log = os.path.join(log_dir, "pipeline.log")
    if os.path.exists(pipeline_log):
        content = open(pipeline_log).read()
        assert "upload only message" not in content


def test_log_level_respected(tmp_path, monkeypatch):
    """DEBUG messages should not appear when level is WARNING."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)
    monkeypatch.setattr("douyu2bilibili.config.LOG_LEVEL", "WARNING")

    setup_logging()

    logging.getLogger("upload.uploader").debug("debug msg")
    logging.getLogger("upload.uploader").warning("warning msg")

    upload_log = os.path.join(log_dir, "upload.log")
    content = open(upload_log).read()
    assert "debug msg" not in content
    assert "warning msg" in content
