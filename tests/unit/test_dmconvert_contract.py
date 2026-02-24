from pathlib import Path

from dmconvert import convert_xml_to_ass

from recording.xml_writer import BilibiliXmlWriter


def test_dmconvert_can_convert(tmp_path: Path):
    xml_path = tmp_path / "a.xml"
    ass_path = tmp_path / "a.ass"

    w = BilibiliXmlWriter(xml_path)
    w.open()
    w.write_danmaku(1.0, "hello")
    w.close()

    convert_xml_to_ass(
        font_size=40,
        sc_font_size=38,
        resolution_x=1920,
        resolution_y=1080,
        xml_file=str(xml_path),
        ass_file=str(ass_path),
    )

    assert ass_path.exists()
    assert "hello" in ass_path.read_text(encoding="utf-8", errors="ignore")

