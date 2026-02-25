from pathlib import Path
from types import SimpleNamespace


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
