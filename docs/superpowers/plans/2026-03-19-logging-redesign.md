# Logging Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragmented per-module logging with 4 function-based log files (upload, pipeline, monitor, recording), unified configuration, daily rotation with 3-day retention, and configurable log level.

**Architecture:** New `logging_config.py` uses `dictConfig()` to configure all handlers/loggers in one shot. Main service uses `TimedRotatingFileHandler` for all 4 files; recording service uses plain `FileHandler` for `recording.log` only. Each module gets a named logger under one of the 4 parent loggers. `service.sh` log rotation/cleanup functions are removed.

**Tech Stack:** Python stdlib `logging`, `logging.config`, `logging.handlers`

**Spec:** `docs/superpowers/specs/2026-03-19-logging-redesign-design.md`

---

### Task 1: Add logging config constants to config.py

**Files:**
- Modify: `src/douyu2bilibili/config.py:89-95`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_logging_config.py
from douyu2bilibili import config


def test_logging_config_constants_exist():
    assert hasattr(config, "LOG_LEVEL")
    assert config.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR")
    assert hasattr(config, "LOG_DIR")
    assert config.LOG_DIR.endswith("logs")
    assert hasattr(config, "LOG_RETENTION_DAYS")
    assert config.LOG_RETENTION_DAYS == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_logging_config.py::test_logging_config_constants_exist -v`
Expected: FAIL with `AttributeError: module 'douyu2bilibili.config' has no attribute 'LOG_LEVEL'`

- [ ] **Step 3: Add constants to config.py**

Add after the `DELETE_UPLOADED_FILES_DELAY_HOURS` block (after line 95) and before the `SCHEDULED_UPLOAD_ENABLED` line:

```python
# --- 日志配置 ---
# 日志级别 (DEBUG / INFO / WARNING / ERROR)，可通过环境变量 LOG_LEVEL 覆盖
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# 日志目录 (绝对路径)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
# 日志文件保留天数
LOG_RETENTION_DAYS = 3
```

Also remove the stale comment on line 94 about `service.sh` log retention:
```python
# 该值同时用于 service.sh 日志文件的保留时间（超过此时间的归档日志将被自动清理）。
```
Delete that line entirely.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_logging_config.py::test_logging_config_constants_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/douyu2bilibili/config.py tests/unit/test_logging_config.py
git commit -m "feat(logging): add LOG_LEVEL, LOG_DIR, LOG_RETENTION_DAYS config constants"
```

---

### Task 2: Create logging_config.py with setup_logging()

**Files:**
- Create: `src/douyu2bilibili/logging_config.py`
- Test: `tests/unit/test_logging_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_logging_config.py`:

```python
import logging
import os

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_logging_config.py -v -k "not test_logging_config_constants"`
Expected: FAIL with `ModuleNotFoundError: No module named 'douyu2bilibili.logging_config'`

- [ ] **Step 3: Create logging_config.py**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_logging_config.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/douyu2bilibili/logging_config.py tests/unit/test_logging_config.py
git commit -m "feat(logging): create logging_config.py with setup_logging()"
```

---

### Task 3: Wire setup_logging() into app.py and recording_service.py entry points

**Files:**
- Modify: `src/douyu2bilibili/app.py:107-108,131`
- Modify: `src/douyu2bilibili/recording_service.py:9-12`

- [ ] **Step 1: Modify app.py**

At the top of the file, add the import (near the other relative imports):
```python
from .logging_config import setup_logging
```

Remove the `logging.basicConfig(...)` call at line 107:
```python
# DELETE: logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
```

At the very top of `startup_event()` (the `@app.on_event("startup")` handler), before any other code, add:
```python
    setup_logging()
```

Keep `logger = logging.getLogger("app")` for now — it will be renamed in a later task.

- [ ] **Step 2: Modify recording_service.py (entry point)**

Replace the `logging.basicConfig(...)` block (lines 9-12) with:
```python
from .logging_config import setup_logging
setup_logging(is_recording_service=True)
```

Also replace the direct `logging.error(...)` call (line 14) with a logger:
```python
import logging
_logger = logging.getLogger("recording.entry")
```
And change `logging.error(...)` to `_logger.error(...)`.

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `uv run pytest -v`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/douyu2bilibili/app.py src/douyu2bilibili/recording_service.py
git commit -m "feat(logging): wire setup_logging() into app and recording service entry points"
```

---

### Task 4: Migrate uploader.py to named logger

**Files:**
- Modify: `src/douyu2bilibili/uploader.py`

This is the largest refactor (~74 call sites).

- [ ] **Step 1: Write a smoke test for logger name**

Append to `tests/unit/test_logging_config.py`:

