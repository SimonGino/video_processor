# Module Restructure & Documentation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split oversized files into focused modules, rewrite README with install guide, and clean up minor issues — without changing any business logic.

**Architecture:** Extract functions from `video_processor.py` (1108 lines) into 3 modules by responsibility (danmaku, encoder, uploader), extract scheduler tasks from `app.py` (777 lines) into `scheduler.py`, and update all imports. No function signatures or behavior change.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, uv

---

### Task 1: Create `danmaku.py` — extract danmaku-related functions

**Files:**
- Create: `danmaku.py`
- Modify: `video_processor.py` (remove extracted functions)

**Step 1: Create `danmaku.py` with functions from `video_processor.py`**

Move these functions (lines 86–240 of `video_processor.py`) into `danmaku.py`:
- `cleanup_small_files()` (lines 86–127)
- `get_video_resolution()` (lines 129–163)
- `convert_danmaku()` (lines 166–240)

```python
import os
import glob
import subprocess
import json
import logging

import config
from dmconvert import convert_xml_to_ass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def cleanup_small_files():
    # ... exact copy from video_processor.py lines 86-127


def get_video_resolution(video_file):
    # ... exact copy from video_processor.py lines 129-163


def convert_danmaku():
    # ... exact copy from video_processor.py lines 166-240
```

**Step 2: Remove the 3 extracted functions from `video_processor.py`**

Remove lines 86–240 from `video_processor.py`. Also remove the now-unused imports: `from dmconvert import convert_xml_to_ass`. Keep the remaining imports that `encode_video()` and upload functions still need.

**Step 3: Verify no syntax errors**

Run: `python -c "import danmaku"`
Expected: No errors

**Step 4: Commit**

```bash
git add danmaku.py video_processor.py
git commit -m "refactor: extract danmaku functions into danmaku.py"
```

---

### Task 2: Create `encoder.py` — extract video encoding function

**Files:**
- Create: `encoder.py`
- Modify: `video_processor.py` (remove extracted function)

**Step 1: Create `encoder.py` with the encode function from `video_processor.py`**

Move `encode_video()` (lines 243–483 of original, now starting around line 243 after danmaku removal) into `encoder.py`:

```python
import os
import glob
import subprocess
import shlex
import shutil
import logging

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def encode_video():
    # ... exact copy of encode_video() from video_processor.py
```

**Step 2: Fix the redundant `hasattr` check inside `encode_video()`**

In the moved `encode_video()`, change line:
```python
if hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING:
```
to:
```python
if config.SKIP_VIDEO_ENCODING:
```

**Step 3: Remove `encode_video()` from `video_processor.py`**

Remove the function and now-unused imports (`shlex`, `shutil`, `subprocess` if no longer needed).

**Step 4: Verify no syntax errors**

Run: `python -c "import encoder"`
Expected: No errors

**Step 5: Commit**

```bash
git add encoder.py video_processor.py
git commit -m "refactor: extract encode_video into encoder.py"
```

---

### Task 3: Rename remaining `video_processor.py` to `uploader.py`

**Files:**
- Rename: `video_processor.py` → `uploader.py`
- Modify: `app.py` (update imports)

**Step 1: Rename the file**

After Tasks 1-2, `video_processor.py` should only contain:
- `get_timestamp_from_filename()`
- `load_yaml_config()`
- `upload_to_bilibili()`
- `update_video_bvids()`

Rename it:
```bash
git mv video_processor.py uploader.py
```

**Step 2: Fix redundant `hasattr` checks in `uploader.py`**

In `upload_to_bilibili()`, change:
```python
is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
```
to:
```python
is_skip_encoding = config.SKIP_VIDEO_ENCODING
```

Also change:
```python
title_suffix = config.NO_DANMAKU_TITLE_SUFFIX if hasattr(config, 'NO_DANMAKU_TITLE_SUFFIX') else "【无弹幕版】"
```
to:
```python
title_suffix = config.NO_DANMAKU_TITLE_SUFFIX
```

And:
```python
title_suffix = config.DANMAKU_TITLE_SUFFIX if hasattr(config, 'DANMAKU_TITLE_SUFFIX') else "【弹幕版】"
```
to:
```python
title_suffix = config.DANMAKU_TITLE_SUFFIX
```

**Step 3: Update imports in `app.py`**

