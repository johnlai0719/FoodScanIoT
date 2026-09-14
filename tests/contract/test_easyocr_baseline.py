from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "tools" / "easyocr_baseline.py"
SPEC = spec_from_file_location("easyocr_baseline", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_select_device():
    assert MODULE.select_device("auto", True) == "cuda"
    assert MODULE.select_device("auto", False) == "cpu"
    assert MODULE.select_device("cpu", True) == "cpu"


def test_normalize_results_orders_by_position():
    raw = [
        ([[40, 30], [80, 30], [80, 50], [40, 50]], "第二行", 0.7),
        ([[10, 5], [70, 5], [70, 20], [10, 20]], "第一行", 0.9),
    ]
    assert MODULE.normalize_results(raw) == [
        {
            "text": "第一行",
            "score": 0.9,
            "box": [[10, 5], [70, 5], [70, 20], [10, 20]],
        },
        {
            "text": "第二行",
            "score": 0.7,
            "box": [[40, 30], [80, 30], [80, 50], [40, 50]],
        },
    ]