```python
def test_uploader_uses_named_logger():
    """uploader module should use a logger under the upload namespace."""
    from douyu2bilibili import uploader  # noqa: F811
    assert hasattr(uploader, "logger")
    assert uploader.logger.name == "upload.uploader"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_logging_config.py::test_uploader_uses_named_logger -v`
Expected: FAIL

- [ ] **Step 3: Refactor uploader.py**

1. Remove `logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')` (line 30)
2. Add at module top (after imports): `logger = logging.getLogger("upload.uploader")`
3. Replace all `logging.info(` → `logger.info(`, `logging.warning(` → `logger.warning(`, `logging.error(` → `logger.error(`, `logging.debug(` → `logger.debug(`, `logging.exception(` → `logger.exception(` throughout the file

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_logging_config.py::test_uploader_uses_named_logger -v`
Expected: PASS

- [ ] **Step 5: Run all tests to check for regressions**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/douyu2bilibili/uploader.py tests/unit/test_logging_config.py
git commit -m "refactor(logging): migrate uploader.py to upload.uploader logger"
```

---

### Task 5: Migrate encoder.py to named logger

**Files:**
- Modify: `src/douyu2bilibili/encoder.py`

~95 call sites to refactor.

- [ ] **Step 1: Refactor encoder.py**

1. Remove `logging.basicConfig(...)` (line 12)
2. Add: `logger = logging.getLogger("pipeline.encoder")`
3. Replace all `logging.info/warning/error/debug/exception(` → `logger.info/warning/error/debug/exception(`

