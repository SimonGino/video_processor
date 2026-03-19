import logging
import logging.handlers
import os

from douyu2bilibili import config


def test_logging_config_constants_exist():
    assert hasattr(config, "LOG_LEVEL")
    assert config.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR")
    assert hasattr(config, "LOG_DIR")
    assert config.LOG_DIR.endswith("logs")
    assert hasattr(config, "LOG_RETENTION_DAYS")
    assert config.LOG_RETENTION_DAYS == 3


from douyu2bilibili.logging_config import setup_logging


def test_setup_logging_creates_log_dir(tmp_path, monkeypatch):
    """setup_logging creates the log directory and configures loggers."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging()

    assert os.path.isdir(log_dir)


def test_setup_logging_configures_parent_loggers(tmp_path, monkeypatch):
    """The 4 parent loggers have handlers and propagate=False."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging()

    for name in ("upload", "pipeline", "monitor", "recording"):
        lgr = logging.getLogger(name)
        assert lgr.handlers, f"{name} logger has no handlers"
        assert lgr.propagate is False, f"{name} logger should not propagate"


def test_setup_logging_child_inherits_handler(tmp_path, monkeypatch):
    """A child logger like upload.uploader writes to upload.log."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging()

    child = logging.getLogger("upload.uploader")
    child.info("test message from upload.uploader")

    upload_log = os.path.join(log_dir, "upload.log")
    assert os.path.exists(upload_log)
    content = open(upload_log).read()
    assert "test message from upload.uploader" in content


def test_setup_logging_recording_uses_file_handler(tmp_path, monkeypatch):
    """Recording service mode uses FileHandler, not TimedRotatingFileHandler."""
    log_dir = str(tmp_path / "logs")
    monkeypatch.setattr("douyu2bilibili.config.LOG_DIR", log_dir)

    setup_logging(is_recording_service=True)

    rec_logger = logging.getLogger("recording")
    file_handlers = [
        h for h in rec_logger.handlers
        if isinstance(h, logging.FileHandler)
        and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
        and type(h) is not logging.StreamHandler
    ]
    assert len(file_handlers) >= 1, "recording logger should have a plain FileHandler"


def test_uploader_uses_named_logger():
    """uploader module should use a logger under the upload namespace."""
    from douyu2bilibili import uploader
    assert hasattr(uploader, "logger")
    assert uploader.logger.name == "upload.uploader"