Change `app.py` lines 22-30 from:
```python
from video_processor import (
    load_yaml_config,
    cleanup_small_files,
    convert_danmaku,
    encode_video,
    update_video_bvids,
    upload_to_bilibili,
    get_timestamp_from_filename
)
```
to:
```python
from danmaku import cleanup_small_files, convert_danmaku
from encoder import encode_video
from uploader import (
    load_yaml_config,
    upload_to_bilibili,
    update_video_bvids,
    get_timestamp_from_filename,
)
```

**Step 4: Fix remaining `hasattr` checks in `app.py`**

Change 3 occurrences in `app.py`:

Line 146:
```python
is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
```
→
```python
is_skip_encoding = config.SKIP_VIDEO_ENCODING
```

Line 657:
```python
is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
```
→
```python
is_skip_encoding = config.SKIP_VIDEO_ENCODING
```

Line 687:
```python
is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
```
→
```python
is_skip_encoding = config.SKIP_VIDEO_ENCODING
```

Line 728:
```python
is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
```
→
```python
is_skip_encoding = config.SKIP_VIDEO_ENCODING
```

**Step 5: Verify imports work**

Run: `python -c "from uploader import load_yaml_config, upload_to_bilibili, update_video_bvids, get_timestamp_from_filename"`
Expected: No errors

Run: `python -c "from danmaku import cleanup_small_files, convert_danmaku; from encoder import encode_video"`
Expected: No errors

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: rename video_processor.py to uploader.py, update imports, remove hasattr checks"
```

---

### Task 4: Create `scheduler.py` — extract scheduler task functions from `app.py`

**Files:**
- Create: `scheduler.py`
- Modify: `app.py` (remove extracted functions, update imports)

**Step 1: Create `scheduler.py`**

Move these functions from `app.py` into `scheduler.py`:
- `scheduled_video_pipeline()` (lines 128-191)
- `scheduled_log_stream_end()` (lines 193-272)
- `clean_stale_sessions()` (lines 274-315)
- `run_processing_sync()` (lines 649-672)
- `run_upload_async()` (lines 698-710)

```python
import time
import asyncio
import logging
from datetime import timedelta

import config
from danmaku import cleanup_small_files, convert_danmaku
from encoder import encode_video
from uploader import load_yaml_config, upload_to_bilibili, update_video_bvids
from models import StreamSession, local_now

from sqlalchemy import desc, select

scheduler_logger = logging.getLogger("scheduler")
logger = logging.getLogger("app")


def get_dependencies():
    """Late import to avoid circular dependencies.

    Returns (AsyncSessionLocal, scheduler, stream_monitors) from app module.
    """
    from app import AsyncSessionLocal, scheduler, stream_monitors
    return AsyncSessionLocal, scheduler, stream_monitors


async def scheduled_video_pipeline():
    """Complete video processing and upload pipeline (scheduled task)."""
    AsyncSessionLocal, scheduler, stream_monitors = get_dependencies()
    # ... rest of function body from app.py lines 129-191
    # Replace all `hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING`
    # with `config.SKIP_VIDEO_ENCODING`


async def scheduled_log_stream_end(streamer_name: str):
    """Check streamer status and record start/end times."""
    AsyncSessionLocal, scheduler, stream_monitors = get_dependencies()
    # ... function body from app.py lines 193-272


async def clean_stale_sessions():
    """Clean up stale stream sessions that never got an end_time."""
    AsyncSessionLocal, _, _ = get_dependencies()
    # ... function body from app.py lines 274-315


def run_processing_sync():
    """Synchronous video processing for background thread."""
    # ... function body from app.py lines 649-672
    # Replace hasattr check with direct config access


async def run_upload_async(db):
    """Async upload task for background execution."""
    # ... function body from app.py lines 698-710
