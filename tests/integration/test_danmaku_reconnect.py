import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aiohttp import web

from recording.stt_codec import pack


async def _start_server(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


@pytest.mark.asyncio
async def test_reconnect_continues_collecting(tmp_path: Path):
    """WebSocket disconnects mid-session; reconnect succeeds and danmaku continues."""
    from recording.danmaku_collector import DouyuDanmakuCollector

    connection_count = 0

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_count
        connection_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        if connection_count == 1:
            # First connection: send one message then close abruptly
            await ws.send_bytes(pack("type@=chatmsg/nn@=u1/txt@=msg1/"))
            await ws.close()
        else:
            # Second connection: send another message then stay open
            await ws.send_bytes(pack("type@=chatmsg/nn@=u2/txt@=msg2/"))
            # Keep alive until client disconnects (duration expires)
            async for _ in ws:
                pass
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner, port = await _start_server(app)

    out = tmp_path / "reconnect.xml.part"
    try:
        collector = DouyuDanmakuCollector(
            ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=60
        )
        count = await collector.collect(
            room_id="1234",
            output_path=str(out),
            duration_seconds=3,
            max_reconnects=3,
            reconnect_base_delay=0,
        )
    finally:
        await runner.cleanup()

    assert count == 2
    assert connection_count == 2
    content = out.read_text(encoding="utf-8", errors="ignore")
    assert "msg1" in content
    assert "msg2" in content

    # Verify offset continuity: both offsets should be relative to original start
    tree = ET.parse(out)
    offsets = [float(d.get("p", "").split(",")[0]) for d in tree.findall(".//d")]
    assert len(offsets) == 2
    assert offsets[0] <= offsets[1], "offsets should be monotonically non-decreasing"


@pytest.mark.asyncio
async def test_max_reconnects_reached(tmp_path: Path):
    """After max_reconnects, collection stops and returns collected count."""
    from recording.danmaku_collector import DouyuDanmakuCollector

    connection_count = 0

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_count
        connection_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Send one message then close on every connection
        await ws.send_bytes(pack("type@=chatmsg/nn@=u1/txt@=hello/"))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner, port = await _start_server(app)

    out = tmp_path / "maxretry.xml.part"
    try:
        collector = DouyuDanmakuCollector(
            ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=60
        )
        count = await collector.collect(
            room_id="1234",
            output_path=str(out),
            duration_seconds=30,
            max_reconnects=2,
            reconnect_base_delay=0,
        )
    finally:
        await runner.cleanup()

    # initial + 2 reconnects = 3 connections
    assert connection_count == 3
    assert count == 3


@pytest.mark.asyncio
async def test_no_reconnect_when_disabled(tmp_path: Path):
    """max_reconnects=0 means no reconnect, same as original behavior."""
    from recording.danmaku_collector import DouyuDanmakuCollector

    connection_count = 0

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_count
        connection_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(pack("type@=chatmsg/nn@=u1/txt@=hello/"))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner, port = await _start_server(app)

    out = tmp_path / "noreconnect.xml.part"
    try:
        collector = DouyuDanmakuCollector(
            ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=60
        )
        count = await collector.collect(
            room_id="1234",
            output_path=str(out),
            duration_seconds=5,
            max_reconnects=0,
            reconnect_base_delay=0,
        )
    finally:
        await runner.cleanup()

    assert connection_count == 1
    assert count == 1


@pytest.mark.asyncio
async def test_backoff_exceeds_remaining_time(tmp_path: Path):
    """When backoff delay exceeds remaining recording time, stop immediately."""
    from recording.danmaku_collector import DouyuDanmakuCollector

    connection_count = 0

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_count
        connection_count += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(pack("type@=chatmsg/nn@=u1/txt@=hello/"))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner, port = await _start_server(app)

    out = tmp_path / "backoff.xml.part"
    try:
        collector = DouyuDanmakuCollector(
            ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=60
        )
        count = await collector.collect(
            room_id="1234",
            output_path=str(out),
            duration_seconds=2,
            max_reconnects=5,
            # base_delay=100 → first backoff = 100s >> 2s remaining
            reconnect_base_delay=100,
        )
    finally:
        await runner.cleanup()

    # Only the initial connection; backoff too long to reconnect
    assert connection_count == 1
    assert count == 1


@pytest.mark.asyncio
async def test_transient_reconnect_failure_retries(tmp_path: Path):
    """A failed reconnect attempt consumes budget but tries again on next attempt."""
    from recording.danmaku_collector import DouyuDanmakuCollector

    connection_count = 0
    server_reject_next = False

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal connection_count, server_reject_next
        connection_count += 1

        if server_reject_next:
            # Reject this connection to simulate transient failure
            server_reject_next = False
            raise web.HTTPServiceUnavailable()

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(pack(f"type@=chatmsg/nn@=u1/txt@=msg{connection_count}/"))

        if connection_count == 1:
            # First connection: close to trigger reconnect
            server_reject_next = True  # next attempt will fail
            await ws.close()
        else:
            # Final connection: stay open
            async for _ in ws:
                pass
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    runner, port = await _start_server(app)

    out = tmp_path / "transient.xml.part"
    try:
        collector = DouyuDanmakuCollector(
            ws_url=f"ws://127.0.0.1:{port}/ws", heartbeat_seconds=60
        )
        count = await collector.collect(
            room_id="1234",
            output_path=str(out),
            duration_seconds=5,
            max_reconnects=3,
            reconnect_base_delay=0,
        )
    finally:
        await runner.cleanup()

    # initial connect + 1 rejected + 1 successful = used 2 of 3 reconnect attempts
    assert count == 2
    content = out.read_text(encoding="utf-8", errors="ignore")
    assert "msg1" in content
