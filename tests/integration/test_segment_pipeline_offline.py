import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aiohttp import web

from recording.stt_codec import pack


@pytest.mark.asyncio
async def test_segment_pipeline_offline(tmp_path: Path):
    from recording.segment_pipeline import run_one_segment

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(pack("type@=chatmsg/nn@=u1/txt@=hello/"))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    ffmpeg_stub = Path(__file__).resolve().parents[1] / "bin" / "ffmpeg"

    flv_part = tmp_path / "seg.flv.part"
    xml_part = tmp_path / "seg.xml.part"

    try:
        await run_one_segment(
            room_id="1234",
            stream_url="https://example.invalid/live.flv",
            stream_headers={"User-Agent": "ua", "Referer": "https://www.douyu.com"},
            flv_part_path=str(flv_part),
            xml_part_path=str(xml_part),
            duration_seconds=1,
            ffmpeg_path=str(ffmpeg_stub),
            ws_url=f"ws://127.0.0.1:{port}/ws",
        )
    finally:
        await runner.cleanup()

    assert not flv_part.exists()
    assert not xml_part.exists()

    flv = tmp_path / "seg.flv"
    xml = tmp_path / "seg.xml"
    assert flv.exists()
    assert xml.exists()
    assert "hello" in xml.read_text(encoding="utf-8", errors="ignore")
    ET.parse(xml)

