import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("stream_monitor")

# Shared request headers for Douyu API
_DOUYU_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://www.douyu.com',
    'Origin': 'https://www.douyu.com'
}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class StreamStatusMonitor:
    """Monitor a single Douyu streamer's live status via API polling."""

    def __init__(self, room_id: str, streamer_name: str):
        self.room_id = room_id
        self.streamer_name = streamer_name
        self._last_status: Optional[bool] = None  # None = uninitialized

    def is_live(self) -> bool:
        """Return cached live status. Defaults to False if uninitialized."""
        return self._last_status if self._last_status is not None else False

    async def check_is_streaming(self) -> Optional[bool]:
        """Call Douyu API to check live status.

        Returns:
            True if streaming, False if not, None if API error.
        """
        try:
            async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
                async with session.get(
                    f"https://www.douyu.com/betard/{self.room_id}",
                    headers=_DOUYU_HEADERS
                ) as response:
                    if response.status != 200:
                        logger.error(f"[{self.streamer_name}] Failed to get room info: HTTP {response.status}")
                        return None

                    room_info = await response.json()
                    if not room_info or 'room' not in room_info:
                        logger.error(f"[{self.streamer_name}] Invalid room info response format")
                        return None

                    room_data = room_info['room']
                    return room_data.get('show_status') == 1 and room_data.get('videoLoop') == 0

        except asyncio.TimeoutError:
            # TimeoutError 的 str(e) 通常为空，单独处理避免空白日志。
            logger.error(f"[{self.streamer_name}] Douyu API request timed out")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"[{self.streamer_name}] Douyu API request failed: {e}")
            return None
        except Exception as e:
            logger.error(
                f"[{self.streamer_name}] Unexpected error checking stream status: "
                f"{type(e).__name__}: {e}"
            )
            return None

    async def initialize(self) -> None:
        """Initialize cached status by calling the API directly.
        Called once on application startup.
        """
        status = await self.check_is_streaming()
        if status is not None:
            self._last_status = status
            logger.info(
                f"[{self.streamer_name}] Initialized status: "
                f"{'live' if status else 'offline'}"
            )
        else:
            self._last_status = False
            logger.warning(
                f"[{self.streamer_name}] Failed to get initial status from API, "
                f"defaulting to offline"
            )

    async def detect_change(self) -> Optional[tuple[bool, bool]]:
        """Check for status change since last call.

        Returns:
            (old_status, new_status) tuple if status changed,
            None if no change or API error.
        """
        current = await self.check_is_streaming()
        if current is None:
            return None  # API error, skip this cycle

        if self._last_status is None:
            # First call without initialize(), just cache and skip
            self._last_status = current
            return None

        if current != self._last_status:
            old = self._last_status
            self._last_status = current
            return (old, current)

        return None  # No change
