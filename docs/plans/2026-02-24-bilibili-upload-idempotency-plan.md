# B站分P上传幂等与稳定性 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改数据库结构的前提下，让同一直播场次的 B 站上传流程具备幂等性（避免重复建稿件），并修复分P编号与BVID回填稳定性问题。

**Architecture:** 在 `uploader.upload_to_bilibili()` 内基于“场次时间窗（带 buffer）”做轻量状态机：`READY_APPEND / PENDING_BVID / NEW_UPLOAD`。用 DB 查询作为幂等锚点：若场次时间窗内存在 `bvid=NULL` 记录则整场次暂停上传；追加分P的 `P{n}` 通过“该时间窗内已记录的视频数量”计算；回填 BVID 同时查询 `pubed,is_pubing`；将 `time.sleep` 替换为 `await asyncio.sleep` 避免阻塞。

**Tech Stack:** Python 3.13 (`uv run`), SQLAlchemy async + SQLite, pytest/pytest-asyncio, bilitool

---

### Task 0: Prepare a clean worktree

**Files:**
- None

**Step 1: Ensure a clean git status**

Run: `git status --porcelain`  
Expected: empty output

**Step 2: (Optional but recommended) Create a dedicated worktree**

Run: `git worktree add ../video_processor-uploader-idempotency -b codex/uploader-idempotency`  
Expected: new directory created and checked out

---

### Task 1: Add a failing test for “pending BVID skips new upload”

**Files:**
- Create: `tests/unit/test_uploader_idempotency.py`

**Step 1: Write the failing test**

Create `tests/unit/test_uploader_idempotency.py`:

```python
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from models import Base, StreamSession, UploadedVideo


@pytest.mark.asyncio
async def test_pending_bvid_session_skips_new_upload(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    # --- Arrange: temp DB ---
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # --- Arrange: YAML config injected ---
    uploader.yaml_config = {
        "title": "测试标题{time}",
        "tid": 171,
        "tag": "t1",
        "source": "s",
        "cover": "",
        "dynamic": "",
        "desc": "d",
        "cdn": None,
    }

    # --- Arrange: fake controllers ---
    class FakeLoginController:
        def check_bilibili_login(self):
            return True

    class FakeUploadController:
        def __init__(self):
            self.upload_calls = 0

        def upload_video_entry(self, *args, **kwargs):
            self.upload_calls += 1
            return True

        def append_video_entry(self, *args, **kwargs):
            raise AssertionError("append should not be called in this test")

    class FakeFeedController:
        def get_video_dict_info(self, *args, **kwargs):
            return {}

    fake_uploader = FakeUploadController()
    monkeypatch.setattr(uploader, "LoginController", FakeLoginController)
    monkeypatch.setattr(uploader, "UploadController", lambda: fake_uploader)
    monkeypatch.setattr(uploader, "FeedController", FakeFeedController)

    # Avoid real sleeping in tests
    async def _fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(uploader.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("time.sleep should not be called")))

    # --- Arrange: temp upload folder with one MP4 file ---
    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")

    file_time = datetime(2026, 2, 24, 10, 0, 0)
    video_path = tmp_path / f"洞主录播{file_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    video_path.write_text("x", encoding="utf-8")

    # --- Arrange: one session covering the file time ---
    session = StreamSession(
        streamer_name="洞主",
        start_time=file_time - timedelta(hours=1),
        end_time=file_time + timedelta(hours=1),
    )

    # Pending record in the same time window (bvid is NULL)
    pending = UploadedVideo(
        bvid=None,
        title="测试标题",
        first_part_filename="already_uploaded_first.mp4",
        upload_time=file_time,
    )

    async with SessionLocal() as db:
        db.add_all([session, pending])
        await db.commit()

        # --- Act ---
        await uploader.upload_to_bilibili(db)

    # --- Assert ---
    assert fake_uploader.upload_calls == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_pending_bvid_session_skips_new_upload -v`  
Expected: FAIL (currently会触发新建稿件上传，`upload_calls == 1`)

---

### Task 2: Implement “PENDING_BVID skips session” behavior

**Files:**
- Modify: `uploader.py`
- Test: `tests/unit/test_uploader_idempotency.py`

**Step 1: Implement minimal code change**

In `upload_to_bilibili()` 的每个 `session_id` 处理逻辑中：

