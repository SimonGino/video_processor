from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_ffmpeg_recorder_writes_part_file(tmp_path: Path):
    from recording.ffmpeg_recorder import FfmpegRecorder

    ffmpeg_stub = Path(__file__).resolve().parents[1] / "bin" / "ffmpeg"
    out = tmp_path / "a.flv.part"

    recorder = FfmpegRecorder(ffmpeg_path=str(ffmpeg_stub))
    rc = await recorder.record(
        url="https://example.invalid/live.flv",
        output_path=str(out),
        duration_seconds=1,
        headers={"User-Agent": "ua", "Referer": "https://www.douyu.com"},
    )

    assert rc == 0
    assert out.exists()
    assert out.read_bytes().startswith(b"stub-ffmpeg-output")

