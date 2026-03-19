import asyncio
import logging

from .recording.recording_service import run_recording_service
from .uploader import load_yaml_config
from .logging_config import setup_logging

_logger = logging.getLogger("recording.entry")


def main() -> None:
    setup_logging(is_recording_service=True)
    if not load_yaml_config():
        _logger.error("无法加载 config.yaml，录制服务无法获取主播配置，退出。")
        return
    asyncio.run(run_recording_service())


if __name__ == "__main__":
    main()

