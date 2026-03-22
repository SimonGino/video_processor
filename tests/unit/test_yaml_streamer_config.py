"""Tests for config.yaml loading and STREAMERS derivation."""
import pytest
from pathlib import Path

from douyu2bilibili import config as config_module


def test_load_yaml_config_populates_streamers(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
streamers:
  洞主:
    room_id: "138243"
    upload:
      title: "洞主直播录像{time}弹幕版"
      tid: 171
      tag: "洞主,直播录像"
      desc: "测试简介"
      source: "https://www.douyu.com/138243"
      cover: ""
      dynamic: ""
  银剑君:
    room_id: "999999"
    upload:
      title: "银剑君直播录像{time}"
      tid: 171
      tag: "银剑君,直播录像"
      desc: "银剑君简介"
      source: "https://www.douyu.com/999999"

upload:
  max_concurrent: 2
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()

    assert result is True
    assert len(config_module.STREAMERS) == 2
    assert config_module.STREAMERS[0]["name"] == "洞主"
    assert config_module.STREAMERS[0]["room_id"] == "138243"
    assert config_module.STREAMERS[1]["name"] == "银剑君"
    assert config_module.STREAMERS[1]["room_id"] == "999999"

    assert "洞主" in uploader.streamer_configs
    assert uploader.streamer_configs["洞主"]["title"] == "洞主直播录像{time}弹幕版"
    assert "银剑君" in uploader.streamer_configs
    assert uploader.streamer_configs["银剑君"]["cover"] == ""  # default

    assert uploader.upload_global_config.get("max_concurrent") == 2


def test_load_yaml_config_fails_on_missing_room_id(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
streamers:
  洞主:
    upload:
      title: "test{time}"
      tid: 171
      tag: "t"
      desc: "d"
      source: "s"
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()
    assert result is False


def test_load_yaml_config_fails_on_missing_upload_fields(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
streamers:
  洞主:
    room_id: "138243"
    upload:
      title: "test{time}"
      # missing tid, tag, desc, source
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()
    assert result is False


def test_load_yaml_config_fails_on_missing_streamers(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
upload:
  max_concurrent: 1
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()
    assert result is False


def test_single_streamer_backward_compatible(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
streamers:
  洞主:
    room_id: "138243"
    upload:
      title: "洞主直播录像{time}弹幕版"
      tid: 171
      tag: "洞主,凯哥,直播录像,游戏实况"
      desc: "简介"
      source: "https://www.douyu.com/138243"
      cover: ""
      dynamic: ""
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()

    assert result is True
    assert len(config_module.STREAMERS) == 1
    assert config_module.STREAMERS[0] == {"name": "洞主", "room_id": "138243"}


def test_danmaku_tag_placeholder_in_title(tmp_path: Path, monkeypatch):
    from douyu2bilibili import uploader

    yaml_content = """\
streamers:
  洞主:
    room_id: "138243"
    upload:
      title: "洞主直播录像{time}{danmaku_tag}"
      tid: 171
      tag: "洞主,直播录像"
      desc: "测试简介"
      source: "https://www.douyu.com/138243"

upload:
  max_concurrent: 1
"""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(config_module, "YAML_CONFIG_PATH", str(yaml_file))

    result = uploader.load_yaml_config()

    assert result is True
    assert "{danmaku_tag}" in uploader.streamer_configs["洞主"]["title"]
