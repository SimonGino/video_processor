"""Tests for danmaku_postprocess module."""
import os
import tempfile
import textwrap

import pytest

from douyu2bilibili.danmaku_postprocess import postprocess_ass

# --- Helpers ---

SAMPLE_ASS = textwrap.dedent("""\
    [Script Info]
    ScriptType: v4.00+
    PlayResX: 1920
    PlayResY: 1080

    [V4+ Styles]
    Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
    Style: R2L,Microsoft YaHei,40,&H4BFFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,1.0,8,0,0,0,1
    Style: L2R,Microsoft YaHei,40,&H4BFFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,1.0,8,0,0,0,1
    Style: TOP,Microsoft YaHei,40,&H4BFFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,1.0,8,0,0,0,1
    Style: BTM,Microsoft YaHei,40,&H4BFFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,1.0,8,0,0,0,1
    Style: SP,Microsoft YaHei,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,1.0,8,0,0,0,1
    Style: message_box,Microsoft YaHei,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,0.7,7,0,0,0,1
    Style: price,Microsoft YaHei,26,&H00FFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,0,0,0,0,100.00,100.00,0.00,0.00,1,0.0,0.7,7,0,0,0,1

    [Events]
    Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    Dialogue: 0,0:00:05.00,0:00:17.00,R2L,,0000,0000,0000,,{\\move(1920,100,-200,100)}{\\c&H4BFFFFFF}Top area danmaku
    Dialogue: 0,0:00:06.00,0:00:18.00,R2L,,0000,0000,0000,,{\\move(1920,400,-200,400)}{\\c&H4BFFFFFF}Middle danmaku
    Dialogue: 0,0:00:07.00,0:00:19.00,R2L,,0000,0000,0000,,{\\move(1920,800,-200,800)}{\\c&H4BFFFFFF}Lower danmaku
    Dialogue: 0,0:00:08.00,0:00:20.00,R2L,,0000,0000,0000,,{\\move(1920,1000,-200,1000)}{\\c&H4BFFFFFF}Bottom area danmaku
    Dialogue: 1,0:00:09.00,0:00:14.00,BTM,,0000,0000,0000,,{\\pos(960,1000)}{\\c&H4BFFFFFF}Fixed bottom danmaku
    Dialogue: 0,0:00:10.00,0:00:10.20,message_box,,0000,0000,0000,,{\\move(10,500,10,300)}{\\c&HD8D8FF}SuperChat message
    Dialogue: 1,0:00:10.00,0:00:10.20,price,,0000,0000,0000,,{\\move(10,520,10,320)}{\\c&HD8D8FF}$100
    Dialogue: 0,0:00:11.00,0:00:23.00,R2L,,0000,0000,0000,,{\\move(1920,200,-200,200)}{\\c&H00FF00}Colored danmaku
""")


def _write_and_process(content, **kwargs):
    """Write content to a temp file, run postprocess, return result."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ass", delete=False, encoding="utf-8-sig") as f:
        f.write(content)
        path = f.name
    try:
        postprocess_ass(path, **kwargs)
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    finally:
        os.unlink(path)


def _count_dialogues(content, style=None):
    """Count Dialogue lines, optionally filtered by style."""
    count = 0
    for line in content.splitlines():
        if line.strip().startswith("Dialogue:"):
            if style is None or f",{style}," in line:
                count += 1
    return count


def _get_style_primary_alpha(content, style_name):
    """Extract the PrimaryColour alpha from a named style."""
    for line in content.splitlines():
        if line.strip().startswith("Style:"):
            fields = line.split("Style:", 1)[1].split(",")
            if fields[0].strip() == style_name:
                # PrimaryColour is field index 3: &HAA......
                import re
                m = re.search(r"&H([0-9A-Fa-f]{2})", fields[3])
                return m.group(1) if m else None
    return None


# =============================================================================
# 4.1 Display area clipping tests
# =============================================================================

class TestDisplayAreaClipping:
    """Test display area clipping for R2L danmaku."""

    def test_full_screen_no_clipping(self):
        """display_area=1.0 should keep all R2L events unchanged."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=1.0)
        # 5 R2L events in sample
        assert _count_dialogues(result, "R2L") == 5

    def test_half_screen(self):
        """display_area=0.5 → threshold=540; keep y<=540, remove y>540."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=0.5)
        # y=100 kept, y=400 kept, y=800 removed, y=1000 removed, y=200 kept
        assert _count_dialogues(result, "R2L") == 3

    def test_quarter_screen(self):
        """display_area=0.25 → threshold=270; keep y<=270, remove y>270."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=0.25)
        # y=100 kept, y=400 removed, y=800 removed, y=1000 removed, y=200 kept
        assert _count_dialogues(result, "R2L") == 2

    def test_different_resolution(self):
        """display_area=0.5 with 720p → threshold=360."""
        # Replace PlayResY but keep the same Y coords in events
        result = _write_and_process(SAMPLE_ASS, resolution_y=720, display_area=0.5)
        # threshold=360: y=100 kept, y=400 removed, y=800 removed, y=1000 removed, y=200 kept
        assert _count_dialogues(result, "R2L") == 2


