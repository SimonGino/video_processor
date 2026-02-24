from recording.douyu_message_parser import parse_kv


def test_parse_chatmsg_minimal():
    s = "type@=chatmsg/nn@=u1/txt@=hello/"
    d = parse_kv(s)
    assert d["type"] == "chatmsg"
    assert d["nn"] == "u1"
    assert d["txt"] == "hello"


def test_parse_unescape():
    s = "type@=chatmsg/txt@=a@Sbc@Adef/"
    d = parse_kv(s)
    assert d["txt"] == "a/bc@def"

