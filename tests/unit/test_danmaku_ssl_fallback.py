import ssl

import pytest


class _FakeWebSocket:
    async def close(self) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.ssl_args: list[object | None] = []

    async def ws_connect(self, url: str, ssl: object | None = None):  # noqa: A002
        self.ssl_args.append(ssl)
        if len(self.ssl_args) == 1:
            raise ssl_module.SSLError("sslv3 alert handshake failure")
        return _FakeWebSocket()


ssl_module = ssl


@pytest.mark.asyncio
async def test_danmaku_collector_fallback_ssl_on_handshake_failure():
    from recording.danmaku_collector import DouyuDanmakuCollector

    collector = DouyuDanmakuCollector(ws_url="wss://example.invalid:8506/")
    session = _FakeSession()

    ws = await collector._connect_ws(session)  # type: ignore[attr-defined]
    await ws.close()

    assert session.ssl_args[0] is None
    assert isinstance(session.ssl_args[1], ssl.SSLContext)
    assert session.ssl_args[1].minimum_version == ssl.TLSVersion.TLSv1_2
    assert session.ssl_args[1].maximum_version == ssl.TLSVersion.TLSv1_2

