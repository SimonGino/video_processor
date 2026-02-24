import asyncio
import contextlib
import time

import aiohttp

from .douyu_message_parser import parse_kv
from .stt_codec import iter_payloads, pack
from .xml_writer import BilibiliXmlWriter


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
                async with session.ws_connect(self._ws_url) as ws:
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
            writer.close()

        return count

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                await ws.send_bytes(pack("type@=mrkl/"))
        except asyncio.CancelledError:
            raise
