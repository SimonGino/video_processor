import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from datetime import datetime

import config
from stream_monitor import StreamStatusMonitor

from .douyu_stream_resolver import DouyuH5PlayResolver
from .segment_pipeline import run_one_segment


logger = logging.getLogger("recording_service")


@dataclass(frozen=True)
class StreamerConfig:
    name: str
    room_id: str


def _segment_base_name(streamer_name: str, now: datetime) -> str:
    return f"{streamer_name}录播{now.strftime('%Y-%m-%dT%H_%M_%S')}"


async def run_recording_service() -> None:
    if not getattr(config, "RECORDING_ENABLED", True):
        logger.info("RECORDING_ENABLED=False，录制服务退出")
        return

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    streamers = [StreamerConfig(**s) for s in config.STREAMERS]
    tasks = [asyncio.create_task(_run_streamer(s, stop_event)) for s in streamers]

    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_streamer(streamer: StreamerConfig, stop_event: asyncio.Event) -> None:
    monitor = StreamStatusMonitor(streamer.room_id, streamer.name)
    await monitor.initialize()

    resolver = DouyuH5PlayResolver(
        did=getattr(config, "DOUYU_DID", "10000000000000000000000000001501"),
        cdn=getattr(config, "DOUYU_CDN", "hw-h5"),
        rate=getattr(config, "DOUYU_RATE", 0),
    )

    segment_seconds = int(getattr(config, "RECORDING_SEGMENT_MINUTES", 60)) * 60
    retry_delay = int(getattr(config, "RECORDING_RETRY_DELAY_SECONDS", 10))
    check_interval = int(getattr(config, "STREAM_STATUS_CHECK_INTERVAL", 10)) * 60
    danmaku_ws_url = getattr(config, "DANMAKU_WS_URL", "wss://danmuproxy.douyu.com:8506/")
    danmaku_heartbeat_seconds = int(getattr(config, "DANMAKU_HEARTBEAT_SECONDS", 30))

    while not stop_event.is_set():
        is_live = await monitor.check_is_streaming()
        if is_live is not True:
            await asyncio.sleep(check_interval)
            continue

        logger.info(f"[{streamer.name}] 检测到开播，开始录制")
        while not stop_event.is_set():
            try:
                stream_url, stream_headers = await resolver.resolve_stream_url(streamer.room_id)
            except Exception as e:
                logger.warning(f"[{streamer.name}] 获取流地址失败: {e}")
                await asyncio.sleep(retry_delay)
                continue

            base = _segment_base_name(streamer.name, datetime.now())
            flv_part_path = f"{config.PROCESSING_FOLDER}/{base}.flv.part"
            xml_part_path = f"{config.PROCESSING_FOLDER}/{base}.xml.part"

            try:
                rc = await run_one_segment(
                    room_id=streamer.room_id,
                    stream_url=stream_url,
                    stream_headers=stream_headers,
                    flv_part_path=flv_part_path,
                    xml_part_path=xml_part_path,
                    duration_seconds=segment_seconds,
                    ffmpeg_path=config.FFMPEG_PATH,
                    ws_url=danmaku_ws_url,
                    danmaku_heartbeat_seconds=danmaku_heartbeat_seconds,
                )
                if rc != 0:
                    logger.warning(f"[{streamer.name}] ffmpeg 退出码 {rc}，将尝试重启")
            except Exception as e:
                logger.exception(f"[{streamer.name}] 单段录制失败: {e}")

            if stop_event.is_set():
                break

            still_live = await monitor.check_is_streaming()
            if still_live is False:
                logger.info(f"[{streamer.name}] 检测到下播，结束录制")
                break

            await asyncio.sleep(retry_delay)
