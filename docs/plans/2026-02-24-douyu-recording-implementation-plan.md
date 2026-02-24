# Douyu Recording (FLV + Bilibili XML) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add built-in Douyu recording to produce `*.flv` + Bilibili `*.xml` into `config.PROCESSING_FOLDER`, so the existing processing/upload pipeline works without changes.

**Architecture:** A standalone `recording_service.py` process monitors streamer live status, resolves stream URL via Douyu `getEncryption + getH5PlayV1`, records with `ffmpeg -c copy`, and collects danmaku via `aiohttp` WebSocket (STT) into Bilibili XML. Recording writes to `*.part` and renames atomically when a segment completes.

**Tech Stack:** Python 3.13 + `aiohttp` + `asyncio` subprocess + `ffmpeg`, plus `pytest` for tests.

---

### Task 1: Add test dependencies and skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/unit/.gitkeep`
- Create: `tests/integration/.gitkeep`

**Step 1: Write the failing test**

Create `tests/unit/test_sanity.py`:

```python
def test_sanity():
    assert 1 + 1 == 2
```

**Step 2: Run test to verify it fails (pytest missing)**

Run: `uv run pytest -q`  
Expected: FAIL with "pytest: command not found" (or missing dependency).

**Step 3: Add dependencies**

Add to `pyproject.toml` dependencies:
- `pytest`
- `pytest-asyncio`

**Step 4: Run test to verify it passes**

Run: `uv sync`  
Run: `uv run pytest -q`  
Expected: PASS (1 passed).

**Step 5: Commit**

Run:
```bash
git add pyproject.toml uv.lock tests/unit/test_sanity.py tests/unit/.gitkeep tests/integration/.gitkeep
git commit -m "test: add pytest skeleton"
```

---

### Task 2: STT codec unit tests

**Files:**
- Create: `tests/unit/test_stt_codec.py`

**Step 1: Write the failing test**

```python
from recording.stt_codec import pack, iter_payloads, escape, unescape


def test_escape_roundtrip():
    s = "a/@b@c/中文"
    assert unescape(escape(s)) == s


def test_pack_iter_payloads_roundtrip():
    payload = "type@=loginreq/roomid@=1/"
    frame = pack(payload)
    assert list(iter_payloads(frame)) == [payload]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_stt_codec.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'recording'` or missing functions.

---

### Task 3: Implement STT codec (minimal)

**Files:**
- Create: `recording/__init__.py`
- Create: `recording/stt_codec.py`

**Step 1: Write minimal implementation**

Implement:
- `escape(s: str) -> str` (`@`→`@A`, `/`→`@S`)
- `unescape(s: str) -> str`
- `pack(payload: str) -> bytes` (Douyu packet header + `payload + "\\x00"`)
- `iter_payloads(data: bytes) -> Iterator[str]` (supports concatenated packets)

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_stt_codec.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/__init__.py recording/stt_codec.py tests/unit/test_stt_codec.py
git commit -m "feat: add douyu stt codec"
```

---

### Task 4: Danmaku message parser unit tests

**Files:**
- Create: `tests/unit/test_douyu_message_parser.py`

**Step 1: Write the failing test**

```python
from recording.douyu_message_parser import parse_kv


def test_parse_chatmsg_minimal():
    s = "type@=chatmsg/nn@=u1/txt@=hello/"
    d = parse_kv(s)
    assert d["type"] == "chatmsg"
    assert d["nn"] == "u1"
    assert d["txt"] == "hello"


def test_parse_unescape():
    s = "type@=chatmsg/txt@=a@Sbc@Adef/"
    d = parse_kv(s)
    assert d["txt"] == "a/bc@def"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_douyu_message_parser.py -q`  
Expected: FAIL (module missing).

---

### Task 5: Implement message parser

**Files:**
- Create: `recording/douyu_message_parser.py`

**Step 1: Minimal implementation**

Implement `parse_kv(payload: str) -> dict[str, str]`:
- split by `/`
- split each token by `@=`
- apply `stt_codec.unescape()` to values

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_douyu_message_parser.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/douyu_message_parser.py tests/unit/test_douyu_message_parser.py
git commit -m "feat: add douyu danmaku kv parser"
```

