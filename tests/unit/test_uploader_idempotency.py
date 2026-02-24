from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from models import Base, StreamSession, UploadedVideo


@pytest.mark.asyncio
async def test_pending_bvid_session_skips_new_upload(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

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

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

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
    pending = UploadedVideo(
        bvid=None,
        title="pending",
        first_part_filename="already_uploaded_first.mp4",
        upload_time=file_time,
    )

    async with session_local() as db:
        db.add_all([session, pending])
        await db.commit()

        await uploader.upload_to_bilibili(db)

    assert fake_uploader.upload_calls == 0


@pytest.mark.asyncio
async def test_append_uses_time_window_count_and_sets_video_name(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

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
            self.append_calls.append(
                {"video_path": video_path, "bvid": bvid, "video_name": video_name}
            )
            return True

    class FakeFeedController:
        def get_video_dict_info(self, *args, **kwargs):
            return {}

    fake_uploader = FakeUploadController()
    monkeypatch.setattr(uploader, "LoginController", FakeLoginController)
    monkeypatch.setattr(uploader, "UploadController", lambda: fake_uploader)
    monkeypatch.setattr(uploader, "FeedController", FakeFeedController)

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

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
    main = UploadedVideo(
        bvid="BV1TEST0000000000",
        title="main",
        first_part_filename="p1.mp4",
        upload_time=file_time - timedelta(minutes=30),
    )
    p2 = UploadedVideo(
        bvid=None,
        title="p2",
        first_part_filename="p2.mp4",
        upload_time=file_time - timedelta(minutes=20),
    )
    p3 = UploadedVideo(
        bvid=None,
        title="p3",
        first_part_filename="p3.mp4",
        upload_time=file_time - timedelta(minutes=10),
    )

    async with session_local() as db:
        db.add_all([session, main, p2, p3])
        await db.commit()

        await uploader.upload_to_bilibili(db)

    assert len(fake_uploader.append_calls) == 1
    assert fake_uploader.append_calls[0]["bvid"] == "BV1TEST0000000000"
    assert fake_uploader.append_calls[0]["video_name"] is not None
    assert fake_uploader.append_calls[0]["video_name"].startswith("P4 ")


@pytest.mark.asyncio
async def test_new_upload_fetches_bvid_with_pubed_and_uses_async_sleep(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

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

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

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

    async with session_local() as db:
        db.add(session)
        await db.commit()

        await uploader.upload_to_bilibili(db)

    assert feed.calls
    assert "pubed" in feed.calls[0]["status_type"]
    assert sleep_calls


@pytest.mark.asyncio
async def test_session_assignment_uses_buffer_minutes(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

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
            self.upload_calls = 0

        def upload_video_entry(self, *args, **kwargs):
            self.upload_calls += 1
            return True

        def append_video_entry(self, *args, **kwargs):
            return True

    class FakeFeedController:
        def get_video_dict_info(self, *args, **kwargs):
            return {}

    fake_uploader = FakeUploadController()
    monkeypatch.setattr(uploader, "LoginController", FakeLoginController)
    monkeypatch.setattr(uploader, "UploadController", lambda: fake_uploader)
    monkeypatch.setattr(uploader, "FeedController", FakeFeedController)

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)

    now = datetime.now().replace(second=0, microsecond=0)
    session_start = now
    session_end = now + timedelta(hours=1)
    video_time = now - timedelta(minutes=5)

    video_path = tmp_path / f"洞主录播{video_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    video_path.write_text("x", encoding="utf-8")

    session = StreamSession(
        streamer_name="洞主",
        start_time=session_start,
        end_time=session_end,
    )

    async with session_local() as db:
        db.add(session)
        await db.commit()

        await uploader.upload_to_bilibili(db)

    assert fake_uploader.upload_calls == 1
