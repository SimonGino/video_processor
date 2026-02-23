# Stream Status Monitor Refactoring — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor stream status detection from fragile function-attribute caching into a proper `StreamStatusMonitor` class with multi-streamer support, API-based initialization, request timeouts, and elimination of duplicated code.

**Architecture:** New `stream_monitor.py` contains the `StreamStatusMonitor` class. One instance per monitored streamer, stored in `app.py`'s global dict. The class encapsulates Douyu API calls and state caching. `app.py` is refactored to use the monitors for all "is streamer live" checks.

**Tech Stack:** Python 3.13, aiohttp (with ClientTimeout), APScheduler, SQLAlchemy async, FastAPI

---

### Task 1: Update config.py — Add STREAMERS list

**Files:**
- Modify: `config.py:66-70`

**Step 1: Add STREAMERS list and derive backward-compatible constants**

Replace lines 66-70 in `config.py`:

```python
# --- 主播配置 ---
# 主播列表 (支持多主播监控)
STREAMERS = [
    {"name": "银剑君", "room_id": "251783"},
]
# Backward compatibility
DEFAULT_STREAMER_NAME = STREAMERS[0]["name"]
STREAMER_NAME = STREAMERS[0]["name"]
DOUYU_ROOM_ID = STREAMERS[0]["room_id"]
```

**Step 2: Verify no import errors**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -c "import config; print(config.STREAMERS, config.STREAMER_NAME, config.DOUYU_ROOM_ID)"`

Expected: `[{'name': '银剑君', 'room_id': '251783'}] 银剑君 251783`

**Step 3: Commit**

```bash
git add config.py
git commit -m "重构: config.py 新增 STREAMERS 列表支持多主播配置"
```

---

### Task 2: Create stream_monitor.py — StreamStatusMonitor class

**Files:**
- Create: `stream_monitor.py`

**Step 1: Create StreamStatusMonitor class**

Create `stream_monitor.py` with the following content:

```python
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("stream_monitor")

# Shared request headers for Douyu API
_DOUYU_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://www.douyu.com',
    'Origin': 'https://www.douyu.com'
}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class StreamStatusMonitor:
    """Monitor a single Douyu streamer's live status via API polling."""

    def __init__(self, room_id: str, streamer_name: str):
        self.room_id = room_id
        self.streamer_name = streamer_name
        self._last_status: Optional[bool] = None  # None = uninitialized

    def is_live(self) -> bool:
        """Return cached live status. Defaults to False if uninitialized."""
        return self._last_status if self._last_status is not None else False

    async def check_is_streaming(self) -> Optional[bool]:
        """Call Douyu API to check live status.

        Returns:
            True if streaming, False if not, None if API error.
        """
        try:
            async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
                async with session.get(
                    f"https://www.douyu.com/betard/{self.room_id}",
                    headers=_DOUYU_HEADERS
                ) as response:
                    if response.status != 200:
                        logger.error(f"[{self.streamer_name}] Failed to get room info: HTTP {response.status}")
                        return None

                    room_info = await response.json()
                    if not room_info or 'room' not in room_info:
                        logger.error(f"[{self.streamer_name}] Invalid room info response format")
                        return None

                    room_data = room_info['room']
                    return room_data.get('show_status') == 1 and room_data.get('videoLoop') == 0

        except aiohttp.ClientError as e:
            logger.error(f"[{self.streamer_name}] Douyu API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.streamer_name}] Unexpected error checking stream status: {e}")
            return None

    async def initialize(self) -> None:
        """Initialize cached status by calling the API directly.
        Called once on application startup.
        """
        status = await self.check_is_streaming()
        if status is not None:
            self._last_status = status
            logger.info(
                f"[{self.streamer_name}] Initialized status: "
                f"{'live' if status else 'offline'}"
            )
        else:
            self._last_status = False
            logger.warning(
                f"[{self.streamer_name}] Failed to get initial status from API, "
                f"defaulting to offline"
            )

    async def detect_change(self) -> Optional[tuple[bool, bool]]:
        """Check for status change since last call.

        Returns:
            (old_status, new_status) tuple if status changed,
            None if no change or API error.
        """
        current = await self.check_is_streaming()
        if current is None:
            return None  # API error, skip this cycle

        if self._last_status is None:
            # First call without initialize(), just cache and skip
            self._last_status = current
            return None

        if current != self._last_status:
            old = self._last_status
            self._last_status = current
            return (old, current)

        return None  # No change
```

**Step 2: Verify the module imports cleanly**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -c "from stream_monitor import StreamStatusMonitor; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add stream_monitor.py
git commit -m "新增: StreamStatusMonitor 类封装直播状态检测逻辑"
```

---

### Task 3: Refactor app.py — imports, global monitors, and startup

**Files:**
- Modify: `app.py:1-12` (imports)
- Modify: `app.py:127-128` (add global monitors dict after scheduler)
- Modify: `app.py:415-466` (startup_event)

**Step 1: Update imports**

