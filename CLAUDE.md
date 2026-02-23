# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BiliBili 全自动录播上传套件 — Automated Douyu live stream recording processor and Bilibili uploader. Monitors streamer status on Douyu, processes recorded FLV videos (danmaku embedding via FFmpeg), and uploads to Bilibili with smart session-based grouping.

## Tech Stack

- Python 3.13.3, managed by `uv`
- FastAPI + Uvicorn (ASGI), APScheduler (async job scheduling)
- SQLAlchemy (async) + SQLite (aiosqlite)
- aiohttp (Douyu API client), bilitool (Bilibili API), dmconvert (danmaku conversion)
- FFmpeg with Intel QSV (video encoding)

## Commands

```bash
uv sync                    # Install dependencies
python app.py              # Run dev server (port 50009)
python app.py --reload     # Run with auto-reload
./service.sh start         # Start background service
./service.sh stop          # Stop service
./service.sh restart       # Restart service
./service.sh status        # Check service status
./service.sh logs 100      # View last 100 lines of log
```

No tests or linting are configured.

## Project Structure

Flat single-directory layout (no sub-packages):

- `app.py` — Entry point: FastAPI app, API endpoints, APScheduler jobs, DB setup
- `stream_monitor.py` — StreamStatusMonitor class: per-streamer Douyu API polling and state tracking
- `video_processor.py` — Core processing: file cleanup, danmaku conversion, video encoding, Bilibili upload
- `config.py` — All configuration constants (paths, intervals, feature flags, streamer list)
- `config.yaml` — Bilibili upload metadata (title template with `{time}` placeholder, tags, category, description)
- `models.py` — SQLAlchemy models: `StreamSession`, `UploadedVideo`
- `service.sh` — Bash service management (nohup uvicorn, PID file based)

## Architecture

- **Sync-in-async pattern**: Synchronous video processing functions (FFmpeg, file ops) run via `loop.run_in_executor()` to avoid blocking the async event loop
- **3 scheduled jobs**: video pipeline (default 60min), stream status check (default 10min), stale session cleanup (12h)
- **Session-based upload grouping**: Videos are matched to stream sessions by time range. First video creates a new Bilibili submission; subsequent videos append as multi-part (分P)
- **BVID retrieval**: Retry logic (3 attempts with delays) after upload to fetch the generated BVID
- **Stream status detection**: `StreamStatusMonitor` class (`stream_monitor.py`) polls Douyu API per-streamer with 10s timeout. State cached in class instance, initialized from API on startup (not DB). Multi-streamer support via `config.STREAMERS` list
- **Database**: SQLite at `app_data.db`, auto-created on startup via `create_all()`

## Configuration

Two config sources:
1. `config.py` — Python constants for paths, intervals, feature flags, streamer info
2. `config.yaml` — Loaded at runtime by `load_yaml_config()` into global `yaml_config` dict

Key feature flags in `config.py`:
- `SKIP_VIDEO_ENCODING` — Skip FFmpeg encoding, upload raw FLV directly
- `PROCESS_AFTER_STREAM_END` — Only process after streamer goes offline
- `DELETE_UPLOADED_FILES` — Delete local files after successful upload
- `API_ENABLED` — Enable/disable API-dependent features

## External Requirements

- FFmpeg and FFprobe must be installed and accessible
- Bilibili login: requires `cookies.json` in project root or `bilitool login`
- Douyu room ID configured in `config.py`

## Key API Endpoints

Server runs on port 50009. Notable endpoints:
- `POST /run_processing_tasks` — Trigger video processing pipeline
- `POST /run_upload_tasks` — Trigger Bilibili upload
- `GET /stream_sessions/{streamer_name}` — Query stream sessions
