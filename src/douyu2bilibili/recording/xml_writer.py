import time
from pathlib import Path
from typing import TextIO
from xml.sax.saxutils import escape as xml_escape


class BilibiliXmlWriter:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._fp: TextIO | None = None

    def open(self) -> None:
        if self._fp is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self._path.open("w", encoding="utf-8", newline="\n")
        self._fp.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        self._fp.write("<i>\n")
        self._fp.flush()

    def write_danmaku(
        self,
        offset_seconds: float,
        text: str,
        *,
        mode: int = 1,
        font_size: int = 25,
        color: int = 16777215,
        timestamp: int | None = None,
        pool: int = 0,
        uid: int = 0,
        row_id: int = 0,
    ) -> None:
        if self._fp is None:
            raise RuntimeError("Writer is not opened")

        ts = int(time.time()) if timestamp is None else int(timestamp)
        p = f"{offset_seconds:.2f},{mode},{font_size},{color},{ts},{pool},{uid},{row_id}"
        self._fp.write(f'<d p="{p}">{xml_escape(text)}</d>\n')

    def close(self) -> None:
        if self._fp is None:
            return
        self._fp.write("</i>\n")
        self._fp.flush()
        self._fp.close()
        self._fp = None

