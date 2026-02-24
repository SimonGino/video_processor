from recording.stt_codec import escape, iter_payloads, pack, unescape


def test_escape_roundtrip():
    s = "a/@b@c/中文"
    assert unescape(escape(s)) == s


def test_pack_iter_payloads_roundtrip_single_packet():
    payload = "type@=loginreq/roomid@=1/"
    frame = pack(payload)
    assert list(iter_payloads(frame)) == [payload]


def test_pack_iter_payloads_roundtrip_concat_packets():
    payload1 = "type@=loginreq/roomid@=1/"
    payload2 = "type@=joingroup/rid@=1/gid@=-9999/"
    frame = pack(payload1) + pack(payload2)
    assert list(iter_payloads(frame)) == [payload1, payload2]

