# Title Placeholder & Skip Danmaku Collection

## Problem

1. **Title contradiction**: When `SKIP_VIDEO_ENCODING=True`, the uploaded video title becomes `洞主直播录像2026年03月22日弹幕版 【无弹幕版】` — the template hardcodes "弹幕版" and the code appends "【无弹幕版】", resulting in a contradictory title.

2. **Unnecessary danmaku XML files**: When `SKIP_VIDEO_ENCODING=True`, the recording service still collects danmaku and generates XML files. These XMLs are never processed (danmaku conversion is skipped) and accumulate in `data/processing/` with no cleanup logic.

## Solution

### 1. Title Placeholder (`{danmaku_tag}`)

**config.yaml**: Replace hardcoded "弹幕版" with `{danmaku_tag}` placeholder in all streamer title templates.

```yaml
# Before
title: "洞主直播录像{time}弹幕版"
# After
title: "洞主直播录像{time}{danmaku_tag}"
```

**config.py**: Change suffix constants to plain text (remove brackets):
- `DANMAKU_TITLE_SUFFIX = "弹幕版"`
- `NO_DANMAKU_TITLE_SUFFIX = "无弹幕版"`

**uploader.py**:
- Remove the `title_suffix` variable assignment (~line 568-576) and the suffix-appending logic at both line ~921-922 and ~925-926 (normal path and error path).
- In the title generation block (~line 913-926), after `{time}` replacement, add `{danmaku_tag}` replacement: `title = title.replace('{danmaku_tag}', danmaku_tag)` where `danmaku_tag` is resolved from `config.SKIP_VIDEO_ENCODING`. Apply the same replacement in the error fallback path (~line 924-926).
- Simplify part title logic (~line 830-832): remove the `title_suffix` conditional branch. Part titles become `f"P{part_number} {part_time_str}"` uniformly (main title already carries the danmaku tag).

### 2. Skip Danmaku Collection

**recording_service.py**: When `config.SKIP_VIDEO_ENCODING=True`, pass `xml_part_path=None` to `run_one_segment`:

```python
xml_part_path = None if config.SKIP_VIDEO_ENCODING else f"{config.PROCESSING_FOLDER}/{base}.xml.part"
```

**segment_pipeline.py**:
- Change `xml_part_path` parameter type to `str | None`, default `None`.
- When `xml_part_path` is `None`:
  - Skip `xml_part.parent.mkdir()` call
  - Do not create `DouyuDanmakuCollector` or its async task
  - `asyncio.gather` only awaits the record task
  - Skip XML finalization (`xml_part.replace(xml_final)`)
- No changes to `DanmakuCollector` itself.

## Files Changed

| File | Change |
|------|--------|
| `config.yaml` | Replace "弹幕版" with `{danmaku_tag}` in both streamer templates |
| `src/douyu2bilibili/config.py` | Update suffix constants (remove brackets) |
| `src/douyu2bilibili/uploader.py` | Handle `{danmaku_tag}` placeholder, remove suffix-appending logic |
| `src/douyu2bilibili/recording/recording_service.py` | Conditionally pass `xml_part_path=None` |
| `src/douyu2bilibili/recording/segment_pipeline.py` | Handle `xml_part_path=None` (skip danmaku collection) |

## Not In Scope

- Cleanup of existing orphan XML files in `data/processing/` (manual cleanup by user)
- New `DANMAKU_ENABLED` config flag (unnecessary complexity for current needs)