At `app.py:1-12`, add the `stream_monitor` import and `local_now` import. Remove unused `aiohttp` import (it's now in stream_monitor.py). The imports section becomes:

```python
import os
import uvicorn
import logging
import argparse
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, AsyncGenerator
from urllib.parse import urlparse
from functools import partial

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, desc, select, inspect
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from video_processor import (
    load_yaml_config,
    cleanup_small_files,
    convert_danmaku,
    encode_video,
    update_video_bvids,
    upload_to_bilibili,
    get_timestamp_from_filename
)
from models import Base, StreamSession, UploadedVideo, local_now
from stream_monitor import StreamStatusMonitor
```

Note: Remove `import requests`, `import threading`, `import aiohttp` — they are no longer used in app.py.

**Step 2: Add global monitors dict**

After `app.py:128` (`scheduler = AsyncIOScheduler()`), add:

```python
stream_monitors: dict[str, StreamStatusMonitor] = {}
```

**Step 3: Refactor startup_event**

Replace `startup_event()` (lines 416-466) with:

```python
@app.on_event("startup")
async def startup_event():
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("数据库初始化完成")

    logger.info("正在加载 YAML 配置...")
    if not load_yaml_config():
        logger.error("无法加载或验证配置文件 config.yaml，部分 API 和定时任务可能无法正常工作")
    else:
        logger.info("YAML 配置加载完成")

    # Initialize stream monitors from config
    for streamer_cfg in config.STREAMERS:
        name = streamer_cfg["name"]
        room_id = streamer_cfg["room_id"]
        monitor = StreamStatusMonitor(room_id, name)
        await monitor.initialize()
        stream_monitors[name] = monitor
        logger.info(f"已初始化主播 {name} (房间号: {room_id}) 的状态监控")

    logger.info("正在启动定时任务调度器...")
    try:
        interval_minutes = config.SCHEDULE_INTERVAL_MINUTES
        scheduler.add_job(
            scheduled_video_pipeline,
            'interval',
            minutes=interval_minutes,
            id='video_pipeline_job',
            replace_existing=True,
            next_run_time=local_now()
        )

        # Add per-streamer status check jobs
        for name, monitor in stream_monitors.items():
            scheduler.add_job(
                partial(scheduled_log_stream_end, name),
                'interval',
                minutes=config.STREAM_STATUS_CHECK_INTERVAL,
                id=f'log_stream_end_{name}',
                replace_existing=True
            )
            logger.info(f"定时任务调度器：已添加主播 {name} 的状态检测任务，每 {config.STREAM_STATUS_CHECK_INTERVAL} 分钟执行一次")

        # Stale session cleanup job (only need one)
        if stream_monitors:
            scheduler.add_job(
                clean_stale_sessions,
                'interval',
                hours=12,
                id='clean_stale_sessions_job',
                replace_existing=True
            )
            logger.info("定时任务调度器：已添加 'clean_stale_sessions_job'，每12小时执行一次")

        scheduler.start()
        logger.info(f"定时任务调度器已启动，每 {interval_minutes} 分钟执行一次 'video_pipeline_job'")
    except Exception as e:
        logger.error(f"启动定时任务调度器失败: {e}", exc_info=True)
```

**Step 4: Verify app starts without errors**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -c "from app import app; print('OK')"`

Expected: `OK` (or import success — the actual server won't start without `uvicorn.run`)

**Step 5: Commit**

```bash
git add app.py
git commit -m "重构: app.py 使用 StreamStatusMonitor 初始化和调度多主播状态检测"
```

---

### Task 4: Refactor app.py — Rewrite scheduled_log_stream_end

**Files:**
- Modify: `app.py:208-370` (replace entire function)

**Step 1: Replace scheduled_log_stream_end function**

Replace the entire `scheduled_log_stream_end` function (lines 208-370) with:

```python
async def scheduled_log_stream_end(streamer_name: str):
    """Scheduled task: check streamer status and record start/end times.

    Uses StreamStatusMonitor.detect_change() for state tracking
    instead of function-attribute caching.
    """
    monitor = stream_monitors.get(streamer_name)
    if not monitor:
        scheduler_logger.error(f"定时任务(log_stream_end): 未找到主播 {streamer_name} 的监控实例")
        return

    current_time = local_now()
    change = await monitor.detect_change()

    if change is None:
        # No change or API error — skip
        scheduler_logger.debug(f"主播 {streamer_name} 状态未变化，仍为: {'直播中' if monitor.is_live() else '未直播'}")
        return

    old_status, new_status = change
    scheduler_logger.info(
        f"检测到主播 {streamer_name} 状态变化: "
        f"{'未直播→直播中' if new_status else '直播中→未直播'}"
    )

    async with AsyncSessionLocal() as db:
        try:
            if new_status:
                # Went live — record start time (adjusted backward)
                adjusted_start_time = current_time - timedelta(minutes=config.STREAM_START_TIME_ADJUSTMENT)
                new_session = StreamSession(
                    streamer_name=streamer_name,
                    start_time=adjusted_start_time,
                    end_time=None
                )
                db.add(new_session)
                scheduler_logger.info(
                    f"已记录主播 {streamer_name} 的上播时间: {adjusted_start_time} "
                    f"(已自动调整-{config.STREAM_START_TIME_ADJUSTMENT}分钟)"
                )
            else:
                # Went offline — find open session and set end_time
                query = select(StreamSession).filter(
                    StreamSession.streamer_name == streamer_name,
                    StreamSession.start_time.is_not(None),
                    StreamSession.end_time.is_(None)
                ).order_by(desc(StreamSession.start_time))

                result = await db.execute(query)
                recent_session = result.scalars().first()

                if recent_session:
                    recent_session.end_time = current_time
                    scheduler_logger.info(f"已记录主播 {streamer_name} 的下播时间: {current_time}")
                else:
                    new_session = StreamSession(
                        streamer_name=streamer_name,
                        start_time=None,
                        end_time=current_time
                    )
                    db.add(new_session)
                    scheduler_logger.info(f"创建新记录并添加主播 {streamer_name} 的下播时间: {current_time}")

            await db.commit()

            # If streamer went offline and PROCESS_AFTER_STREAM_END is enabled,
            # schedule a delayed pipeline run instead of blocking with sleep
            if not new_status and config.PROCESS_AFTER_STREAM_END:
                scheduler_logger.info("检测到主播下播，且已启用'仅下播后处理'选项，3分钟后触发视频处理和上传流程")
                scheduler.add_job(
                    scheduled_video_pipeline,
                    'date',
                    run_date=local_now() + timedelta(minutes=3),
                    id=f'post_stream_pipeline_{streamer_name}',
                    replace_existing=True
                )

        except Exception as e:
            scheduler_logger.error(f"定时任务(log_stream_end): 记录直播状态时出错: {e}", exc_info=True)
            await db.rollback()
```

**Step 2: Commit**

```bash
git add app.py
git commit -m "重构: scheduled_log_stream_end 使用 StreamStatusMonitor 替代函数属性缓存"
```

---

### Task 5: Refactor app.py — Eliminate duplicated "is live" checks

**Files:**
- Modify: `app.py:130-158` (scheduled_video_pipeline, lines 136-158)
- Modify: `app.py:764-788` (trigger_processing_tasks, lines 768-788)
- Modify: `app.py:816-843` (trigger_upload_tasks, lines 823-843)

**Step 1: Replace the "is live" check in scheduled_video_pipeline**

In `scheduled_video_pipeline()`, replace lines 136-158 (the `PROCESS_AFTER_STREAM_END` block) with:

```python
    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            scheduler_logger.info(
                f"定时任务：检测到主播 {config.STREAMER_NAME} 正在直播中，"
                f"当前配置为仅下播后处理，跳过压制和上传任务"
            )
            return
        scheduler_logger.info(f"定时任务：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行压制和上传任务")
```

**Step 2: Replace the "is live" check in trigger_processing_tasks**

In `trigger_processing_tasks()`, replace lines 768-788 with:

```python
    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行压制任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行压制任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行压制任务")
```

**Step 3: Replace the "is live" check in trigger_upload_tasks**

In `trigger_upload_tasks()`, replace lines 823-843 with:

```python
    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行上传任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行上传任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行上传任务")
```

**Step 4: Also fix timezone in startup_event's next_run_time**

In `startup_event()`, the line `next_run_time=datetime.now()` should already be `local_now()` from Task 3. Verify this is done.

**Step 5: Clean up unused imports**

Verify that `requests`, `threading`, and `aiohttp` are no longer imported in `app.py`. (Should already be done in Task 3 Step 1, but double check.)

**Step 6: Verify app starts cleanly**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -c "from app import app; print('OK')"`

Expected: `OK`

**Step 7: Commit**

```bash
git add app.py
git commit -m "重构: 消除3处重复的直播状态查询代码，统一使用 StreamStatusMonitor.is_live()"
```

---

### Task 6: Final cleanup and verification

**Files:**
- Verify: `app.py`, `config.py`, `stream_monitor.py`

**Step 1: Full syntax check**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -m py_compile app.py && python -m py_compile config.py && python -m py_compile stream_monitor.py && echo "All files compile OK"`

Expected: `All files compile OK`

**Step 2: Verify all imports resolve**

Run: `cd /Users/wqq/Code/Personal/video_processor && python -c "from app import app, stream_monitors, scheduled_log_stream_end; from stream_monitor import StreamStatusMonitor; from config import STREAMERS; print('All imports OK')"`

Expected: `All imports OK`

**Step 3: Verify no leftover references to old patterns**

Search for any remaining function-attribute patterns or old `aiohttp` usage in `app.py`:

Run: `grep -n "last_stream_status\|import aiohttp\|import requests\|import threading" app.py`

Expected: No output (no matches)

**Step 4: Commit any final cleanup**

```bash
git add -A
git commit -m "重构完成: 直播状态检测代码重构清理"
```
