import xml.etree.ElementTree as ET
from pathlib import Path

from recording.xml_writer import BilibiliXmlWriter


def test_xml_is_parseable(tmp_path: Path):
    out = tmp_path / "a.xml"

    w = BilibiliXmlWriter(out)
    w.open()
    w.write_danmaku(1.23, "a & <b>")
    w.close()

    ET.parse(out)

