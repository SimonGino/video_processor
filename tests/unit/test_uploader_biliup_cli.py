from pathlib import Path
from types import SimpleNamespace

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from models import Base, StreamSession, UploadedVideo


def test_extract_biliup_bvid_from_app_submit_output():
    import uploader

    output = (
        'INFO biliup::uploader::bilibili: ResponseData { code: 0, data: Some(Object '
        '{"aid": Number(1), "bvid": String("BV1y9fsBbEma")}), message: "0" }'
    )

    assert uploader._extract_biliup_bvid(output) == "BV1y9fsBbEma"


def test_biliup_upload_video_entry_builds_command_and_returns_bvid(monkeypatch, tmp_path: Path):
    import uploader

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"x")

    monkeypatch.setattr(
        uploader,
        "_get_biliup_runtime",
        lambda: {
            "bin": "/opt/biliup",
            "cookies": "/opt/cookies.json",
            "submit": "app",
            "line": None,
        },
    )

    calls = {}

    def fake_run(cmd):
        calls["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=(
                'INFO ... Object {"code": Number(0), "data": Object {"bvid": '
                'String("BV1y9fsBbEma")}}\nINFO ... APP接口投稿成功\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(uploader, "_run_biliup_cli_command", fake_run)

    ok, bvid = uploader._biliup_upload_video_entry(
        video_path=str(video_path),
        tid=171,
        title="测试标题",
        desc="测试简介",
        tag="测试,CLI",
        source="",
        cover="",
        dynamic="",
        copyright=1,
    )

    assert ok is True
    assert bvid == "BV1y9fsBbEma"
    assert calls["cmd"][:4] == ["/opt/biliup", "-u", "/opt/cookies.json", "upload"]
    assert "--submit" in calls["cmd"]
    assert "app" in calls["cmd"]
    assert "--tid" in calls["cmd"]
    assert "171" in calls["cmd"]
    assert str(video_path) == calls["cmd"][-1]


def test_biliup_append_video_entry_uses_vid_and_detects_modify_success(monkeypatch, tmp_path: Path):
    import uploader

    video_path = tmp_path / "video_p2.mp4"
    video_path.write_bytes(b"x")

    monkeypatch.setattr(
        uploader,
        "_get_biliup_runtime",
        lambda: {
            "bin": "/opt/biliup",
            "cookies": "/opt/cookies.json",
            "submit": "app",
            "line": None,
        },
    )

    calls = {}

    def fake_run(cmd):
        calls["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout='INFO biliup::uploader::bilibili: 稿件修改成功\n',
            stderr="",
        )

    monkeypatch.setattr(uploader, "_run_biliup_cli_command", fake_run)

    ok = uploader._biliup_append_video_entry(
        video_path=str(video_path),
        bvid="BV1y9fsBbEma",
        part_title="P2 12:00:00",
    )

    assert ok is True
    assert calls["cmd"][:4] == ["/opt/biliup", "-u", "/opt/cookies.json", "append"]
    assert "--vid" in calls["cmd"]
    assert "BV1y9fsBbEma" in calls["cmd"]
    assert "--title" not in calls["cmd"]
    assert str(video_path) == calls["cmd"][-1]


def test_biliup_append_detects_rate_limit_21540(monkeypatch, tmp_path: Path):
    import uploader

    video_path = tmp_path / "video_p3.mp4"
    video_path.write_bytes(b"x")

    monkeypatch.setattr(
        uploader,
        "_get_biliup_runtime",
        lambda: {
            "bin": "/opt/biliup",
            "cookies": "/opt/cookies.json",
            "submit": "app",
            "line": None,
        },
    )

    def fake_run(_cmd):
        return SimpleNamespace(
            returncode=1,
            stdout='Object {"code": Number(21540), "message": String("请求过于频繁，请稍后再试")}\n',
            stderr='{"code":21540,"message":"请求过于频繁，请稍后再试","ttl":1}\n',
        )

    monkeypatch.setattr(uploader, "_run_biliup_cli_command", fake_run)

    ok, rate_limited = uploader._biliup_append_video_entry_with_status(
        video_path=str(video_path),
        bvid="BV1y9fsBbEma",
        part_title="P3 13:00:00",
    )

    assert ok is False
    assert rate_limited is True


@pytest.mark.asyncio
async def test_upload_to_bilibili_with_biliup_cli_persists_bvid(tmp_path: Path, monkeypatch):
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
        "tag": "t1,t2",
        "source": "",
        "cover": "",
        "dynamic": "",
        "desc": "d",
        "cdn": None,
    }

    monkeypatch.setattr(uploader, "_detect_uploader_backend", lambda: "biliup_cli")
    monkeypatch.setattr(uploader, "_biliup_check_login", lambda: True)
    monkeypatch.setattr(
        uploader,
        "_biliup_upload_video_entry",
        lambda **kwargs: (True, "BV1y9fsBbEma"),
    )

    append_calls = []

    def fake_append(**kwargs):
        append_calls.append(kwargs)
        return True

    monkeypatch.setattr(uploader, "_biliup_append_video_entry", fake_append)

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", False)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)

    base_time = datetime(2026, 2, 24, 17, 30, 0)
    p1 = tmp_path / f"洞主录播{base_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    p2_time = base_time + timedelta(hours=1)
    p2 = tmp_path / f"洞主录播{p2_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    p1.write_text("x", encoding="utf-8")
    p2.write_text("x", encoding="utf-8")

    session = StreamSession(
        streamer_name="洞主",
        start_time=base_time - timedelta(minutes=5),
        end_time=base_time + timedelta(hours=2),
    )

    async with session_local() as db:
        db.add(session)
        await db.commit()

        await uploader.upload_to_bilibili(db)

        result = await db.execute(
            uploader.select(UploadedVideo).order_by(UploadedVideo.id)
        )
        rows = result.scalars().all()

    # 首个视频应写入 BVID；后续分P在同轮不会追加（保持原有策略）
    assert len(rows) == 1
    assert rows[0].bvid == "BV1y9fsBbEma"
    assert rows[0].first_part_filename == p1.name
    assert append_calls == []