1. 维持现有 “查 `bvid IS NOT NULL`” 的逻辑  
2. 当未查到 `existing_bvid` 时，新增一次查询：若该场次时间窗内存在 `bvid IS NULL` 的记录，则 `continue` 跳过该场次（并打印清晰日志）

```python
# Skip this session if there's a pending record without BVID.
pending_query = select(UploadedVideo).filter(
    UploadedVideo.upload_time.between(range_start, range_end),
    UploadedVideo.bvid.is_(None),
).limit(1)
```

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_pending_bvid_session_skips_new_upload -v`  
Expected: PASS

**Step 3: Commit**

Run:

```bash
git add uploader.py tests/unit/test_uploader_idempotency.py
git commit -m "fix: skip session upload when bvid pending"
```

---

### Task 3: Add a failing test for correct part numbering and part title propagation

**Files:**
- Modify: `tests/unit/test_uploader_idempotency.py`

**Step 1: Add a failing test**

Append a new test in `tests/unit/test_uploader_idempotency.py`:

```python
@pytest.mark.asyncio
async def test_append_uses_time_window_count_and_sets_video_name(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    uploader.yaml_config = {
        "title": "测试标题{time}",
        "tid": 171,
        "tag": "t1",
        "source": "s",
        "cover": "",
        "dynamic": "",
        "desc": "d",
        "cdn": None,
    }

    class FakeLoginController:
        def check_bilibili_login(self):
            return True

    class FakeUploadController:
        def __init__(self):
            self.append_calls = []

        def upload_video_entry(self, *args, **kwargs):
            raise AssertionError("upload should not be called in this test")

        def append_video_entry(self, video_path, bvid, cdn=None, video_name=None):
            self.append_calls.append({"video_path": video_path, "bvid": bvid, "video_name": video_name})
            return True

    class FakeFeedController:
        def get_video_dict_info(self, *args, **kwargs):
            return {}

    fake_uploader = FakeUploadController()
    monkeypatch.setattr(uploader, "LoginController", FakeLoginController)
    monkeypatch.setattr(uploader, "UploadController", lambda: fake_uploader)
    monkeypatch.setattr(uploader, "FeedController", FakeFeedController)

    async def _fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(uploader.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("time.sleep should not be called")))

    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")

    file_time = datetime(2026, 2, 24, 10, 0, 0)
    new_part = tmp_path / f"洞主录播{file_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    new_part.write_text("x", encoding="utf-8")

    session = StreamSession(
        streamer_name="洞主",
        start_time=file_time - timedelta(hours=1),
        end_time=file_time + timedelta(hours=1),
    )

    # Existing uploaded records in the same time window:
    # - one record has bvid (the main稿件)
    # - two records represent already appended parts (bvid stays NULL in current schema)
    main = UploadedVideo(
        bvid="BV1TEST0000000000",
        title="主稿件",
        first_part_filename="p1.mp4",
        upload_time=file_time - timedelta(minutes=30),
    )
    p2 = UploadedVideo(
        bvid=None,
        title="P2",
        first_part_filename="p2.mp4",
        upload_time=file_time - timedelta(minutes=20),
    )
    p3 = UploadedVideo(
        bvid=None,
        title="P3",
        first_part_filename="p3.mp4",
        upload_time=file_time - timedelta(minutes=10),
    )

    async with SessionLocal() as db:
        db.add_all([session, main, p2, p3])
        await db.commit()

        await uploader.upload_to_bilibili(db)

    assert len(fake_uploader.append_calls) == 1
    assert fake_uploader.append_calls[0]["bvid"] == "BV1TEST0000000000"
    assert fake_uploader.append_calls[0]["video_name"] is not None
    assert fake_uploader.append_calls[0]["video_name"].startswith("P4 ")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_append_uses_time_window_count_and_sets_video_name -v`  
Expected: FAIL（当前实现通常会从 `P2` 开始，且 `video_name` 为 `None`）

---

### Task 4: Implement “time-window count P number” + pass `video_name`

**Files:**
- Modify: `uploader.py`
- Test: `tests/unit/test_uploader_idempotency.py`

**Step 1: Implement minimal code change**

在追加分P分支中：

1. 用时间窗计数（`COUNT` 或 `scalars().all()` 都可，但优先 `COUNT`）计算 `start_part_number`  
2. 调用 `append_video_entry(..., video_name=part_title)`

```python
# Count all uploaded records in the session window to compute next part number.
count_query = select(func.count()).select_from(UploadedVideo).filter(
    UploadedVideo.upload_time.between(range_start, range_end),
)
```

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_append_uses_time_window_count_and_sets_video_name -v`  
Expected: PASS

