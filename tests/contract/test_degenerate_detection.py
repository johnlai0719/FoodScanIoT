"""重複退化偵測的契約。

自回歸退化是本管線 p95 延遲的來源：177 案的中位是 7.81 秒，但 p95 到 19.06、
最大 50.8 秒，尾巴幾乎全部來自模型在裁切圖上讀不到東西便開始複讀，
一路吃到 max_tokens。

**這裡守的是「別改回用長度判斷」。** 2026-09-14 以 177 案的既有輸出量測：

    c150 百事可樂無糖   3292 字   「公克」重複 1366 次   ← 退化
    c159 義美蘇打餅乾   3563 字   最高重複 6 次          ← 正常（全集最長）

最長的那一案是正常的，退化的那一案還更短。長度沒有判別力——用長度上限會
截掉 c159 這種密集雙語標示，同時放過 c150。

實測效果（c150 的營養標示區，同一張圖）：
    不中止    20.4 秒   finish=degenerate   保留 101 字
    串流中止   1.8 秒   finish=degenerate   保留 101 字   ← 輸出逐字相同
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "PPOCR_TEST"))

import degenerate as DG  # noqa: E402


# ── 該抓的 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("unit", ["公克", "克", "的", "abc", "、 "])
def test_a_repeated_unit_is_detected(unit):
    text = "營養標示 每一份量 330毫升 熱量 0大卡 " + unit * 400
    assert DG.is_degenerate(text), "%r 重複 400 次沒有被判為退化" % unit


def test_the_kept_prefix_is_the_clean_part():
    """保留前段而不是整份丟掉——退化發生在後面，前半通常是讀對的。"""
    head = "營養標示 每一份量 330毫升 熱量 0大卡 蛋白質 0公克 "
    kept, cut, n = DG.trim(head + "公克" * 400)
    assert cut is True
    assert kept == head, "切掉的位置不對：%r" % kept[-40:]
    assert n == 800


def test_a_run_in_the_middle_is_found_by_the_full_scan():
    """退化不一定在結尾。

    c150 的「公克」重複 1366 次之後，模型又接回營養表——乾淨的表格結尾
    把退化段蓋在中間。只看尾段會整案漏掉，所以清理既有輸出必須掃全文。
    """
    head, tail = "成分：水、二氧化碳、焦糖色素。", "\n| 熱量 | 0大卡 |\n| 鈉 | 25毫克 |"
    text = head + "公克" * 400 + tail
    assert DG.find_degenerate_tail(text)[0] is None, "尾段是乾淨的，不該由尾段判準命中"
    cleaned, removed = DG.clean(text)
    assert removed == 800
    assert cleaned == head + tail


# ── 不該抓的 ─────────────────────────────────────────────────────────────────
def test_a_dense_nutrition_table_is_not_degenerate():
    """營養表每一列結構相同但內容不同，不是週期。

    這是最容易誤判的形狀——「公克」在正常營養表裡本來就會出現十幾次。
    """
    rows = ["| 熱量 | 147大卡 | 488大卡 |", "| 蛋白質 | 2.7公克 | 9.0公克 |",
            "| 脂肪 | 6.3公克 | 21.0公克 |", "| 飽和脂肪 | 3.7公克 | 12.2公克 |",
            "| 反式脂肪 | 0公克 | 0公克 |", "| 碳水化合物 | 19.8公克 | 65.9公克 |",
            "| 糖 | 0.3公克 | 1.1公克 |", "| 鈉 | 126毫克 | 420毫克 |"]
    text = "\n".join(rows * 4)
    assert not DG.is_degenerate(text), "正常營養表被誤判為退化"
    assert DG.clean(text)[1] == 0


def test_a_long_ingredient_list_is_not_degenerate():
    """成分表用頓號分隔、每項都不同，長度可以很長但不是週期。"""
    text = "、".join("成分%d" % i for i in range(200))
    assert not DG.is_degenerate(text)


def test_short_text_is_never_flagged():
    """視窗長度以下不判——樣本太少，判了也不可信。"""
    assert not DG.is_degenerate("公克" * 10)


def test_a_unit_repeated_only_a_few_times_is_not_flagged():
    """重複四次以下是巧合不是退化。"""
    assert not DG.is_degenerate("每份每份每份 " + "x" * 10)


# ── 門檻是量出來的 ──────────────────────────────────────────────────────────
def test_thresholds_are_the_measured_ones():
    """改門檻前請用 177 案重跑：目前設定下 2 案命中、175 案零誤判。"""
    assert DG.MAX_PERIOD == 16
    assert DG.WINDOW == 180
    assert DG.PERIOD_RATIO == 0.9


def test_streaming_abort_is_enabled_by_default():
    """預設要開。關掉的話退化案仍會跑滿 max_tokens，只是事後把垃圾清掉——
    輸出一樣乾淨，但 20 秒照付。"""
    import re
    src = (REPO / "PPOCR_TEST" / "run_vlcrop.py").read_text(encoding="utf-8")
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    assert 'VLCROP_STREAM_ABORT", "1"' in src, "串流中止的預設值不是開啟"


def test_streaming_does_not_use_the_broken_unicode_decoder():
    """`iter_lines(decode_unicode=True)` 會把跨 chunk 的多位元組字元切壞。

    實測 3102 字的輸出只收到 140 字，偵測器因此永遠不會觸發——
    而且是**靜默**失效：看起來有在跑，只是從來沒抓到過。
    """
    import re
    src = (REPO / "PPOCR_TEST" / "run_vlcrop.py").read_text(encoding="utf-8")
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    assert "decode_unicode=True" not in src