---

### Task 6: Bilibili XML writer unit tests + dmconvert contract

**Files:**
- Create: `tests/unit/test_xml_writer.py`
- Create: `tests/unit/test_dmconvert_contract.py`

**Step 1: Write failing tests**

`test_xml_writer.py`:
```python
import xml.etree.ElementTree as ET
from pathlib import Path

from recording.xml_writer import BilibiliXmlWriter


def test_xml_is_parseable(tmp_path: Path):
    out = tmp_path / "a.xml"
    w = BilibiliXmlWriter(out)
    w.open()
    w.write_danmaku(1.23, "a & <b>")
    w.close()
    ET.parse(out)
```

`test_dmconvert_contract.py`:
```python
from pathlib import Path

from dmconvert import convert_xml_to_ass
from recording.xml_writer import BilibiliXmlWriter


def test_dmconvert_can_convert(tmp_path: Path):
    xml_path = tmp_path / "a.xml"
    ass_path = tmp_path / "a.ass"
    w = BilibiliXmlWriter(xml_path)
    w.open()
    w.write_danmaku(1.0, "hello")
    w.close()

    convert_xml_to_ass(
        font_size=40,
        sc_font_size=38,
        resolution_x=1920,
        resolution_y=1080,
        xml_file=str(xml_path),
        ass_file=str(ass_path),
    )
    assert ass_path.exists()
    assert "hello" in ass_path.read_text(encoding="utf-8", errors="ignore")
```

**Step 2: Run to verify fails**

Run: `uv run pytest tests/unit/test_xml_writer.py -q`  
Expected: FAIL (module missing).

---

### Task 7: Implement XML writer

**Files:**
- Create: `recording/xml_writer.py`

**Step 1: Minimal implementation**

Implement:
- `open()`, `write_danmaku(offset_seconds, text, ...)`, `close()`
- always write valid XML with `<i>` root and `<d p="...">...</d>`
- escape XML text correctly

**Step 2: Run tests**

Run: `uv run pytest tests/unit/test_xml_writer.py tests/unit/test_dmconvert_contract.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/xml_writer.py tests/unit/test_xml_writer.py tests/unit/test_dmconvert_contract.py
git commit -m "feat: add bilibili xml writer"
```

---

### Task 8: Douyu H5PlayV1 resolver integration test (HTTP stub)

**Files:**
- Create: `tests/integration/test_douyu_stream_resolver_http_stub.py`

**Step 1: Write failing integration test**

Use `aiohttp.web` to stub:
- `/wgapi/livenc/liveweb/websec/getEncryption`
- `/lapi/live/getH5PlayV1/{rid}`

Assert `resolve_stream_url()` returns `rtmp_url/rtmp_live`.

**Step 2: Run to verify fails**

Run: `uv run pytest tests/integration/test_douyu_stream_resolver_http_stub.py -q`  
Expected: FAIL (resolver missing).

---

### Task 9: Implement Douyu resolver

**Files:**
- Create: `recording/douyu_stream_resolver.py`

**Step 1: Minimal implementation**

Implement `DouyuH5PlayResolver`:
- `async resolve_stream_url(room_id: str) -> tuple[str, dict[str, str]]` (url + headers for ffmpeg)
- cache key material from `getEncryption` with a 24h soft-expire
- compute `auth` by md5-iter
- call `getH5PlayV1` (POST with params)

**Step 2: Run tests**

Run: `uv run pytest tests/integration/test_douyu_stream_resolver_http_stub.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/douyu_stream_resolver.py tests/integration/test_douyu_stream_resolver_http_stub.py
git commit -m "feat: add douyu h5playv1 stream resolver"
```

---

### Task 10: FFmpeg recorder integration test (stub binary)

**Files:**
- Create: `tests/bin/ffmpeg`
- Create: `tests/integration/test_ffmpeg_recorder_stub.py`

