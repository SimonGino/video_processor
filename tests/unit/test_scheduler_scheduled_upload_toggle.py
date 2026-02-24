import pytest


class _FakeLoop:
    async def run_in_executor(self, _executor, func):
        return func()


class _FakeDbSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


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


@pytest.mark.asyncio
async def test_scheduled_pipeline_skips_upload_when_scheduled_upload_disabled(monkeypatch):
    import config as config_module
    import scheduler as scheduler_module

    events = []
    fake_db = _FakeDbSession()

    monkeypatch.setattr(scheduler_module.asyncio, "get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr(
        scheduler_module,
        "_get_app_deps",
        lambda: (_FakeSessionFactory(fake_db), None, {}),
    )

    monkeypatch.setattr(config_module, "PROCESS_AFTER_STREAM_END", False)
    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "SCHEDULED_UPLOAD_ENABLED", False, raising=False)

    monkeypatch.setattr(scheduler_module, "cleanup_small_files", lambda: events.append("cleanup"))
    monkeypatch.setattr(scheduler_module, "convert_danmaku", lambda: events.append("convert"))
    monkeypatch.setattr(scheduler_module, "encode_video", lambda: events.append("encode"))

    def fake_load_yaml_config():
        events.append("load_yaml")
        return True

    async def fake_update_video_bvids(_db):
        events.append("update_bvids")

    async def fake_upload_to_bilibili(_db):
        events.append("upload")

    monkeypatch.setattr(scheduler_module, "load_yaml_config", fake_load_yaml_config)
    monkeypatch.setattr(scheduler_module, "update_video_bvids", fake_update_video_bvids)
    monkeypatch.setattr(scheduler_module, "upload_to_bilibili", fake_upload_to_bilibili)

    await scheduler_module.scheduled_video_pipeline()

    assert events == ["cleanup", "convert", "encode"]
    assert fake_db.closed is False


@pytest.mark.asyncio
async def test_manual_upload_task_still_runs_when_scheduled_upload_disabled(monkeypatch):
    import config as config_module
    import scheduler as scheduler_module

    events = []

    monkeypatch.setattr(config_module, "SCHEDULED_UPLOAD_ENABLED", False, raising=False)

    monkeypatch.setattr(scheduler_module, "load_yaml_config", lambda: True)

    async def fake_update_video_bvids(db):
        events.append(("update_bvids", db))

    async def fake_upload_to_bilibili(db):
        events.append(("upload", db))

    monkeypatch.setattr(scheduler_module, "update_video_bvids", fake_update_video_bvids)
    monkeypatch.setattr(scheduler_module, "upload_to_bilibili", fake_upload_to_bilibili)

    fake_db = object()
    await scheduler_module.run_upload_async(fake_db)

    assert events == [("update_bvids", fake_db), ("upload", fake_db)]
