# Logging Redesign: Per-Function Log Files with Rotation

## Problem

Current logging has two pain points:

1. **All modules mixed in one file per service** — hard to find relevant info when debugging (e.g., upload errors buried in pipeline noise)
2. **Log retention shares config with file deletion** (`DELETE_UPLOADED_FILES_DELAY_HOURS`) — semantically unclear, retention policy not independently tunable

Additional issues: multiple conflicting `logging.basicConfig()` calls across modules (only first takes effect), no configurable log level, inconsistent log formats.

## Design

### Log Files by Function

4 log files in `logs/` directory, grouped by business function:

| File | Modules | Content |
|------|---------|---------|
| `logs/upload.log` | uploader.py, scheduler.py (upload logic) | Upload tasks, BVID retrieval, rate limiting, multi-part append |
| `logs/pipeline.log` | scheduler.py (processing logic), danmaku.py, danmaku_postprocess.py, encoder.py | File discovery, danmaku conversion, encoding, file movement |
| `logs/monitor.log` | stream_monitor.py, scheduler.py (status check & session cleanup) | Online/offline detection, session management |
| `logs/recording.log` | recording/ subpackage (all modules) | Recording flow, segmentation, danmaku collection |

### Unified Configuration via `logging_config.py`

New file: `src/douyu2bilibili/logging_config.py`

- Uses `logging.config.dictConfig()` for one-shot configuration of all handlers and loggers
- Exposes `setup_logging()` function called at process startup

**Handlers:**

- **Main service**: one `TimedRotatingFileHandler` per function file — rotates at midnight (`when='midnight'`), retains 3 days (`backupCount=3`), archive suffix `.YYYY-MM-DD`
- **Recording service**: plain `FileHandler` in append mode for the `logs/recording.log` file — does NOT perform rotation. Only the main service rotates log files, avoiding multi-process rotation race conditions (see rationale below).
- One `StreamHandler` to stdout for development use (both processes)

**Multi-process safety rationale:** `TimedRotatingFileHandler.doRollover()` calls `os.rename()`, which is not safe when two processes attempt it simultaneously. The main service owns all 4 log files and handles rotation. The recording service only writes to `logs/recording.log` via a plain `FileHandler`. Since the main service's `TimedRotatingFileHandler` renames the file at midnight, the recording service's `FileHandler` will continue writing to the old inode. On next recording service restart (or via a `WatchedFileHandler` if needed in the future), it picks up the new file. At this project's throughput (low volume, infrequent restarts align with rotation), this is acceptable.

**Log format (unified):**

```
2026-03-19 14:30:00 - upload.uploader - INFO - message text
```

Pattern: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

**Log level:**

- Configurable via `config.py`: `LOG_LEVEL = "INFO"`
- Override via environment variable `LOG_LEVEL` (takes precedence)
- Supports: `DEBUG`, `INFO`, `WARNING`, `ERROR`

### Logger Hierarchy

Logger names use dot-separated hierarchy so child loggers inherit parent handlers:

```
upload              → logs/upload.log
  upload.uploader
  upload.scheduler

pipeline            → logs/pipeline.log
  pipeline.danmaku
  pipeline.danmaku_post
  pipeline.encoder
  pipeline.scheduler

monitor             → logs/monitor.log
  monitor.stream
  monitor.session
  monitor.app

recording           → logs/recording.log
  recording.service
  recording.segment
  recording.danmaku_collector
  ...
```

**Propagation:** The 4 parent loggers (`upload`, `pipeline`, `monitor`, `recording`) must set `propagate = False` to prevent messages from bubbling up to the root logger. Child loggers keep the default `propagate = True` so they inherit their parent's handlers. The `dictConfig` sets `disable_existing_loggers: False` to avoid silencing third-party library loggers.

**Uvicorn loggers:** Uvicorn's built-in loggers (`uvicorn`, `uvicorn.access`, `uvicorn.error`) are left at their defaults and go to stdout/stderr, captured by `service.sh` redirection.

### dictConfig Skeleton

```python
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
        "upload_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "formatter": "standard",
            "filename": "<LOG_DIR>/upload.log",
            "when": "midnight",
            "backupCount": 3,  # LOG_RETENTION_DAYS
            "encoding": "utf-8",
        },
        # pipeline_file, monitor_file, recording_file — same pattern
    },
    "loggers": {
        "upload": {
            "handlers": ["upload_file", "console"],
            "level": "<LOG_LEVEL>",
            "propagate": False,
        },
        "pipeline": {
            "handlers": ["pipeline_file", "console"],
            "level": "<LOG_LEVEL>",
            "propagate": False,
        },
        "monitor": {
            "handlers": ["monitor_file", "console"],
            "level": "<LOG_LEVEL>",
            "propagate": False,
        },
        "recording": {
            "handlers": ["recording_file", "console"],
            "level": "<LOG_LEVEL>",
            "propagate": False,
        },
    },
}
```

