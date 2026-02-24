from .stt_codec import unescape


def parse_kv(payload: str) -> dict[str, str]:
    """Parse Douyu STT key/value payload into a dict."""
    result: dict[str, str] = {}
    for token in payload.split("/"):
        if not token or "@=" not in token:
            continue
        key, value = token.split("@=", 1)
        result[key] = unescape(value)
    return result

