import json

import pytest

from vision.structured_ocr import ingredients_lines, label_lines, nutrition_lines
from vision.table_md import labeled_rows, pairs_from_lines


def test_structured_ingredients_keep_first_occurrence_and_separate_warning():
    payload = {"ingredients": ["成分：水", " 水 ", "生奶", "紅茶"],
               "allergy_warning": "本產品含乳類"}
    assert ingredients_lines(json.dumps(payload)) == [
        "成分：水、生奶、紅茶", "過敏原資訊：本產品含乳類",
    ]


def test_structured_ingredients_remove_product_name_and_recover_misplaced_warning():
    payload = {"ingredients": ["義美奶茶", "水", "水。", "脂肪酸甘油酯（本產品含有奶類成分）",
                               "保存期限：16天"],
               "allergy_warning": None}
    assert ingredients_lines(json.dumps(payload), product_name="義美奶茶") == [
        "成分：水、脂肪酸甘油酯", "過敏原資訊：本產品含有奶類成分）",
    ]


def test_label_fields_are_only_kept_when_present_in_ocr_evidence():
    payload = {"ingredients": ["奶茶", "水", "生奶"], "allergy_warning": None,
               "name": "奶茶", "manufacturer": "猜的公司"}
    assert label_lines(json.dumps(payload), evidence="品名奶茶成分水生奶") == [
        "成分：水、生奶", "品名：奶茶",
    ]


def test_label_fields_remove_repeated_prefixes_and_omit_standalone_weight():
    payload = {"ingredients": ["梅", "６５公克", "淨重：65 g", "調味粉（含糖５公克）"],
               "allergy_warning": "本產品含麩質", "name": "品名：品名：梅仔餅",
               "manufacturer": "廠商：委製商：佳美產業有限公司"}
    assert label_lines(json.dumps(payload), evidence="品名梅仔餅委製商佳美產業有限公司") == [
        "成分：梅、調味粉（含糖５公克）", "過敏原資訊：本產品含麩質",
        "品名：梅仔餅", "廠商：佳美產業有限公司",
    ]


def test_structured_nutrition_keeps_daily_value_separate_and_missing_cells_empty():
    payload = {"serving_size": "30公克", "servings_per_container": "2",
               "columns": ["per_serving", "daily_value"],
               "rows": [{"nutrient": "糖", "values": [None, "2%"]}]}
    assert nutrition_lines(json.dumps(payload)) == [
        "每一份量 30公克", "本包裝含 2份",
        "| 營養標示 | 每份 | 每日參考值百分比 |",
        "| ---- | ---- | ---- |",
        "| 糖 | — | 2% |",
    ]


def test_conflicting_nutrition_rows_are_rejected():
    payload = {"serving_size": None, "servings_per_container": None,
               "columns": ["per_100"],
               "rows": [{"nutrient": "鈉", "values": ["5毫克"]},
                        {"nutrient": "鈉", "values": ["50毫克"]}]}
    with pytest.raises(ValueError, match="duplicate"):
        nutrition_lines(json.dumps(payload))


def test_nutrition_rejects_column_labels_in_serving_fields():
    payload = {"serving_size": "4份", "servings_per_container": "每份 每100毫升",
               "columns": ["per_serving"], "rows": [{"nutrient": "鈉", "values": ["5毫克"]}]}
    with pytest.raises(ValueError, match="serving"):
        nutrition_lines(json.dumps(payload))


def test_merged_headers_do_not_shift_single_serving_values_into_per_100():
    lines = ["每份每100毫升", "熱量", "164大卡", "蛋白質", "4.6公克",
             "脂肪", "5.5公克", "糖", "23.5公克", "鈉", "72毫克"]
    assert labeled_rows(lines) == {}
    lines[3:3] = ["54.5大卡"]
    lines[-1:] = ["72毫克", "24毫克"]
    assert labeled_rows(lines) == {"calories": (164, 54.5), "sodium": (72, 24)}


def test_structured_table_uses_named_columns_even_when_reversed():
    assert pairs_from_lines([
        "| 營養標示 | 每100毫升 | 每份 |",
        "| ---- | ---- | ---- |",
        "| 熱量 | 54.5大卡 | 164大卡 |",
    ]) == {"calories": (164, 54.5)}


def test_markdown_with_one_cell_and_merged_headers_leaves_basis_unknown():
    assert pairs_from_lines([
        "| 營養標示 | 每份每１００毫升 |",
        "| ---- | ---- |",
        "| 熱量 | 164大卡 |",
    ]) == {}
