"""撇號不得影響添加物比對，且兩份實作必須一致。

核苷酸類調味劑寫作 `5'-次黃嘌呤核苷磷酸二鈉`，而 OCR 對那一撇**時有時無**
——同一批照片裡 `5'-次黃` 出現 13 次、`5-次黃` 出現 5 次，後者配不到。
那一撇不帶任何區辨資訊：知識庫裡不存在「5-次黃」與「5'-次黃」兩種添加物。

它是全庫第 5 常見的添加物（25 案），而降階路徑走 RapidOCR 不走 Gemini，
漏撇號的機率更高。

⚠ **本專案有兩份比對實作**，這個測試同時鎖住兩邊：
    server/module_a/ingredient_parser.normalize_text   線上（App 實際走的）
    PPOCR_TEST/sim_match.normalize_text                評估與實驗用
2026-09-11 先改了評估台，09-12 才補上線上——中間一天是分岔狀態。
見 `專案管理/文件與程式落差對照表` 六之二。
"""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'cloud'))
from module_a.ingredient_parser import normalize_text  # noqa: E402

# 同一個物質的各種撇號寫法，正規化後必須完全相同
SAME = [
    pytest.param(["5'-次黃嘌呤核苷磷酸二鈉", "5’-次黃嘌呤核苷磷酸二鈉",
                  "5′-次黃嘌呤核苷磷酸二鈉", "5-次黃嘌呤核苷磷酸二鈉"],
                 id='inosinate'),
    pytest.param(["5'-鳥嘌呤核苷磷酸二鈉", "5-鳥嘌呤核苷磷酸二鈉"], id='guanylate'),
]


@pytest.mark.parametrize('variants', SAME)
def test_apostrophe_variants_normalize_identically(variants):
    forms = {normalize_text(v) for v in variants}
    assert len(forms) == 1, f'撇號寫法沒有收斂成同一個形式：{forms}'


@pytest.mark.parametrize('name', [
    'L-麩酸鈉', 'DL-蘋果酸', 'D-山梨醇', '碳酸鈉', '碳酸鉀', '檸檬酸鈉', '檸檬酸鉀',
])
def test_other_names_are_untouched(name):
    """去撇號不可波及靠字母前綴或單一字元區辨的條目。

    `DL-` 與 `L-`、`碳酸鈉` 與 `碳酸鉀` 都是不同物質，
    連字號與那些字必須留著。
    """
    out = normalize_text(name)
    assert out, name
    # 連字號要保留（D-／DL-／L- 的區辨靠它前面的字母，但形狀不應被破壞）
    if '-' in name:
        assert '-' in out


def test_apostrophe_is_actually_removed_not_just_unified():
    """`_PUNCT_CANON` 只把彎撇統一成直撇，**不會去掉它**。

    只做統一的話 `5'-` 與 `5-` 仍然不同，這個測試防止有人把去除改回統一。
    """
    assert "'" not in normalize_text("5'-次黃嘌呤核苷磷酸二鈉")


# 線上與評估台（sim_match.py）兩份正規化的一致性測試，2026-09-24 隨評估台移到
# FoodScanIoT-research repo，由那邊維護。


# ── 繁簡摺疊：辨識層吐簡體字時仍要配得到 ──────────────────────────────
@pytest.mark.parametrize('simplified,traditional', [
    ('柠檬酸', '檸檬酸'),
    ('L-麸酸钠', 'L-麩酸鈉'),
    ('碳酸氢钠', '碳酸氫鈉'),
])
def test_simplified_folds_to_the_same_key(simplified, traditional):
    """知識庫是繁體，而辨識層會吐簡體字（`trad-decode-finding` 記錄過混雜 33 案）。

    2026-09-12 實測，未摺疊時簡體輸入 3 個只配到 0 個。
    降階路徑走 RapidOCR，吐簡體的機率比 Gemini 高，影響更大。
    """
    assert normalize_text(simplified) == normalize_text(traditional)


def test_folding_is_present_in_the_shipped_parser():
    """防止有人把 opencc 拿掉——缺席時只印警告不會報錯，很容易沒發現。"""
    src = open(os.path.join(ROOT, 'cloud', 'module_a', 'ingredient_parser.py'),
               encoding='utf-8').read()
    assert 'OpenCC' in src and '_FOLD' in src
