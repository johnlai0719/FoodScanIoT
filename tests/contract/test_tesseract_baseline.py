from pathlib import Path

from tools.tesseract_baseline import parse_tsv, resolve_image


TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
1\t1\t0\t0\t0\t0\t0\t0\t100\t50\t-1\t
5\t1\t1\t1\t1\t1\t10\t5\t20\t10\t90.0\t成分
5\t1\t1\t1\t1\t2\t35\t5\t30\t10\t80.0\t砂糖
5\t1\t1\t1\t2\t1\t10\t25\t40\t10\t70.0\tNutrition
"""


def test_parse_tsv_groups_words_into_lines():
    lines = parse_tsv(TSV)
    assert lines == [
        {
            "text": "成分 砂糖",
            "score": 0.85,
            "box": [[10, 5], [65, 5], [65, 15], [10, 15]],
        },
        {
            "text": "Nutrition",
            "score": 0.7,
            "box": [[10, 25], [50, 25], [50, 35], [10, 35]],
        },
    ]


def test_resolve_image_accepts_root_below_images(tmp_path: Path):
    image = tmp_path / "beverage" / "case.jpg"
    image.parent.mkdir()
    image.write_bytes(b"test")
    assert resolve_image(tmp_path, "images/beverage/case.jpg") == image