- [ ] **Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/douyu2bilibili/encoder.py
git commit -m "refactor(logging): migrate encoder.py to pipeline.encoder logger"
```

---

### Task 6: Migrate danmaku.py and danmaku_postprocess.py to named loggers

**Files:**
- Modify: `src/douyu2bilibili/danmaku.py`
- Modify: `src/douyu2bilibili/danmaku_postprocess.py`

- [ ] **Step 1: Refactor danmaku.py**

1. Remove `logging.basicConfig(...)` (line 13)
2. Add: `logger = logging.getLogger("pipeline.danmaku")`
3. Replace all `logging.info/warning/error(` → `logger.info/warning/error(`

- [ ] **Step 2: Refactor danmaku_postprocess.py**

1. Remove `logging.basicConfig(...)` (line 4)
2. Add: `logger = logging.getLogger("pipeline.danmaku_post")`
3. Replace `logging.info(` → `logger.info(` (1 call site at line 74)

- [ ] **Step 3: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/douyu2bilibili/danmaku.py src/douyu2bilibili/danmaku_postprocess.py
git commit -m "refactor(logging): migrate danmaku modules to pipeline.* loggers"
```

---

### Task 7: Migrate scheduler.py to split loggers

**Files:**
- Modify: `src/douyu2bilibili/scheduler.py`

This file spans 3 functional domains. Replace the two existing loggers with three domain-specific ones.

- [ ] **Step 1: Refactor scheduler.py**

Replace lines 15-16:
```python
scheduler_logger = logging.getLogger("scheduler")
logger = logging.getLogger("app")
```
with:
```python
upload_logger = logging.getLogger("upload.scheduler")
pipeline_logger = logging.getLogger("pipeline.scheduler")
monitor_logger = logging.getLogger("monitor.session")
```

Then update every call site per this function-to-logger mapping:

| Function | Current logger var | New logger var | Rationale |
|----------|-------------------|----------------|-----------|
| `scheduled_upload()` | `scheduler_logger` | `upload_logger` | Upload/BVID tasks |
| `run_upload_async()` | `logger` | `upload_logger` | Manual upload trigger |
| `scheduled_video_processing()` | `scheduler_logger` | `pipeline_logger` | Video processing pipeline |
| `run_processing_sync()` | `logger` | `pipeline_logger` | Sync video processing |
| `scheduled_log_stream_end()` | `scheduler_logger` | `monitor_logger` | Stream status changes, session creation |
| `clean_stale_sessions()` | `logger` | `monitor_logger` | Session cleanup |

- [ ] **Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/douyu2bilibili/scheduler.py
git commit -m "refactor(logging): split scheduler.py into upload/pipeline/monitor loggers"
```

---

### Task 8: Migrate stream_monitor.py and app.py to new logger names

**Files:**
- Modify: `src/douyu2bilibili/stream_monitor.py`
- Modify: `src/douyu2bilibili/app.py`

- [ ] **Step 1: Refactor stream_monitor.py**

Change line 7 from:
```python
logger = logging.getLogger("stream_monitor")
```
to:
```python
logger = logging.getLogger("monitor.stream")
```

- [ ] **Step 2: Refactor app.py**

Change line 108 from:
```python
logger = logging.getLogger("app")
```
to:
```python
logger = logging.getLogger("monitor.app")
```

- [ ] **Step 3: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/douyu2bilibili/stream_monitor.py src/douyu2bilibili/app.py
git commit -m "refactor(logging): rename stream_monitor and app loggers to monitor.* namespace"
```

---

### Task 9: Migrate recording/ subpackage to new logger names

**Files:**
- Modify: `src/douyu2bilibili/recording/recording_service.py`
- Modify: `src/douyu2bilibili/recording/segment_pipeline.py`
- Modify: `src/douyu2bilibili/recording/danmaku_collector.py`

- [ ] **Step 1: Refactor recording/recording_service.py**

Change line 15 from:
```python
logger = logging.getLogger("recording_service")
```
to:
```python
logger = logging.getLogger("recording.service")
```

- [ ] **Step 2: Refactor recording/segment_pipeline.py**

Change line 10 from:
```python
logger = logging.getLogger("segment_pipeline")
```
to:
```python
logger = logging.getLogger("recording.segment")
```

- [ ] **Step 3: Refactor recording/danmaku_collector.py**

Change line 14 from:
```python
logger = logging.getLogger("danmaku_collector")
```
to:
```python
logger = logging.getLogger("recording.danmaku_collector")
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/douyu2bilibili/recording/recording_service.py src/douyu2bilibili/recording/segment_pipeline.py src/douyu2bilibili/recording/danmaku_collector.py
git commit -m "refactor(logging): rename recording/ loggers to recording.* namespace"
```

---

### Task 10: Simplify service.sh — remove log rotation/cleanup functions

**Files:**
- Modify: `service.sh`

- [ ] **Step 1: Remove get_log_retention_hours() function**

Delete lines 50-59 (the `get_log_retention_hours` function).

- [ ] **Step 2: Remove clean_old_logs() function**

Delete lines 61-67 (the `clean_old_logs` function).

- [ ] **Step 3: Remove rotate_log_if_needed() function**

Delete lines 69-89 (the `rotate_log_if_needed` function).

- [ ] **Step 4: Remove calls to deleted functions in _run_supervisor()**

In `_run_supervisor()`, remove lines 144-145:
```bash
        rotate_log_if_needed "$log_file"
        clean_old_logs
```

- [ ] **Step 5: Remove call to clean_old_logs in start_service()**

In `start_service()`, remove line 205:
```bash
    clean_old_logs
```

- [ ] **Step 6: Update logs_service() to also tail Python log files**

Replace the `logs_service()` function with:

```bash
logs_service() {
    local lines=${2:-50}

    echo -e "${BLUE}=== 主服务日志 (最后 $lines 行) ===${NC}"
    if [ -f "$MAIN_LOG_FILE" ]; then
        tail -n "$lines" "$MAIN_LOG_FILE"
    else
        print_warning "日志不存在: $MAIN_LOG_FILE"
    fi

    echo ""

    echo -e "${BLUE}=== 录制服务日志 (最后 $lines 行) ===${NC}"
    if [ -f "$REC_LOG_FILE" ]; then
        tail -n "$lines" "$REC_LOG_FILE"
    else
        print_warning "日志不存在: $REC_LOG_FILE"
    fi

    # Python application logs
    local log_dir="$SCRIPT_DIR/logs"
    for log_name in upload pipeline monitor recording; do
        local log_file="$log_dir/${log_name}.log"
        echo ""
        echo -e "${BLUE}=== ${log_name} 日志 (最后 $lines 行) ===${NC}"
        if [ -f "$log_file" ]; then
            tail -n "$lines" "$log_file"
        else
            print_warning "日志不存在: $log_file"
        fi
    done
}
```

- [ ] **Step 7: Verify service.sh has no syntax errors**

Run: `bash -n service.sh`
Expected: No output (no syntax errors)

- [ ] **Step 8: Commit**

```bash
git add service.sh
git commit -m "refactor(logging): remove log rotation/cleanup from service.sh, update logs command"
```

---

### Task 11: End-to-end integration smoke test

**Files:**
- Create: `tests/unit/test_logging_integration.py`

- [ ] **Step 1: Write integration test**

```python
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
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_logging_integration.py
git commit -m "test(logging): add end-to-end logging integration smoke tests"
```

---

### Task 12: Final verification — run full test suite

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Verify log files are in .gitignore**

Confirm `logs/` and `*.log` are present in `.gitignore` (already there — just verify).

- [ ] **Step 3: Review all changes**

Run: `git diff main --stat`
Verify only expected files were changed and no unintended modifications exist.