**Step 1: Write failing integration test**

Use env var `FFMPEG_PATH` to point to `tests/bin/ffmpeg`. Stub should:
- create output file and write some bytes
- exit 0

Test `FfmpegRecorder.record(...)` creates `*.flv.part`.

**Step 2: Run to verify fails**

Run: `uv run pytest tests/integration/test_ffmpeg_recorder_stub.py -q`  
Expected: FAIL (recorder missing).

---

### Task 11: Implement FFmpeg recorder

**Files:**
- Create: `recording/ffmpeg_recorder.py`

**Step 1: Minimal implementation**

Implement `FfmpegRecorder.record(...)` using `asyncio.create_subprocess_exec()`:
- supports duration seconds (`-t`)
- supports custom HTTP headers

**Step 2: Run tests**

Run: `uv run pytest tests/integration/test_ffmpeg_recorder_stub.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/ffmpeg_recorder.py tests/bin/ffmpeg tests/integration/test_ffmpeg_recorder_stub.py
git commit -m "feat: add ffmpeg recorder wrapper"
```

---

### Task 12: Danmaku collector integration test (local WS)

**Files:**
- Create: `tests/integration/test_danmaku_collector_ws.py`

**Step 1: Write failing integration test**

Start local `aiohttp.web` WS server, send one packed `chatmsg` packet, assert collector writes XML containing the text.

**Step 2: Run to verify fails**

Run: `uv run pytest tests/integration/test_danmaku_collector_ws.py -q`  
Expected: FAIL (collector missing).

---

### Task 13: Implement Danmaku collector

**Files:**
- Create: `recording/danmaku_collector.py`

**Step 1: Minimal implementation**

Implement `DouyuDanmakuCollector.collect(...)`:
- connect to ws url
- send loginreq + joingroup + keeplive
- parse incoming messages (STT → kv dict → chatmsg)
- write to `BilibiliXmlWriter`

**Step 2: Run tests**

Run: `uv run pytest tests/integration/test_danmaku_collector_ws.py -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/danmaku_collector.py tests/integration/test_danmaku_collector_ws.py
git commit -m "feat: add douyu danmaku collector"
```

---

### Task 14: Segment pipeline integration test (offline)

**Files:**
- Create: `tests/integration/test_segment_pipeline_offline.py`

**Step 1: Write failing test**

Wire together:
- resolver stub (return fixed URL)
- recorder stub (writes flv.part)
- WS stub (sends chatmsg)

Assert final rename removes `.part` and produces `.flv` + `.xml`.

**Step 2: Run to verify fails**

Run: `uv run pytest tests/integration/test_segment_pipeline_offline.py -q`

---

### Task 15: Implement segment coordinator + recording service entrypoint

**Files:**
- Create: `recording/segment_pipeline.py`
- Create: `recording/recording_service.py`
- Create: `recording_service.py`
- Modify: `config.py`
- (Optional) Modify: `service.sh`

**Step 1: Minimal implementation**

- `segment_pipeline.run_one_segment(...)` orchestrates ffmpeg + danmaku and renames atomically.
- `recording/recording_service.py` runs per-streamer loop: offline poll → resolve url → record segment → retry on failure.
- `recording_service.py` as root entrypoint: `uv run python recording_service.py`.
- Add recording-related config defaults.

**Step 2: Run tests**

Run: `uv run pytest -q`  
Expected: PASS.

**Step 3: Commit**

```bash
git add recording/segment_pipeline.py recording/recording_service.py recording_service.py config.py tests/integration/test_segment_pipeline_offline.py service.sh
git commit -m "feat: add douyu recording service"
```

---

### Task 16: E2E checklist doc

**Files:**
- Create: `tests/e2e/README.md`

**Steps:**
- Document how to run `recording_service.py` with a short segment time (e.g. 1 minute)
- Verify files appear in `PROCESSING_FOLDER`

**Commit**

```bash
git add tests/e2e/README.md
git commit -m "docs: add recording e2e checklist"
```

