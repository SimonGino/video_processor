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