```

Key design decision: Use `get_dependencies()` with late import to break the circular dependency between `scheduler.py` and `app.py`. `app.py` imports functions from `scheduler.py`, and `scheduler.py` needs `AsyncSessionLocal`, `scheduler`, and `stream_monitors` from `app.py`. The late import resolves this cleanly.

**Step 2: Update `app.py` to import from `scheduler.py`**

Add import at top of `app.py`:
```python
from scheduler import (
    scheduled_video_pipeline,
    scheduled_log_stream_end,
    clean_stale_sessions,
    run_processing_sync,
    run_upload_async,
)
```

Remove the 5 function definitions from `app.py`. After this, `app.py` should be approximately 350-400 lines containing:
- Database setup (lines 34-58)
- Pydantic models (lines 66-100)
- FastAPI app creation and middleware (lines 101-126)
- Scheduler setup and `stream_monitors` dict (line 125-126)
- Startup/shutdown events (lines 317-386)
- All API endpoints (lines 388-736)
- Server startup function (lines 738-777)

**Step 3: Verify the app starts**

Run: `python -c "from app import app; print('OK')"`
Expected: `OK` (no import errors)

**Step 4: Commit**

```bash
git add scheduler.py app.py
git commit -m "refactor: extract scheduler task functions into scheduler.py"
```

---

### Task 5: Remove unused `schedule` dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Remove the dependency**

Remove this line from `pyproject.toml`:
```
    "schedule>=1.2.2",
```

**Step 2: Run uv sync**

Run: `uv sync`
Expected: Successful sync without `schedule` package

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: remove unused schedule dependency"
```

---

### Task 6: Rewrite README.md

**Files:**
- Modify: `README.md`

**Step 1: Rewrite README with comprehensive install guide**

Replace the entire `README.md` with the new structure. Key sections:

1. **Project overview** — 3 bullet points (monitor, process, upload)
2. **Architecture diagram** — ASCII module relationship diagram showing data flow
3. **Prerequisites** — system dependencies:
   - Python 3.13+ and uv
   - FFmpeg with Intel QSV support (+ ffprobe)
   - External recording tool (e.g., StreamRecorder)
   - Sufficient disk space
4. **Installation steps**:
   ```bash
   git clone ...
   cd video_processor
   uv sync  # installs bilitool, dmconvert, fastapi, etc.
   cp config.py.example config.py  # if applicable, or edit directly
   # Edit config.py with your paths and streamer info
   # Edit config.yaml with Bilibili upload parameters
   bilitool login  # or place cookies.json
   ```
5. **Configuration tables** — all config.py and config.yaml options with defaults
6. **Usage** — service management commands, API endpoints
7. **Project structure** — updated file list matching new modules
8. **Python dependencies table** — dmconvert, bilitool, etc. with descriptions

Preserve the existing mermaid sequence diagram (it's good). Update the project structure section to reflect the new module layout.

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README with comprehensive install guide and updated structure"
```

---

### Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update project structure section**

Update the "Project Structure" section to reflect the new module layout:

```markdown
## Project Structure

Flat single-directory layout (no sub-packages):

- `app.py` — Entry point: FastAPI app, API endpoints, DB setup
- `scheduler.py` — APScheduler task functions (video pipeline, stream status, stale session cleanup)
- `danmaku.py` — Danmaku processing: file cleanup, XML→ASS conversion
- `encoder.py` — Video encoding: FFmpeg QSV encoding, file management
- `uploader.py` — Bilibili upload: video upload, BVID management, YAML config loading
- `stream_monitor.py` — StreamStatusMonitor class: per-streamer Douyu API polling
- `config.py` — All configuration constants (paths, intervals, feature flags, streamer list)
- `config.yaml` — Bilibili upload metadata (title template, tags, category, description)
- `models.py` — SQLAlchemy models: `StreamSession`, `UploadedVideo`
- `service.sh` — Bash service management (nohup uvicorn, PID file based)
```

**Step 2: Update Architecture section**

Update bullet points about sync-in-async pattern and module references.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect new module structure"
```

---

### Task 8: Final verification

**Step 1: Verify all imports resolve**

Run:
```bash
python -c "
from danmaku import cleanup_small_files, convert_danmaku
from encoder import encode_video
from uploader import load_yaml_config, upload_to_bilibili, update_video_bvids
from scheduler import scheduled_video_pipeline, scheduled_log_stream_end, clean_stale_sessions
from app import app
print('All imports OK')
"
```
Expected: `All imports OK`

**Step 2: Verify old `video_processor.py` is gone**

Run: `ls video_processor.py 2>&1`
Expected: `No such file or directory`

**Step 3: Verify no remaining `hasattr(config` patterns in source files**

Run: `grep -r "hasattr(config" *.py`
Expected: No output (all removed)

**Step 4: Verify file count and sizes**

Run: `wc -l *.py`
Expected: `app.py` ~350-400 lines, each new module ~100-500 lines, no single file > 600 lines

**Step 5: Commit any remaining changes**

If any files need final cleanup:
```bash
git add -A
git commit -m "refactor: final cleanup after module restructure"
```
