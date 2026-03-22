# Title Placeholder & Skip Danmaku Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix contradictory upload title ("弹幕版 【无弹幕版】") by using a `{danmaku_tag}` placeholder, and stop recording danmaku XML files when video encoding is skipped.

**Architecture:** Two independent changes: (1) Replace hardcoded "弹幕版" in config.yaml with `{danmaku_tag}` placeholder, resolved at upload time by `SKIP_VIDEO_ENCODING` flag; (2) Conditionally skip `DanmakuCollector` in the recording pipeline when `SKIP_VIDEO_ENCODING=True`.

**Tech Stack:** Python 3.13, pytest, pytest-asyncio (auto mode)

**Spec:** `docs/superpowers/specs/2026-03-22-title-placeholder-and-skip-danmaku-design.md`

---

### Task 1: Update config constants and config.yaml

**Files:**
- Modify: `src/douyu2bilibili/config.py:52-55`
- Modify: `config.yaml:6,20`

- [ ] **Step 1: Update config.py suffix constants**

Remove brackets from the two suffix constants:

```python
# Before
NO_DANMAKU_TITLE_SUFFIX = "【无弹幕版】"
DANMAKU_TITLE_SUFFIX = "【弹幕版】"

# After
NO_DANMAKU_TITLE_SUFFIX = "无弹幕版"
DANMAKU_TITLE_SUFFIX = "弹幕版"
```

Also update the comments on lines 52-55 to reflect that these are now placeholder replacement values, not suffixes:

```python
# {danmaku_tag} placeholder value when skipping encoding
NO_DANMAKU_TITLE_SUFFIX = "无弹幕版"
# {danmaku_tag} placeholder value when encoding with danmaku
DANMAKU_TITLE_SUFFIX = "弹幕版"
```

- [ ] **Step 2: Update config.yaml title templates**

Replace hardcoded "弹幕版" with `{danmaku_tag}` in both streamer title templates:

```yaml
# 洞主 (line 6)
# Before
title: "洞主直播录像{time}弹幕版"
# After
title: "洞主直播录像{time}{danmaku_tag}"

# 银剑君 (line 20)
# Before
title: "银剑君直播录像{time}弹幕版"
# After
title: "银剑君直播录像{time}{danmaku_tag}"
```

- [ ] **Step 3: Commit**

```bash
git add src/douyu2bilibili/config.py config.yaml
git commit -m "refactor: replace hardcoded danmaku text with {danmaku_tag} placeholder"
```

---

### Task 2: Update uploader.py title generation logic

**Files:**
- Modify: `src/douyu2bilibili/uploader.py:568-576,830-832,913-926`
- Test: `tests/unit/test_yaml_streamer_config.py`

- [ ] **Step 1: Write failing test for `{danmaku_tag}` replacement**

Add a test to `tests/unit/test_yaml_streamer_config.py` that verifies the `{danmaku_tag}` placeholder is present in the loaded config:

```python
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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_yaml_streamer_config.py::test_danmaku_tag_placeholder_in_title -v`
Expected: PASS (this is a config loading test, no code changes needed)

- [ ] **Step 3: Remove `title_suffix` variable and suffix-appending logic in uploader.py**

In `upload_to_bilibili()` (~line 568-576), remove the `title_suffix` assignment:

```python
# Before (lines 568-576)
    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        logger.info("检测到 SKIP_VIDEO_ENCODING=True 配置，将寻找并上传 FLV 文件")
        video_extension = "flv"
        title_suffix = config.NO_DANMAKU_TITLE_SUFFIX
    else:
        logger.info("将寻找并上传压制后的 MP4 文件")
        video_extension = "mp4"
        title_suffix = config.DANMAKU_TITLE_SUFFIX

# After
    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        logger.info("检测到 SKIP_VIDEO_ENCODING=True 配置，将寻找并上传 FLV 文件")
        video_extension = "flv"
    else:
        logger.info("将寻找并上传压制后的 MP4 文件")
        video_extension = "mp4"
    danmaku_tag = config.NO_DANMAKU_TITLE_SUFFIX if is_skip_encoding else config.DANMAKU_TITLE_SUFFIX
```

- [ ] **Step 4: Replace suffix-appending with `{danmaku_tag}` replacement in title generation**

In the title generation block (~line 913-926):

