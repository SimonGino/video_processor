import asyncio
from collections.abc import Mapping
from pathlib import Path

from .danmaku_collector import DouyuDanmakuCollector
from .ffmpeg_recorder import FfmpegRecorder


def _finalize_part_path(part_path: str | Path) -> Path:
    p = Path(part_path)
    if p.suffix != ".part":
        raise ValueError(f"Expected .part file, got: {p}")
    return p.with_suffix("")


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
        )
    )

    rc, _ = await asyncio.gather(record_task, danmaku_task)

    flv_final = _finalize_part_path(flv_part)
    xml_final = _finalize_part_path(xml_part)

    if flv_part.exists():
        flv_part.replace(flv_final)
    if xml_part.exists():
        xml_part.replace(xml_final)

    return int(rc)
