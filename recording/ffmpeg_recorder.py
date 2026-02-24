import asyncio
from collections.abc import Mapping


def _build_header_arg(headers: Mapping[str, str]) -> str:
    return "".join(f"{k}: {v}\r\n" for k, v in headers.items())


class FfmpegRecorder:
    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg_path = ffmpeg_path

    async def record(
        self,
        *,
        url: str,
        output_path: str,
        duration_seconds: int,
        headers: Mapping[str, str] | None = None,
    ) -> int:
        args: list[str] = [
            self._ffmpeg_path,
            "-hide_banner",
            "-y",
            "-loglevel",
            "error",
        ]

        if headers:
            args += ["-headers", _build_header_arg(headers)]

        args += [
            "-i",
            url,
            "-c",
            "copy",
            "-t",
            str(int(duration_seconds)),
            "-f",
            "flv",
            output_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timeout = max(10, int(duration_seconds) + 30)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            return 124

        return int(proc.returncode or 0)

