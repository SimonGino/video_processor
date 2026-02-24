import asyncio
import logging

from recording.recording_service import run_recording_service


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(run_recording_service())


if __name__ == "__main__":
    main()

