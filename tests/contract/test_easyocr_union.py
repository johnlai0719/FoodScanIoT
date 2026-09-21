import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
from easyocr_union_gemini import text_envelope


def test_union_preserves_space_between_boxes():
    rect, reason = text_envelope([[[100,100],[200,200]], [[600,600],[700,700]]], (1000,1000))
    assert rect == (60,60,740,740)
    assert reason is None


def test_no_boxes_returns_original():
    assert text_envelope([], (100,200)) == ((0,0,100,200), 'no_boxes')


def test_full_page_returns_original():
    assert text_envelope([[[0,0],[100,200]]], (100,200))[1] == 'little_background_removed'
