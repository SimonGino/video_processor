# Stream Status Monitor Refactoring Design

## Problem

The current stream status detection code in `app.py` (`scheduled_log_stream_end`) has several issues:

1. **Fragile state management** — uses function attributes (`func.last_stream_status`) for caching, which is lost on restart and requires error-prone DB-based initialization
2. **Wrong initialization source** — startup initializes state from DB records instead of calling the actual Douyu API
3. **3x duplicated code** — "check if streamer is live" logic is copy-pasted across `scheduled_video_pipeline`, `trigger_processing_tasks`, and `trigger_upload_tasks`
4. **No request timeout** — aiohttp calls to Douyu API have no timeout, risking indefinite hangs
5. **Blocking sleep** — `asyncio.sleep(180)` inside the scheduled job blocks the job instance for 3 minutes
6. **Timezone inconsistency** — `datetime.now()` used in `app.py` vs `local_now()` (UTC+8) in `models.py`

## Design

### Approach: New `StreamStatusMonitor` class

Create `stream_monitor.py` with a `StreamStatusMonitor` class. One instance per monitored streamer, stored in a global dict in `app.py`.

### StreamStatusMonitor class

```
StreamStatusMonitor
├── __init__(room_id, streamer_name)
├── _last_status: Optional[bool]         # None = uninitialized
├── async check_is_streaming() -> Optional[bool]  # Douyu API call, returns True/False/None(error)
├── async initialize()                   # Call API on startup for real initial status
├── async detect_change() -> Optional[tuple[bool, bool]]  # (old, new) if changed, None otherwise
└── is_live() -> bool                    # Return cached status for reuse
```

Key behaviors:
- `check_is_streaming()` uses `aiohttp.ClientTimeout(total=10)`
- `initialize()` calls the API directly on startup — no DB guessing
- API errors return `None`, causing `detect_change()` to skip the cycle (no false state changes)
- `is_live()` provides a single reusable check, eliminating 3 duplicate DB queries

### Config changes

`config.py` adds a `STREAMERS` list for multi-streamer support:

```python
STREAMERS = [
    {"name": "银剑君", "room_id": "251783"},
]
# Backward compatibility
STREAMER_NAME = STREAMERS[0]["name"]
DOUYU_ROOM_ID = STREAMERS[0]["room_id"]
```

### app.py changes

1. **Global monitors dict**: `stream_monitors: dict[str, StreamStatusMonitor] = {}`
2. **Startup**: iterate `config.STREAMERS`, create and initialize a monitor for each, add per-streamer APScheduler jobs
3. **`scheduled_log_stream_end(streamer_name)`**: accepts streamer name param, uses `monitor.detect_change()` instead of inline API calls and function-attribute caching
4. **Post-stream trigger**: replace `asyncio.sleep(180)` with `scheduler.add_job(..., trigger='date', run_date=now+3min)`
5. **Eliminate duplication**: replace 3 inline "is streaming" DB queries with `stream_monitors[name].is_live()`
6. **Timezone**: all `datetime.now()` calls replaced with `models.local_now()`

### Files affected

| File | Change |
|------|--------|
| `stream_monitor.py` | **New** — StreamStatusMonitor class |
| `config.py` | Add `STREAMERS` list, derive `STREAMER_NAME`/`DOUYU_ROOM_ID` from it |
| `app.py` | Refactor `scheduled_log_stream_end`, startup init, eliminate duplication, fix timezone |

### Files NOT changed

- `video_processor.py` — no stream status detection logic
- `models.py` — models unchanged
- `config.yaml` — upload metadata unchanged
- `service.sh` — unchanged

### Error handling

- API timeout/error → skip this check cycle, do not change cached status
- Monitor initialization failure → log warning, default to "not live", don't block other streamers
- Maintain existing "continue on error" fault tolerance strategy
