import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aiohttp import web

from recording.stt_codec import pack


@pytest.mark.asyncio
async def test_danmaku_collector_writes_xml(tmp_path: Path):
    from recording.danmaku_collector import DouyuDanmakuCollector

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

    out = tmp_path / "a.xml.part"
    try:
        collector = DouyuDanmakuCollector(ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=1)
        count = await collector.collect(room_id="1234", output_path=str(out), duration_seconds=2)
        assert count == 1
    finally:
        await runner.cleanup()

    assert out.exists()
    assert "hello" in out.read_text(encoding="utf-8", errors="ignore")
    ET.parse(out)

