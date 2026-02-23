# Module Restructure & Documentation Design

## Goal

Restructure the codebase for clarity and add comprehensive documentation, with minimal logic changes.

## Context

- **Current state**: 2 oversized files (`video_processor.py` 1108 lines, `app.py` 777 lines) containing mixed responsibilities
- **Deployment**: Linux server with Intel QSV, single streamer
- **Recording**: External tool produces FLV + XML files; this project handles post-processing only

## Module Split

### Before

```
video_processor.py (1108 lines) — cleanup, danmaku, encoding, upload, BVID management
app.py (777 lines)              — FastAPI routes, scheduler tasks, DB setup, startup logic
```

### After

```
video_processor/
├── app.py              — FastAPI entry, routes, startup (~300 lines)
├── scheduler.py        — APScheduler task functions
├── danmaku.py          — cleanup_small_files(), convert_danmaku()
├── encoder.py          — encode_video(), get_video_duration(), FFmpeg helpers
├── uploader.py         — upload_to_bilibili(), update_video_bvids(), helpers
├── stream_monitor.py   — (unchanged)
├── models.py           — (unchanged)
├── config.py           — (unchanged)
├── config.yaml         — (unchanged)
├── service.sh          — (unchanged)
└── README.md           — rewritten with install guide
```

### Split Rules

- Move functions as-is, no logic changes
- Keep function signatures and behavior identical
- Update imports in all consuming files

## README Rewrite

New structure:

1. **Overview** — what the project does (3 bullet points)
2. **Architecture** — module diagram + data flow
3. **Prerequisites** — system dependencies (FFmpeg, uv, Python 3.13+)
4. **Installation** — step-by-step (clone, uv sync, configure, login, start)
5. **Configuration**
   - `config.py` — table of all settings with defaults and descriptions
   - `config.yaml` — Bilibili upload metadata explanation
6. **Usage** — service management, API endpoints, manual triggers
7. **Project Structure** — file-by-file description

Explicit mention of `dmconvert` (danmaku conversion library, auto-installed via uv sync).

## Small Fixes (bundled)

1. Remove 6 redundant `hasattr(config, 'SKIP_VIDEO_ENCODING')` checks — the attribute is always defined
2. Remove unused `schedule>=1.2.2` from pyproject.toml
3. Update CLAUDE.md to reflect new module structure

## Out of Scope (future work)

- `time.sleep()` → `asyncio.sleep()` in async functions
- Deprecated API upgrades (FastAPI lifespan, SQLAlchemy DeclarativeBase, Pydantic v2)
- Full multi-streamer support in upload/pipeline
- Timezone unification
- DB session lifecycle fix in BackgroundTasks
- Unfinished unassigned-video upload logic