```python
# Before (lines 913-926)
                title = title_template
                try:
                    video_time = first_video_info['timestamp']
                    formatted_time = video_time.strftime('%Y年%m月%d日')
                    if '{time}' in title_template:
                        title = title_template.replace('{time}', formatted_time)
                    elif len(videos) > 1:
                        title = f"{title_template} (合集 {video_time.strftime('%Y-%m-%d')})"
                    if is_skip_encoding:
                        title = f"{title} {title_suffix}"
                except Exception as e:
                    logger.warning(f"生成标题时出错: {e}，使用默认标题: {title}")
                    if is_skip_encoding:
                        title = f"{title} {title_suffix}"

# After
                title = title_template
                try:
                    video_time = first_video_info['timestamp']
                    formatted_time = video_time.strftime('%Y年%m月%d日')
                    if '{time}' in title_template:
                        title = title_template.replace('{time}', formatted_time)
                    elif len(videos) > 1:
                        title = f"{title_template} (合集 {video_time.strftime('%Y-%m-%d')})"
                except Exception as e:
                    logger.warning(f"生成标题时出错: {e}，使用默认标题: {title}")
                title = title.replace('{danmaku_tag}', danmaku_tag)
```

- [ ] **Step 5: Simplify part title logic**

In the part title block (~line 827-832):

```python
# Before
                        try:
                            video_time = video_info['timestamp']
                            part_time_str = video_time.strftime('%H:%M:%S')
                            part_title = f"P{part_number} {part_time_str} {title_suffix}" if is_skip_encoding else f"P{part_number} {part_time_str}"
                        except Exception:
                            part_title = f"P{part_number} {title_suffix}" if is_skip_encoding else f"P{part_number}"

# After
                        try:
                            video_time = video_info['timestamp']
                            part_time_str = video_time.strftime('%H:%M:%S')
                            part_title = f"P{part_number} {part_time_str}"
                        except Exception:
                            part_title = f"P{part_number}"
```

- [ ] **Step 6: Run existing tests**

Run: `uv run pytest tests/unit/test_yaml_streamer_config.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/douyu2bilibili/uploader.py tests/unit/test_yaml_streamer_config.py
git commit -m "feat: use {danmaku_tag} placeholder for upload title instead of appending suffix"
```

---

### Task 3: Skip danmaku collection when encoding is skipped

**Files:**
- Modify: `src/douyu2bilibili/recording/segment_pipeline.py:20-78`
- Modify: `src/douyu2bilibili/recording/recording_service.py:88-89`
- Test: `tests/integration/test_segment_pipeline_offline.py`

- [ ] **Step 1: Write failing test for xml_part_path=None**

Add a test to `tests/integration/test_segment_pipeline_offline.py`:

```python
@pytest.mark.asyncio
async def test_segment_pipeline_skip_danmaku(tmp_path: Path):
    """When xml_part_path is None, danmaku collection is skipped."""
    from douyu2bilibili.recording.segment_pipeline import run_one_segment

    ffmpeg_stub = Path(__file__).resolve().parents[1] / "bin" / "ffmpeg"
    flv_part = tmp_path / "seg.flv.part"

    rc = await run_one_segment(
        room_id="1234",
        stream_url="https://example.invalid/live.flv",
        stream_headers={"User-Agent": "ua", "Referer": "https://www.douyu.com"},
        flv_part_path=str(flv_part),
        xml_part_path=None,
        duration_seconds=1,
        ffmpeg_path=str(ffmpeg_stub),
        ws_url="ws://127.0.0.1:1/unused",
    )

    # FLV should be finalized
    flv = tmp_path / "seg.flv"
    assert flv.exists()

    # No XML files should exist at all
    xml_files = list(tmp_path.glob("*.xml*"))
    assert xml_files == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_segment_pipeline_offline.py::test_segment_pipeline_skip_danmaku -v`