# =============================================================================
# 4.2 Opacity tests
# =============================================================================

class TestOpacity:
    """Test opacity modification of style PrimaryColour alpha."""

    def test_default_opacity_08(self):
        """opacity=0.8 → alpha=33 (round((1-0.8)*255)=51=0x33)."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=0.8)
        assert _get_style_primary_alpha(result, "R2L") == "33"
        assert _get_style_primary_alpha(result, "L2R") == "33"
        assert _get_style_primary_alpha(result, "TOP") == "33"
        assert _get_style_primary_alpha(result, "BTM") == "33"

    def test_fully_opaque(self):
        """opacity=1.0 → alpha=00."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=1.0)
        assert _get_style_primary_alpha(result, "R2L") == "00"

    def test_half_transparent(self):
        """opacity=0.5 → alpha=80 (round((1-0.5)*255)=128=0x80)."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=0.5)
        assert _get_style_primary_alpha(result, "R2L") == "80"


# =============================================================================
# 4.3 Color toggle tests
# =============================================================================

class TestColorToggle:
    """Test color tag preservation/removal."""

    def test_color_enabled_preserves_tags(self):
        """color_enabled=True should keep all \\c tags."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, color_enabled=True)
        assert "{\\c&H00FF00}" in result
        assert "{\\c&H4BFFFFFF}" in result

    def test_color_disabled_removes_tags(self):
        """color_enabled=False should remove all {\\c&H......} tags from events."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, color_enabled=False)
        import re
        # No standalone color tags should remain in event lines
        for line in result.splitlines():
            if line.strip().startswith("Dialogue:"):
                assert not re.search(r"\{\\c&H[0-9A-Fa-f]{6,8}\}", line), \
                    f"Color tag found in: {line}"


# =============================================================================
# 4.4 Boundary tests: BTM/SP/message_box not affected
# =============================================================================

class TestBoundaryStyles:
    """BTM, SP, message_box, price styles should not be clipped or have opacity changed."""

    def test_btm_not_clipped(self):
        """BTM events should be preserved regardless of display_area."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=0.25)
        assert _count_dialogues(result, "BTM") == 1

    def test_message_box_not_clipped(self):
        """message_box events should be preserved regardless of display_area."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=0.25)
        assert _count_dialogues(result, "message_box") == 1

    def test_price_not_clipped(self):
        """price events should be preserved regardless of display_area."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, display_area=0.25)
        assert _count_dialogues(result, "price") == 1

    def test_sp_opacity_unchanged(self):
        """SP style PrimaryColour should not be modified by opacity setting."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=0.5)
        # SP original alpha is 00, should remain unchanged
        assert _get_style_primary_alpha(result, "SP") == "00"

    def test_message_box_opacity_unchanged(self):
        """message_box style PrimaryColour should not be modified by opacity."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=0.5)
        assert _get_style_primary_alpha(result, "message_box") == "00"

    def test_price_opacity_unchanged(self):
        """price style PrimaryColour should not be modified by opacity."""
        result = _write_and_process(SAMPLE_ASS, resolution_y=1080, opacity=0.5)
        assert _get_style_primary_alpha(result, "price") == "00"
