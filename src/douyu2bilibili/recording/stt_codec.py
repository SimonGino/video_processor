import struct
from collections.abc import Iterator


_DOUYU_OP_CODE = 689
_HEADER_SIZE = 12


def escape(s: str) -> str:
    return s.replace("@", "@A").replace("/", "@S")


def unescape(s: str) -> str:
    return s.replace("@S", "/").replace("@A", "@")


def pack(payload: str) -> bytes:
    """Pack a single Douyu STT payload into a binary frame."""
    if not payload.endswith("/"):
        payload = f"{payload}/"

    payload_bytes = payload.encode("utf-8") + b"\x00"
    length = len(payload_bytes) + 8
    header = struct.pack("<I", length) * 2 + struct.pack("<I", _DOUYU_OP_CODE)
    return header + payload_bytes


def iter_payloads(data: bytes) -> Iterator[str]:
    """Yield STT payload strings from a binary message (supports concatenated packets)."""
    offset = 0
    data_len = len(data)
    while offset + 4 <= data_len:
        (length,) = struct.unpack_from("<I", data, offset)
        packet_size = int(length) + 4
        if packet_size <= _HEADER_SIZE or offset + packet_size > data_len:
            break

        payload = data[offset + _HEADER_SIZE : offset + packet_size]
        payload = payload.split(b"\x00", 1)[0]
        yield payload.decode("utf-8", errors="ignore")
        offset += packet_size