**Step 3: Commit**

```bash
git add uploader.py tests/unit/test_uploader_idempotency.py
git commit -m "fix: compute part number by session window and set part title"
```

---

### Task 5: Add a failing test for Feed status types and non-blocking sleep

**Files:**
- Modify: `tests/unit/test_uploader_idempotency.py`

**Step 1: Add a failing test**

Add a test ensuring `FeedController.get_video_dict_info()` is called with `pubed` included (or uses default), and `time.sleep` is never called:

```python
@pytest.mark.asyncio
async def test_new_upload_fetches_bvid_with_pubed_and_uses_async_sleep(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    uploader.yaml_config = {
        "title": "测试标题{time}",
        "tid": 171,
        "tag": "t1",
        "source": "s",
        "cover": "",
        "dynamic": "",
        "desc": "d",
        "cdn": None,
    }

    class FakeLoginController:
        def check_bilibili_login(self):
            return True

    class FakeUploadController:
        def upload_video_entry(self, *args, **kwargs):
            return True

        def append_video_entry(self, *args, **kwargs):
            return True

    class FakeFeedController:
        def __init__(self):
            self.calls = []

        def get_video_dict_info(self, size=20, status_type=""):
            self.calls.append({"size": size, "status_type": status_type})
            return {"测试标题2026年02月24日": "BV1TEST0000000000"}

    feed = FakeFeedController()
    monkeypatch.setattr(uploader, "LoginController", FakeLoginController)
    monkeypatch.setattr(uploader, "UploadController", FakeUploadController)
    monkeypatch.setattr(uploader, "FeedController", lambda: feed)

    sleep_calls = []

    async def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(uploader.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("time.sleep should not be called")))

    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")

    file_time = datetime(2026, 2, 24, 10, 0, 0)
    video_path = tmp_path / f"洞主录播{file_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    video_path.write_text("x", encoding="utf-8")

    session = StreamSession(
        streamer_name="洞主",
        start_time=file_time - timedelta(hours=1),
        end_time=file_time + timedelta(hours=1),
    )

    async with SessionLocal() as db:
        db.add(session)
        await db.commit()
        await uploader.upload_to_bilibili(db)

    assert feed.calls, "expected FeedController.get_video_dict_info to be called"
    assert "pubed" in feed.calls[0]["status_type"]
    assert sleep_calls, "expected asyncio.sleep to be used"
```

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_new_upload_fetches_bvid_with_pubed_and_uses_async_sleep -v`  
Expected: FAIL（当前实现使用 `is_pubing` 且调用 `time.sleep`）

---

### Task 6: Implement Feed status + replace `time.sleep` with `await asyncio.sleep`

**Files:**
- Modify: `uploader.py`
- Test: `tests/unit/test_uploader_idempotency.py`

**Step 1: Implement minimal code change**

1. `FeedController.get_video_dict_info(...)` 改为包含 `pubed,is_pubing`（或使用默认参数，确保覆盖 `pubed`）  
2. 所有 `time.sleep(...)` 替换为 `await asyncio.sleep(...)`  
3. 需要 `import asyncio`

**Step 2: Run test**

Run: `uv run pytest tests/unit/test_uploader_idempotency.py::test_new_upload_fetches_bvid_with_pubed_and_uses_async_sleep -v`  
Expected: PASS

**Step 3: Commit**

```bash
git add uploader.py tests/unit/test_uploader_idempotency.py
git commit -m "fix: improve bvid fetch and avoid blocking sleep"
```

---

### Task 7: Add buffered session window (optional but recommended)

**Files:**
- Modify: `uploader.py`

**Step 1: Expand session time window with buffer**

Use `timedelta(minutes=config.STREAM_START_TIME_ADJUSTMENT)` to expand:

- 文件归属判定（`start_time/end_time`）  
- DB 查询的 `range_start/range_end`

**Step 2: Quick regression**

Run: `uv run pytest -q`  
Expected: PASS

**Step 3: Commit**

```bash
git add uploader.py
git commit -m "fix: add buffered session time window"
```

---

### Task 8: Final verification

**Files:**
- None

**Step 1: Run full test suite**

Run: `uv run pytest -v`  
Expected: PASS