Placeholders `<LOG_DIR>` and `<LOG_LEVEL>` are resolved at runtime from config before calling `dictConfig()`. The recording service replaces `recording_file` handler class with plain `FileHandler`.

### Module Changes

Each module:

1. **Remove** its `logging.basicConfig(...)` call
2. **Change** logger name to match the hierarchy above
3. **Replace all `logging.info(...)` / `logging.warning(...)` / `logging.error(...)` calls with `logger.info(...)` / `logger.warning(...)` / `logger.error(...)`** — several modules (uploader.py, danmaku.py, danmaku_postprocess.py, encoder.py) call logging functions directly on the `logging` module rather than on a named logger. These must all be changed to use the module's named logger instance.

Specific mapping:

| File | Current Logger | New Logger | Call-site refactor needed |
|------|---------------|------------|--------------------------|
| `uploader.py` | `logging.basicConfig(...)` + direct `logging.xxx()` calls | `logging.getLogger("upload.uploader")` | Yes (~100+ call sites) |
| `scheduler.py` | `getLogger("scheduler")` + `getLogger("app")` | Three loggers: `upload.scheduler`, `pipeline.scheduler`, `monitor.session` (by function within file) | Partial (already uses named loggers, but needs split) |
| `danmaku.py` | `logging.basicConfig(...)` + direct `logging.xxx()` calls | `logging.getLogger("pipeline.danmaku")` | Yes |
| `danmaku_postprocess.py` | `logging.basicConfig(...)` + direct `logging.xxx()` calls | `logging.getLogger("pipeline.danmaku_post")` | Yes |
| `encoder.py` | `logging.basicConfig(...)` + direct `logging.xxx()` calls | `logging.getLogger("pipeline.encoder")` | Yes |
| `stream_monitor.py` | `getLogger("stream_monitor")` | `logging.getLogger("monitor.stream")` | No (already uses named logger) |
| `app.py` | `getLogger("app")` | `logging.getLogger("monitor.app")`, calls `setup_logging()` at startup | No (already uses named logger) |
| `recording/recording_service.py` | `getLogger("recording_service")` | `logging.getLogger("recording.service")` | No |
| `recording/segment_pipeline.py` | `getLogger("segment_pipeline")` | `logging.getLogger("recording.segment")` | No |
| `recording/danmaku_collector.py` | `getLogger("danmaku_collector")` | `logging.getLogger("recording.danmaku_collector")` | No |

**scheduler.py special handling:** This file spans multiple functions. Define three logger instances at file top; each function uses the appropriate one based on its business domain.

### config.py Additions

```python
# Logging
LOG_LEVEL = "INFO"                                      # DEBUG / INFO / WARNING / ERROR
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")            # Log directory (absolute path)
LOG_RETENTION_DAYS = 3                                  # Log file retention in days
```

`LOG_DIR` uses `PROJECT_ROOT` for an absolute path, consistent with other directory configs in `config.py`. `DELETE_UPLOADED_FILES_DELAY_HOURS` is no longer reused for log cleanup.

### service.sh Simplification

- **Remove** `rotate_log_if_needed()` function and its calls — rotation handled by Python
- **Remove** `clean_old_logs()` function and its calls — cleanup handled by Python
- **Remove** `get_log_retention_hours()` function — no longer needed
- **Keep** `MAIN_LOG_FILE` and `REC_LOG_FILE` in project root — these capture supervisor `[SUPERVISOR]` messages and any pre-Python startup errors via stdout/stderr redirection
- **Update** `logs_service()` function — in addition to tailing the supervisor log files, also tail the 4 Python log files in `logs/` so `service.sh logs` shows useful application logs

### Initialization

- **Main service** (`app.py`): call `setup_logging()` at the top of `startup_event()` (the existing `@app.on_event("startup")` handler), before DB init
- **Recording service** (`recording_service.py`): call `setup_logging()` at entry point

### Directory Management

- `logs/` created automatically on startup: `os.makedirs(LOG_DIR, exist_ok=True)`
- `logs/` added to `.gitignore`

## Out of Scope

- Structured/JSON logging (overkill for current scale)
- Per-module log level overrides (one global level is sufficient)
- Log aggregation / external log platform integration
- Migrating `app.py` from `@app.on_event("startup")` to `lifespan()` (separate concern)