Expected: FAIL (current signature doesn't accept `None` for `xml_part_path`)

- [ ] **Step 3: Update segment_pipeline.py to handle xml_part_path=None**

```python
# Before (lines 20-78)
async def run_one_segment(
    *,
    room_id: str,
    stream_url: str,
    stream_headers: Mapping[str, str],
    flv_part_path: str,
    xml_part_path: str,
    duration_seconds: int,
    ffmpeg_path: str,
    ws_url: str,
    danmaku_heartbeat_seconds: int = 30,
    danmaku_ws_max_reconnects: int = 0,
    danmaku_ws_reconnect_base_delay: int = 2,
) -> int:
    flv_part = Path(flv_part_path)
    xml_part = Path(xml_part_path)
    flv_part.parent.mkdir(parents=True, exist_ok=True)
    xml_part.parent.mkdir(parents=True, exist_ok=True)

    recorder = FfmpegRecorder(ffmpeg_path=ffmpeg_path)
    collector = DouyuDanmakuCollector(ws_url=ws_url, heartbeat_seconds=danmaku_heartbeat_seconds)

    record_task = asyncio.create_task(
        recorder.record(
            url=stream_url,
            output_path=str(flv_part),
            duration_seconds=duration_seconds,
            headers=stream_headers,
        )
    )
    danmaku_task = asyncio.create_task(
        collector.collect(
            room_id=room_id,
            output_path=str(xml_part),
            duration_seconds=duration_seconds,
            max_reconnects=danmaku_ws_max_reconnects,
            reconnect_base_delay=danmaku_ws_reconnect_base_delay,
        )
    )

    record_result, danmaku_result = await asyncio.gather(
        record_task,
        danmaku_task,
        return_exceptions=True,
    )
    if isinstance(record_result, Exception):
        raise record_result
    if isinstance(danmaku_result, Exception):
        logger.warning("Danmaku collection failed: %s", danmaku_result)

    flv_final = _finalize_part_path(flv_part)
    xml_final = _finalize_part_path(xml_part)

    if flv_part.exists():
        flv_part.replace(flv_final)
    if xml_part.exists():
        xml_part.replace(xml_final)

    return int(record_result)

# After
async def run_one_segment(
    *,
    room_id: str,
    stream_url: str,
    stream_headers: Mapping[str, str],
    flv_part_path: str,
    xml_part_path: str | None = None,
    duration_seconds: int,
    ffmpeg_path: str,
    ws_url: str,
    danmaku_heartbeat_seconds: int = 30,
    danmaku_ws_max_reconnects: int = 0,
    danmaku_ws_reconnect_base_delay: int = 2,
) -> int:
    flv_part = Path(flv_part_path)
    flv_part.parent.mkdir(parents=True, exist_ok=True)

    recorder = FfmpegRecorder(ffmpeg_path=ffmpeg_path)

    record_task = asyncio.create_task(
        recorder.record(
            url=stream_url,
            output_path=str(flv_part),
            duration_seconds=duration_seconds,
            headers=stream_headers,
        )
    )

    danmaku_task = None
    if xml_part_path is not None:
        xml_part = Path(xml_part_path)
        xml_part.parent.mkdir(parents=True, exist_ok=True)
        collector = DouyuDanmakuCollector(ws_url=ws_url, heartbeat_seconds=danmaku_heartbeat_seconds)
        danmaku_task = asyncio.create_task(
            collector.collect(
                room_id=room_id,
                output_path=str(xml_part),
                duration_seconds=duration_seconds,
                max_reconnects=danmaku_ws_max_reconnects,
                reconnect_base_delay=danmaku_ws_reconnect_base_delay,
            )
        )

    if danmaku_task is not None:
        record_result, danmaku_result = await asyncio.gather(
            record_task, danmaku_task, return_exceptions=True,
        )
        if isinstance(danmaku_result, Exception):
            logger.warning("Danmaku collection failed: %s", danmaku_result)
    else:
        record_result = await record_task

    if isinstance(record_result, Exception):
        raise record_result

    flv_final = _finalize_part_path(flv_part)
    if flv_part.exists():
        flv_part.replace(flv_final)

    if xml_part_path is not None:
        xml_part = Path(xml_part_path)
        xml_final = _finalize_part_path(xml_part)
        if xml_part.exists():
            xml_part.replace(xml_final)

    return int(record_result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_segment_pipeline_offline.py -v`
Expected: Both tests PASS (existing test and new test)

- [ ] **Step 5: Update recording_service.py to conditionally pass xml_part_path**

In `_run_streamer()` (~line 88-89):

```python
# Before
            flv_part_path = f"{config.PROCESSING_FOLDER}/{base}.flv.part"
            xml_part_path = f"{config.PROCESSING_FOLDER}/{base}.xml.part"

# After
            flv_part_path = f"{config.PROCESSING_FOLDER}/{base}.flv.part"
            xml_part_path = None if config.SKIP_VIDEO_ENCODING else f"{config.PROCESSING_FOLDER}/{base}.xml.part"
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/douyu2bilibili/recording/segment_pipeline.py src/douyu2bilibili/recording/recording_service.py tests/integration/test_segment_pipeline_offline.py
git commit -m "feat: skip danmaku collection when SKIP_VIDEO_ENCODING is enabled"
```
