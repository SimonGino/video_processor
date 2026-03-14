import asyncio
import logging

from .recording.recording_service import run_recording_service
from .uploader import load_yaml_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    if not load_yaml_config():
        logging.error("无法加载 config.yaml，录制服务无法获取主播配置，退出。")
        return
    asyncio.run(run_recording_service())


if __name__ == "__main__":
    main()

