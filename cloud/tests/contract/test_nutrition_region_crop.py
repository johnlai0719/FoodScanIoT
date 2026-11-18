from vision import region_crop as crop


def _line(text, x0, y0, x1, y1):
    return {"text": text, "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def _lines(sodium_values):
    return [
        _line("成分：梅、糖、甘草粉", 10, 15, 210, 25),
        _line("保存方式：請放陰涼乾燥處", 10, 45, 210, 55),
        _line("營養標示", 300, 20, 500, 30),
        _line("熱量", 300, 35, 325, 45),
        _line("蛋白質", 300, 50, 330, 60),
        _line("碳水化合物", 300, 65, 350, 75),
        _line("49.3公克", 435, 80, 500, 90),
        _line("鈉", 300, 90, 315, 100),
        _line(sodium_values, 345, 91, 500, 101),
        _line("4719886953749", 345, 107, 500, 120),
    ]


def test_nutrition_box_includes_adjacent_sodium_row_with_wide_merged_values():
    result = crop.find_regions(_lines("158.0 毫克 243.0 毫克"))

    assert result["nutrition"] == (300, 20, 500, 101)
    assert "末列鈉" in result["why"]["nutrition"]


def test_nutrition_box_does_not_expand_for_unconfirmed_sodium_or_barcode():
    result = crop.find_regions(_lines("158.0 243.0"))

    assert result["nutrition"] == (300, 20, 500, 90)
    assert "末列鈉" not in result["why"]["nutrition"]
