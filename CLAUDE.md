# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

斗鱼录播到 B 站一站式自动化套件 — One-stop Douyu-to-Bilibili recording, danmaku processing, encoding, and upload workflow. Monitors streamer status on Douyu, records/processes FLV + danmaku, and uploads to Bilibili with smart session-based grouping.

## Tech Stack

- Python 3.13.3, managed by `uv`
- FastAPI + Uvicorn (ASGI), APScheduler (async job scheduling)
- SQLAlchemy (async) + SQLite (aiosqlite)
- aiohttp (Douyu API client), bilitool (Bilibili API), dmconvert (danmaku conversion)
- FFmpeg with Intel QSV (video encoding), VideoToolbox fallback on macOS

## Commands

```bash
uv sync                    # Install dependencies
python app.py              # Run dev server (port 50009)
python app.py --reload     # Run with auto-reload
./service.sh start         # Start all services (main + recording) with process supervisor
./service.sh stop          # Stop all services
./service.sh restart       # Restart all services
./service.sh status        # Check all services status
./service.sh logs 100      # View last 100 lines of log (both services)
```

Tests (pytest + pytest-asyncio in auto mode):
```bash
uv run pytest                           # Run all tests
uv run pytest tests/unit/               # Unit tests only
uv run pytest tests/integration/        # Integration tests only
uv run pytest tests/unit/test_sanity.py # Single test file
```

No linting is configured.

## Project Structure

Standard Python `src/` layout with `douyu2bilibili` package:

```
src/douyu2bilibili/          # Main package (all business logic)
├── __init__.py
├── app.py                   # FastAPI app, API endpoints, DB setup, startup/shutdown
├── scheduler.py             # APScheduler task functions (video pipeline, stream status check, stale session cleanup)
├── danmaku.py               # Danmaku processing: file cleanup, XML→ASS conversion
├── danmaku_postprocess.py   # ASS post-processing: display area clipping, opacity, color tags
├── encoder.py               # Video encoding: FFmpeg QSV encoding, skip-encoding mode
├── uploader.py              # Bilibili upload: dual backend (biliup CLI / bilitool), BVID management
├── stream_monitor.py        # StreamStatusMonitor: per-streamer Douyu API polling
├── config.py                # All configuration constants (paths, intervals, feature flags)
├── models.py                # SQLAlchemy models: StreamSession, UploadedVideo
├── recording_service.py     # Recording service entry point (delegates to recording/)
└── recording/               # Live stream recording sub-package
    ├── recording_service.py # Main recording loop orchestration
    ├── douyu_stream_resolver.py
    ├── ffmpeg_recorder.py
    ├── danmaku_collector.py
    ├── segment_pipeline.py
    ├── xml_writer.py
    ├── stt_codec.py
    └── douyu_message_parser.py
```

Root directory files:
- `app.py` — Thin entry point, delegates to `douyu2bilibili.app`
- `recording_service.py` — Thin entry point, delegates to `douyu2bilibili.recording_service`
- `config.yaml` — Bilibili upload metadata (title template with `{time}` placeholder, tags, category, description)
- `service.sh` — Unified service management with process supervisor (auto-restart on crash, log rotation, log cleanup)

Package-internal imports use relative imports (`from . import config`, `from .models import ...`).
Tests import via `from douyu2bilibili import ...` or `from douyu2bilibili.xxx import ...`.

## Architecture

- **Sync-in-async pattern**: Synchronous video processing functions in `danmaku.py` and `encoder.py` (FFmpeg, file ops) run via `loop.run_in_executor()` in `scheduler.py` to avoid blocking the async event loop
- **3 scheduled jobs** (in `scheduler.py`): video pipeline (default 60min), stream status check (default 10min per streamer), stale session cleanup (12h)
- **Circular dependency**: `scheduler.py` uses late import (`_get_app_deps()`) to access `AsyncSessionLocal`, `scheduler`, and `stream_monitors` from `app.py` via relative import (`from .app import ...`)
- **Session-based upload grouping**: Videos are matched to stream sessions by time range. First video creates a new Bilibili submission; subsequent videos append as multi-part (分P)
- **Dual upload backend**: `uploader.py` supports `biliup_cli` (biliupR binary) and `bilitool` (Python library), configured via `BILIBILI_UPLOADER_BACKEND`. The biliup CLI backend auto-discovers binaries under `third-party/` with platform-aware sorting
- **BVID retrieval**: Retry logic (3 attempts with delays) after upload to fetch the generated BVID. Regex parsing of biliup CLI stdout (`_BILIUP_BVID_RE`)
- **Rate limit handling**: biliup CLI backend detects Bilibili rate limit (code 21540) and implements cooldown with configurable retry
- **Stream status detection**: `StreamStatusMonitor` polls Douyu API per-streamer with 10s timeout. State cached in class instance, initialized from API on startup (not DB)
- **Post-stream trigger**: When `PROCESS_AFTER_STREAM_END` is enabled and a streamer goes offline, a one-shot pipeline job is scheduled 3 minutes later via APScheduler
- **File pipeline**: `data/processing/` → (cleanup → danmaku convert → encode) → `data/upload/` → (upload to Bilibili). Files with `.flv.part` suffix are skipped as still-recording
- **Database**: SQLite at `app_data.db`, auto-created on startup via `create_all()`. All timestamps use UTC+8 via `local_now()` in `models.py`

## Configuration

Two config sources:
1. `config.py` — Python constants for paths, intervals, feature flags, streamer info
2. `config.yaml` — Loaded at runtime by `load_yaml_config()` into global `yaml_config` dict

Key feature flags in `config.py`:
- `SKIP_VIDEO_ENCODING` — Skip FFmpeg encoding, move raw FLV directly to upload folder
- `PROCESS_AFTER_STREAM_END` — Only process after streamer goes offline
- `DELETE_UPLOADED_FILES` — Delete local files after successful upload (with configurable delay via `DELETE_UPLOADED_FILES_DELAY_HOURS`)
- `SCHEDULED_UPLOAD_ENABLED` — Toggle scheduled uploads (manual `/run_upload_tasks` always works)
- `BILIBILI_UPLOADER_BACKEND` — `"biliup_cli"`, `"bilitool"`, or `"auto"`
- `API_ENABLED` — Enable/disable API-dependent features

## External Requirements

- FFmpeg and FFprobe must be installed and accessible
- Bilibili login: `cookies.json` in project root or `bilitool login` (bilitool backend), or cookies at `BILIUP_COOKIES_PATH` (biliup CLI backend)
- Douyu room ID configured in `config.py` STREAMERS list

## Key API Endpoints

Server runs on port 50009. Notable endpoints:
- `POST /run_processing_tasks` — Trigger video processing pipeline
- `POST /run_upload_tasks` — Trigger Bilibili upload
- `GET /stream_sessions/{streamer_name}` — Query stream sessions

## Testing Notes

- `tests/bin/ffmpeg` — Stub ffmpeg binary used in encoder tests (writes placeholder output)
- `tests/unit/` — Pure unit tests with mocks/stubs
- `tests/integration/` — Tests with HTTP/WebSocket stubs for recording subsystem
- pytest-asyncio configured in auto mode (`pyproject.toml`)
