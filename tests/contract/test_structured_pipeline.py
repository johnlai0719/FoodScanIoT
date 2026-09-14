from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "tools" / "structured_pipeline.py"
SPEC = spec_from_file_location("structured_pipeline", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_split_ingredients_preserves_nested_formula():
    text = "成分：水、調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)、糖"
    assert MODULE.split_ingredients(text) == [
        "水", "調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)", "糖",
    ]


def test_schema_distinguishes_missing_from_zero():
    nutrition = MODULE.Nutrition(protein=0, fiber=None)
    assert nutrition.protein == 0
    assert nutrition.fiber is None


def test_anchor_fallback_stops_at_next_field():
    def row(text, label):
        return {"text": text, "label": label, "probabilities": {"成分": 0.1}}

    rows = [row("成分：水、糖", "其他"), row("香料", "其他"), row("營養標示", "營養")]
    assert [x["text"] for x in MODULE.choose_ingredient_lines(rows, 0.35)] == [
        "成分：水、糖", "香料",
    ]


def test_classifier_selection_requires_top_label():
    rows = [
        {"text": "蛋白質", "label": "營養", "probabilities": {"成分": 0.4}},
        {"text": "水、糖", "label": "成分", "probabilities": {"成分": 0.8}},
    ]
    assert [x["text"] for x in MODULE.choose_ingredient_lines(rows, 0.35)] == ["水、糖"]


def test_split_ingredients_handles_backticks():
    text = "柳丁原汁、麥芽糊精`檸檬酸、DL-蘋果酸`維生素C、香料"
    assert MODULE.split_ingredients(text) == [
        "柳丁原汁", "麥芽糊精", "檸檬酸", "DL-蘋果酸", "維生素C", "香料",
    ]


def test_head_pattern_matches_truncated_fen():
    def row(text, label):
        return {"text": text, "label": label, "probabilities": {"其他": 0.9}}

    rows = [
        row("名:義美厚奶茶", "品名"),
        row("分:水`生奶 奶粉 蔗糖", "品名"),
        row("紅茶 脂肪酸甘油酯", "成分"),
        row("(過敏原資訊:本產品含有奶類成分)", "過敏原"),
    ]
    chosen = MODULE.choose_ingredient_lines(rows, 0.35)
    assert [x["text"] for x in chosen] == [
        "分:水`生奶 奶粉 蔗糖", "紅茶 脂肪酸甘油酯",
    ]
    raw = "、".join(x["text"] for x in chosen)
    assert MODULE.split_ingredients(raw) == [
        "水", "生奶", "奶粉", "蔗糖", "紅茶", "脂肪酸甘油酯",
    ]


