"""Unified logging configuration for douyu2bilibili.

Call setup_logging() once at process startup before any other module code runs.
Main service: setup_logging()
Recording service: setup_logging(is_recording_service=True)
"""

import logging
import logging.config
import logging.handlers
import os

from . import config

# The 4 functional log files and their parent logger names
_LOG_FILES = {
    "upload": "upload.log",
    "pipeline": "pipeline.log",
    "monitor": "monitor.log",
    "recording": "recording.log",
}


def setup_logging(*, is_recording_service: bool = False) -> None:
    """Configure all loggers via dictConfig.

    Args:
        is_recording_service: If True, only configure the ``recording`` logger
            with a plain FileHandler (no rotation) to avoid multi-process
            rotation races.  The main service handles rotation for all files.
    """
    log_dir = config.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    level = config.LOG_LEVEL

    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    handlers: dict = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
    }

    loggers: dict = {}

    for logger_name, filename in _LOG_FILES.items():
        filepath = os.path.join(log_dir, filename)
        handler_name = f"{logger_name}_file"

        if is_recording_service and logger_name != "recording":
            # Recording service only needs the recording logger
            continue

        if is_recording_service and logger_name == "recording":
            # Plain FileHandler — no rotation
            handlers[handler_name] = {
                "class": "logging.FileHandler",
                "formatter": "standard",
                "filename": filepath,
                "encoding": "utf-8",
            }
        else:
            # TimedRotatingFileHandler — main service owns rotation
            handlers[handler_name] = {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "formatter": "standard",
                "filename": filepath,
                "when": "midnight",
                "backupCount": config.LOG_RETENTION_DAYS,
                "encoding": "utf-8",
            }

        loggers[logger_name] = {
            "handlers": [handler_name, "console"],
            "level": level,
            "propagate": False,
        }

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": fmt,
            },
        },
        "handlers": handlers,
        "loggers": loggers,
    }

    logging.config.dictConfig(logging_config)
