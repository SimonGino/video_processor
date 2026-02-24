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


class DouyuDanmakuCollector:
    def __init__(
        self,
        *,
        ws_url: str = "wss://danmuproxy.douyu.com:8506/",
        heartbeat_seconds: int = 30,
    ) -> None:
        self._ws_url = ws_url
        self._heartbeat_seconds = int(heartbeat_seconds)

    async def collect(self, *, room_id: str, output_path: str, duration_seconds: int) -> int:
        writer = BilibiliXmlWriter(output_path)
        writer.open()

        start = time.monotonic()
        count = 0

        try:
            async with aiohttp.ClientSession() as session:
                try:
                    ws = await self._connect_ws(session)
                except (aiohttp.ClientError, ssl.SSLError) as e:
                    logger.warning("Failed to connect douyu danmaku ws: %s", e)
                    return 0

                try:
                    await ws.send_bytes(pack(f"type@=loginreq/roomid@={room_id}/"))
                    await ws.send_bytes(pack(f"type@=joingroup/rid@={room_id}/gid@=-9999/"))

                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    try:
                        end = start + float(duration_seconds)
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
                                    offset = time.monotonic() - start
                                    writer.write_danmaku(offset, text)
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
