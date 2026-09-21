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


def test_ingredient_anchor_does_not_cross_images():
    rows = [
        {"text": "成分：水、糖", "label": "成分", "probabilities": {}, "image_id": 0},
        {"text": "宣傳文字", "label": "其他", "probabilities": {}, "image_id": 1},
    ]
    assert MODULE.choose_ingredient_lines(rows, 0.35) == rows[:1]


def test_nutrition_conflicting_readings_are_not_selected():
    def parser(text):
        return {"protein": (float(text), None)}
    assert MODULE.parse_nutrition_rows([{"text": "3"}, {"text": "8"}], parser) == {}
    assert MODULE.parse_nutrition_rows([{"text": "3"}, {"text": "3"}], parser) == {"protein": (3.0, None)}


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


def test_fuzzy_correction_corrects_typos_from_tfda_lexicon():
    lexicon, by_len = MODULE.load_tfda_lexicon()
    assert len(lexicon) > 1000
    assert MODULE.correct_ingredient("正墊", lexicon, by_len) == "正鰹"
    assert MODULE.correct_ingredient("黧蛋", lexicon, by_len) == "雞蛋"
    assert MODULE.correct_ingredient("棻籽油", lexicon, by_len) in {"棉籽油", "菜籽油", "茶籽油"}
    assert MODULE.correct_ingredient("大豆油", lexicon, by_len) == "大豆油"

