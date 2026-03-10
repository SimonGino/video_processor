import asyncio
import contextlib
import logging
import ssl
import time

import aiohttp

from .douyu_message_parser import parse_kv
from .stt_codec import iter_payloads, pack
from .xml_writer import BilibiliXmlWriter


logger = logging.getLogger("danmaku_collector")

# Douyu col field → RGB int color mapping
_DOUYU_COLOR_MAP = {
    "1": 0xFF0000,   # 红
    "2": 0x1E87F0,   # 蓝
    "3": 0x7AC84B,   # 绿
    "4": 0xFF7F00,   # 橙
    "5": 0x9B39F4,   # 紫
    "6": 0xFF69B4,   # 粉
}


class DouyuDanmakuCollector:
    def __init__(
        self,
        *,
        ws_url: str = "wss://danmuproxy.douyu.com:8506/",
        heartbeat_seconds: int = 30,
    ) -> None:
        self._ws_url = ws_url
        self._heartbeat_seconds = int(heartbeat_seconds)

    async def collect(
        self,
        *,
        room_id: str,
        output_path: str,
        duration_seconds: int,
        max_reconnects: int = 0,
        reconnect_base_delay: int = 2,
    ) -> int:
        writer = BilibiliXmlWriter(output_path)
        writer.open()

        start = time.monotonic()
        end = start + float(duration_seconds)
        count = 0
        reconnect_attempt = 0

        try:
            async with aiohttp.ClientSession() as session:
                # --- initial connection (no retry on first failure) ---
                try:
                    ws = await self._connect_ws(session)
                except (aiohttp.ClientError, ssl.SSLError) as e:
                    logger.warning("Failed to connect douyu danmaku ws: %s", e)
                    return 0

                while True:
                    # --- send login / join & run message loop ---
                    try:
                        await ws.send_bytes(pack(f"type@=loginreq/roomid@={room_id}/"))
                        await ws.send_bytes(pack(f"type@=joingroup/rid@={room_id}/gid@=-9999/"))

                        heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                        try:
                            while True:
                                timeout = end - time.monotonic()
                                if timeout <= 0:
                                    break

                                try:
                                    msg = await ws.receive(timeout=timeout)
                                except asyncio.TimeoutError:
                                    break

                                if msg.type == aiohttp.WSMsgType.BINARY:
                                    for payload in iter_payloads(msg.data):
                                        d = parse_kv(payload)
                                        if d.get("type") != "chatmsg":
                                            continue
                                        text = d.get("txt")
                                        if not text:
                                            continue
                                        color = _DOUYU_COLOR_MAP.get(d.get("col", ""), 16777215)
                                        offset = time.monotonic() - start
                                        writer.write_danmaku(offset, text, color=color)
                                        count += 1
                                elif msg.type in {
                                    aiohttp.WSMsgType.CLOSE,
                                    aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.CLOSED,
                                    aiohttp.WSMsgType.ERROR,
                                }:
                                    break
                        finally:
                            heartbeat_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await heartbeat_task
                    finally:
                        with contextlib.suppress(Exception):
                            await ws.close()

                    # --- check whether to reconnect ---
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        break  # recording time exhausted

                    if reconnect_attempt >= max_reconnects:
                        if max_reconnects > 0:
                            logger.warning(
                                "Danmaku WS: max reconnects (%d) reached, stopping. collected=%d",
                                max_reconnects, count,
                            )
                        break

                    delay = min(reconnect_base_delay * (2 ** reconnect_attempt), 30)
                    if delay > remaining:
                        logger.info(
                            "Danmaku WS: backoff %.1fs exceeds remaining %.1fs, stopping. collected=%d",
                            delay, remaining, count,
                        )
                        break

                    logger.warning(
                        "Danmaku WS disconnected, reconnecting in %.1fs (attempt %d/%d, remaining=%.0fs, collected=%d)",
                        delay, reconnect_attempt + 1, max_reconnects, remaining, count,
                    )
                    await asyncio.sleep(delay)
                    reconnect_attempt += 1

                    try:
                        ws = await self._connect_ws(session)
                    except (aiohttp.ClientError, ssl.SSLError) as e:
                        logger.warning("Danmaku WS reconnect failed: %s", e)
                        continue  # will check remaining time / attempt limit at top of loop

                    logger.info(
                        "Danmaku WS reconnected successfully (attempt %d/%d, collected=%d)",
                        reconnect_attempt, max_reconnects, count,
                    )
        finally:
            writer.close()

        return count

    async def _connect_ws(self, session: aiohttp.ClientSession) -> aiohttp.ClientWebSocketResponse:
        """Connect websocket; fallback to a compat TLS config if OpenSSL handshake fails."""
        try:
            return await session.ws_connect(self._ws_url)
        except ssl.SSLError as e:
            if "handshake failure" not in str(e).lower():
                raise

        ctx = self._build_compat_ssl_context()
        return await session.ws_connect(self._ws_url, ssl=ctx)

    def _build_compat_ssl_context(self) -> ssl.SSLContext:
        # Douyu danmaku wss sometimes requires weaker DH params; OpenSSL 3 defaults may reject it.
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        return ctx

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                await ws.send_bytes(pack("type@=mrkl/"))
        except asyncio.CancelledError:
            raise
