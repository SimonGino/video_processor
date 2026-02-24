import hashlib

import pytest
from aiohttp import web


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _compute_auth(rid: str, ts: int, rand_str: str, key: str, enc_time: int, is_special: int) -> str:
    secret = rand_str
    salt = "" if is_special else f"{rid}{ts}"
    for _ in range(enc_time):
        secret = _md5(f"{secret}{key}")
    return _md5(f"{secret}{key}{salt}")


@pytest.mark.asyncio
async def test_resolve_stream_url_flv_h5playv1_http_stub():
    from recording.douyu_stream_resolver import DouyuH5PlayResolver

    rid = "1234"

    encryption_data = {
        "enc_data": "ENC_DATA",
        "rand_str": "RAND",
        "key": "KEY",
        "enc_time": 2,
        "is_special": 0,
    }

    async def handle_get_encryption(request: web.Request) -> web.Response:
        assert request.query.get("did")
        return web.json_response({"error": 0, "msg": "", "data": encryption_data})

    async def handle_get_h5play_v1(request: web.Request) -> web.Response:
        assert request.match_info["rid"] == rid

        q = request.query
        did = q.get("did")
        tt = int(q.get("tt"))
        auth = q.get("auth")
        enc_data = q.get("enc_data")

        assert did
        assert enc_data == encryption_data["enc_data"]
        assert auth == _compute_auth(
            rid=rid,
            ts=tt,
            rand_str=encryption_data["rand_str"],
            key=encryption_data["key"],
            enc_time=encryption_data["enc_time"],
            is_special=encryption_data["is_special"],
        )

        return web.json_response(
            {
                "error": 0,
                "msg": "",
                "data": {
                    "rtmp_url": "https://example.invalid/live",
                    "rtmp_live": "stream.flv?token=abc",
                },
            }
        )

    app = web.Application()
    app.router.add_get("/wgapi/livenc/liveweb/websec/getEncryption", handle_get_encryption)
    app.router.add_post("/lapi/live/getH5PlayV1/{rid}", handle_get_h5play_v1)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        resolver = DouyuH5PlayResolver(
            base_url=f"http://127.0.0.1:{port}",
            did="TEST_DID",
            cdn="hw-h5",
            rate=0,
        )
        url, headers = await resolver.resolve_stream_url(rid)
        assert url == "https://example.invalid/live/stream.flv?token=abc"
        assert headers.get("Referer") == "https://www.douyu.com"
        assert "User-Agent" in headers
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_resolve_stream_url_retries_with_fresh_encryption_after_403():
    from recording.douyu_stream_resolver import DouyuH5PlayResolver

    rid = "1234"

    encryption_payloads = [
        {
            "enc_data": "ENC_DATA_OLD",
            "rand_str": "RAND1",
            "key": "KEY1",
            "enc_time": 1,
            "is_special": 0,
            "expire_at": 1,
        },
        {
            "enc_data": "ENC_DATA_NEW",
            "rand_str": "RAND2",
            "key": "KEY2",
            "enc_time": 1,
            "is_special": 0,
            "expire_at": 9999999999,
        },
    ]
    get_encryption_calls = 0
    get_h5play_calls = 0

    async def handle_get_encryption(request: web.Request) -> web.Response:
        nonlocal get_encryption_calls
        payload = encryption_payloads[min(get_encryption_calls, len(encryption_payloads) - 1)]
        get_encryption_calls += 1
        return web.json_response({"error": 0, "msg": "", "data": payload})

    async def handle_get_h5play_v1(request: web.Request) -> web.StreamResponse:
        nonlocal get_h5play_calls
        get_h5play_calls += 1
        q = request.query
        enc_data = q.get("enc_data")
        tt = int(q.get("tt"))
        auth = q.get("auth")

        if enc_data == "ENC_DATA_OLD":
            raise web.HTTPForbidden()

        assert enc_data == "ENC_DATA_NEW"
        assert auth == _compute_auth(
            rid=rid,
            ts=tt,
            rand_str="RAND2",
            key="KEY2",
            enc_time=1,
            is_special=0,
        )
        return web.json_response(
            {
                "error": 0,
                "msg": "",
                "data": {
                    "rtmp_url": "https://example.invalid/live",
                    "rtmp_live": "stream.flv",
                },
            }
        )

    app = web.Application()
    app.router.add_get("/wgapi/livenc/liveweb/websec/getEncryption", handle_get_encryption)
    app.router.add_post("/lapi/live/getH5PlayV1/{rid}", handle_get_h5play_v1)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        resolver = DouyuH5PlayResolver(
            base_url=f"http://127.0.0.1:{port}",
            did="TEST_DID",
        )
        url, _ = await resolver.resolve_stream_url(rid)
        assert url == "https://example.invalid/live/stream.flv"
        assert get_h5play_calls == 2
        assert get_encryption_calls == 2
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ensure_key_refreshes_when_server_expire_at_passed():
    from recording.douyu_stream_resolver import DouyuH5PlayResolver

    payloads = [
        {
            "enc_data": "ENC_DATA_1",
            "rand_str": "RAND1",
            "key": "KEY1",
            "enc_time": 1,
            "is_special": 0,
            "expire_at": 1,
        },
        {
            "enc_data": "ENC_DATA_2",
            "rand_str": "RAND2",
            "key": "KEY2",
            "enc_time": 1,
            "is_special": 0,
            "expire_at": 9999999999,
        },
    ]
    get_encryption_calls = 0

    async def handle_get_encryption(_: web.Request) -> web.Response:
        nonlocal get_encryption_calls
        payload = payloads[min(get_encryption_calls, len(payloads) - 1)]
        get_encryption_calls += 1
        return web.json_response({"error": 0, "msg": "", "data": payload})

    app = web.Application()
    app.router.add_get("/wgapi/livenc/liveweb/websec/getEncryption", handle_get_encryption)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        resolver = DouyuH5PlayResolver(
            base_url=f"http://127.0.0.1:{port}",
            did="TEST_DID",
        )
        first = await resolver._ensure_key()
        second = await resolver._ensure_key()
        assert first["enc_data"] == "ENC_DATA_1"
        assert second["enc_data"] == "ENC_DATA_2"
        assert get_encryption_calls == 2
    finally:
        await runner.cleanup()