@pytest.mark.asyncio
async def test_update_video_bvids_skips_when_biliup_cli_backend(monkeypatch):
    import uploader

    monkeypatch.setattr(uploader, "_detect_uploader_backend", lambda: "biliup_cli")

    await uploader.update_video_bvids(db=None)


@pytest.mark.asyncio
async def test_biliup_append_async_wrapper_uses_to_thread(monkeypatch):
    import uploader

    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        calls["func"] = func
        calls["args"] = args
        calls["kwargs"] = kwargs
        return (True, False)

    monkeypatch.setattr(uploader.asyncio, "to_thread", fake_to_thread)

    result = await uploader._biliup_append_video_entry_with_status_async(
        video_path="/tmp/p.mp4",
        bvid="BV1y9fsBbEma",
        part_title="P1 00:00:00",
    )

    assert result == (True, False)
    assert calls["func"] is uploader._biliup_append_video_entry_with_status
    assert calls["kwargs"]["video_path"] == "/tmp/p.mp4"
    assert calls["kwargs"]["bvid"] == "BV1y9fsBbEma"


@pytest.mark.asyncio
async def test_upload_to_bilibili_biliup_cli_cools_down_and_retries_on_21540(tmp_path: Path, monkeypatch):
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
        "source": "",
        "cover": "",
        "dynamic": "",
        "desc": "d",
        "cdn": None,
    }

    monkeypatch.setattr(uploader, "_detect_uploader_backend", lambda: "biliup_cli")
    monkeypatch.setattr(uploader, "_biliup_check_login_async", lambda: uploader.asyncio.sleep(0, result=True))

    append_results = [(False, True), (True, False)]
    append_calls = []

    async def fake_append_async(**kwargs):
        append_calls.append(kwargs)
        return append_results.pop(0)

    monkeypatch.setattr(uploader, "_biliup_append_video_entry_with_status_async", fake_append_async)

    async def fake_upload_async(**kwargs):
        raise AssertionError("create upload should not be called in this test")

    monkeypatch.setattr(uploader, "_biliup_upload_video_entry_async", fake_upload_async)

    sleep_calls = []

    async def fake_sleep(seconds: float, result=None):
        sleep_calls.append(seconds)
        return result

    monkeypatch.setattr(uploader.asyncio, "sleep", fake_sleep)

    monkeypatch.setattr(config_module, "SKIP_VIDEO_ENCODING", False)
    monkeypatch.setattr(config_module, "API_ENABLED", False)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", False)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(config_module, "DEFAULT_STREAMER_NAME", "洞主")
    monkeypatch.setattr(config_module, "STREAM_START_TIME_ADJUSTMENT", 10)
    monkeypatch.setattr(config_module, "BILIUP_RATE_LIMIT_COOLDOWN_SECONDS", 123)
    monkeypatch.setattr(config_module, "BILIUP_RATE_LIMIT_APPEND_MAX_RETRIES", 1)

    file_time = datetime(2026, 2, 24, 10, 0, 0)
    video_path = tmp_path / f"洞主录播{file_time.strftime('%Y-%m-%dT%H_%M_%S')}.mp4"
    video_path.write_text("x", encoding="utf-8")

    session = StreamSession(
        streamer_name="洞主",
        start_time=file_time - timedelta(hours=1),
        end_time=file_time + timedelta(hours=1),
    )
    existing = UploadedVideo(
        bvid="BV1y9fsBbEma",
        title="main",
        first_part_filename="already.mp4",
        upload_time=file_time - timedelta(minutes=10),
    )

    async with session_local() as db:
        db.add_all([session, existing])
        await db.commit()

        await uploader.upload_to_bilibili(db)

        result = await db.execute(
            uploader.select(UploadedVideo).filter(
                UploadedVideo.first_part_filename == video_path.name
            )
        )
        inserted = result.scalars().first()

    assert len(append_calls) == 2
    assert 123 in sleep_calls
    assert inserted is not None


def test_handle_uploaded_file_after_success_defers_delete_when_delay_enabled(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    file_path = tmp_path / "video.mp4"
    file_path.write_text("x", encoding="utf-8")

    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES_DELAY_HOURS", 24)

    uploader._handle_uploaded_file_after_success(str(file_path), file_path.name)

    assert file_path.exists()


@pytest.mark.asyncio
async def test_cleanup_delayed_uploaded_files_deletes_only_expired_records(tmp_path: Path, monkeypatch):
    import uploader
    import config as config_module

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES", True)
    monkeypatch.setattr(config_module, "DELETE_UPLOADED_FILES_DELAY_HOURS", 1)
    monkeypatch.setattr(config_module, "UPLOAD_FOLDER", str(tmp_path))

    old_file = tmp_path / "old.mp4"
    new_file = tmp_path / "new.mp4"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    now = datetime.now()
    old_record = UploadedVideo(
        bvid="BV1OLD0000000A",
        title="old",
        first_part_filename="old.mp4",
        upload_time=now - timedelta(hours=3),
        created_at=now - timedelta(hours=2),
    )
    new_record = UploadedVideo(
        bvid="BV1NEW0000000B",
        title="new",
        first_part_filename="new.mp4",
        upload_time=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=10),
    )

    async with session_local() as db:
        db.add_all([old_record, new_record])
        await db.commit()

        await uploader.cleanup_delayed_uploaded_files(db)

    assert not old_file.exists()
    assert new_file.exists()
