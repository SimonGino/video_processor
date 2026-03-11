"""Tests for startup session creation in scheduled_log_stream_end.

Covers three scenarios:
1. Streamer online at startup, no open session → creates one
2. Streamer online, open session already exists → skips
3. Streamer offline → does not create
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta


class _FakeScalarsResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _FakeScalarsResult(self._value)


class _FakeDbSession:
    def __init__(self, existing_session=None):
        self._existing = existing_session
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        return _FakeExecuteResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        pass


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSessionFactory:
    def __init__(self, db):
        self._db = db

    def __call__(self):
        return _FakeSessionContext(self._db)


class _FakeMonitor:
    def __init__(self, *, live: bool, change=None):
        self._live = live
        self._change = change

    def is_live(self):
        return self._live

    async def detect_change(self):
        return self._change


@pytest.mark.asyncio
async def test_startup_live_no_session_creates_one(monkeypatch):
    """Streamer online at startup with no open session → creates a new session."""
    from douyu2bilibili import config as config_module
    from douyu2bilibili import scheduler as scheduler_module

    fake_db = _FakeDbSession(existing_session=None)
    monitor = _FakeMonitor(live=True, change=None)

    monkeypatch.setattr(
        scheduler_module,
        "_get_app_deps",
        lambda: (_FakeSessionFactory(fake_db), None, {"test_streamer": monitor}),
    )
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)

    await scheduler_module.scheduled_log_stream_end("test_streamer")

    assert len(fake_db.added) == 1
    session = fake_db.added[0]
    assert session.streamer_name == "test_streamer"
    assert session.start_time is not None
    assert session.end_time is None
    assert fake_db.committed is True


@pytest.mark.asyncio
async def test_startup_live_with_existing_session_skips(monkeypatch):
    """Streamer online but open session already exists → no new session created."""
    from douyu2bilibili import config as config_module
    from douyu2bilibili import scheduler as scheduler_module

    existing = MagicMock()
    existing.end_time = None
    fake_db = _FakeDbSession(existing_session=existing)
    monitor = _FakeMonitor(live=True, change=None)

    monkeypatch.setattr(
        scheduler_module,
        "_get_app_deps",
        lambda: (_FakeSessionFactory(fake_db), None, {"test_streamer": monitor}),
    )
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)

    await scheduler_module.scheduled_log_stream_end("test_streamer")

    assert len(fake_db.added) == 0
    assert fake_db.committed is False


@pytest.mark.asyncio
async def test_startup_offline_does_not_create(monkeypatch):
    """Streamer offline, detect_change returns None → no session created."""
    from douyu2bilibili import config as config_module
    from douyu2bilibili import scheduler as scheduler_module

    fake_db = _FakeDbSession(existing_session=None)
    monitor = _FakeMonitor(live=False, change=None)

    monkeypatch.setattr(
        scheduler_module,
        "_get_app_deps",
        lambda: (_FakeSessionFactory(fake_db), None, {"test_streamer": monitor}),
    )
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)

    await scheduler_module.scheduled_log_stream_end("test_streamer")

    assert len(fake_db.added) == 0
    assert fake_db.committed is False
