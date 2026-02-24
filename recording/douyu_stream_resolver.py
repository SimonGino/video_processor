import hashlib
import time
from typing import Any

import aiohttp


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class DouyuH5PlayResolver:
    """Resolve Douyu live stream URL via getEncryption + getH5PlayV1."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.douyu.com",
        did: str = "10000000000000000000000000001501",
        cdn: str = "hw-h5",
        rate: int = 0,
        timeout_seconds: int = 10,
        user_agent: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._did = did
        self._cdn = cdn
        self._rate = int(rate)
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )

        self._key_data: dict[str, Any] | None = None
        self._key_expire_at: int = 0

    async def resolve_stream_url(self, room_id: str) -> tuple[str, dict[str, str]]:
        """Return (stream_url, headers) for ffmpeg."""
        headers = self._request_headers()
        data: dict[str, Any] | None = None
        for attempt in range(2):
            key_data = await self._ensure_key()
            ts = int(time.time())
            auth = self._sign(room_id=room_id, ts=ts, key_data=key_data)

            params: dict[str, Any] = {
                "cdn": self._cdn,
                "rate": str(self._rate),
                "ver": "219032101",
                "iar": "0",
                "ive": "0",
                "rid": str(room_id),
                "hevc": "0",
                "fa": "0",
                "sov": "0",
                "enc_data": key_data["enc_data"],
                "tt": str(ts),
                "did": self._did,
                "auth": auth,
            }

            try:
                data = await self._get_h5play(room_id=room_id, params=params, headers=headers)
                break
            except aiohttp.ClientResponseError as e:
                if e.status == 403 and attempt == 0:
                    self._invalidate_key()
                    continue
                raise

        if data is None:
            raise RuntimeError("Douyu getH5PlayV1 returned no data")

        if not isinstance(data, dict) or data.get("error") != 0:
            raise RuntimeError(f"Douyu getH5PlayV1 failed: {data}")

        play_info = data.get("data") or {}
        rtmp_url = play_info.get("rtmp_url")
        rtmp_live = play_info.get("rtmp_live")
        if rtmp_url and rtmp_live:
            return (f"{rtmp_url.rstrip('/')}/{str(rtmp_live).lstrip('/')}", headers)

        hls_url = play_info.get("hls_url")
        hls_live = play_info.get("hls_live")
        if hls_url and hls_live:
            return (f"{hls_url.rstrip('/')}/{str(hls_live).lstrip('/')}", headers)

        raise RuntimeError(f"Douyu play_info missing stream url: {play_info}")

    async def _ensure_key(self) -> dict[str, Any]:
        now = int(time.time())
        if self._key_data and now < self._key_expire_at:
            return self._key_data

        url = f"{self._base_url}/wgapi/livenc/liveweb/websec/getEncryption"
        params = {"did": self._did}
        headers = {"User-Agent": self._user_agent}
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if not isinstance(data, dict) or data.get("error") != 0:
            raise RuntimeError(f"Douyu getEncryption failed: {data}")

        key_data = data.get("data") or {}
        if not isinstance(key_data, dict) or "enc_data" not in key_data:
            raise RuntimeError(f"Douyu getEncryption invalid data: {data}")

        self._key_data = key_data
        self._key_expire_at = self._compute_key_expire_at(now=now, key_data=key_data)
        return key_data

    async def _get_h5play(
        self,
        *,
        room_id: str,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{self._base_url}/lapi/live/getH5PlayV1/{room_id}"
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(url, params=params, data=params, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _invalidate_key(self) -> None:
        self._key_data = None
        self._key_expire_at = 0

    def _compute_key_expire_at(self, *, now: int, key_data: dict[str, Any]) -> int:
        raw_expire_at = key_data.get("expire_at")
        if raw_expire_at is not None:
            try:
                expire_at = int(raw_expire_at)
            except (TypeError, ValueError):
                expire_at = 0
            if expire_at > 0:
                # Refresh slightly ahead of server-side expiry to avoid segment-boundary 403s.
                return max(0, expire_at - 5)
        # Fallback to a short cache if the server payload format changes.
        return now + 300

    def _sign(self, *, room_id: str, ts: int, key_data: dict[str, Any]) -> str:
        rand_str = str(key_data["rand_str"])
        enc_time = int(key_data["enc_time"])
        key = str(key_data["key"])
        is_special = int(key_data.get("is_special") or 0)

        secret = rand_str
        salt = "" if is_special else f"{room_id}{ts}"
        for _ in range(enc_time):
            secret = _md5(f"{secret}{key}")
        return _md5(f"{secret}{key}{salt}")

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Referer": "https://www.douyu.com",
            "Origin": "https://www.douyu.com",
        }
