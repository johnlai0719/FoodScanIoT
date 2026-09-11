"""「診斷引擎暫時降級運作。」這個字串是跨層協定，改一處就會靜默失效。

它同時被三個地方用來判斷「這份結果是不是降級產出的」：

    server/module_d/diagnosis.py:71   決定要不要重用資料庫裡的舊摘要
    server/module_d/diagnosis.py:120  產生它
    server/module_d/diagnosis.py:133  決定要不要寫回資料庫
    fog/queryHandler.ts               決定要不要寫入 Fog 快取

**跨兩層、兩種語言、四個地方比對同一個中文常數。** 任何一處改了字（包括
改標點、把句號拿掉），其餘幾處會安靜地不再認得它——降級結果就會被當成
正常結果寫進快取，然後一路服務到 TTL 到期。

這與族群詞彙那次是同一種風險（`test_group_vocabulary.py`：詞彙散在四處
各自維護，導致六種族群警告從未觸發）。所以照同樣的方式鎖住。

⚠ 這不是在說現在壞了——現在四處是一致的。這是在防止下一次。
"""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
MARKER = '診斷引擎暫時降級運作。'

SITES = [
    ('server/module_d/diagnosis.py', 3),   # 產生 1 次、比對 2 次
    ('fog/queryHandler.ts', 1),
]


@pytest.mark.parametrize('rel,least', SITES)
def test_marker_present_in_every_layer(rel, least):
    p = os.path.join(ROOT, rel)
    src = open(p, encoding='utf-8').read()
    n = src.count(MARKER)
    assert n >= least, (
        '%s 只出現 %d 次（預期至少 %d）。若是刻意改了字串，'
        '其餘各層也要一起改，否則降級結果會被當成正常結果快取。' % (rel, n, least))


def test_typescript_and_python_use_the_identical_string():
    """逐字相同，包含句號。曾經有人只改標點就讓比對失效的類似前例。"""
    py = open(os.path.join(ROOT, 'server/module_d/diagnosis.py'),
              encoding='utf-8').read()
    ts = open(os.path.join(ROOT, 'fog/queryHandler.ts'), encoding='utf-8').read()
    py_lits = set(re.findall(r'["\']([^"\']*降級運作[^"\']*)["\']', py))
    ts_lits = set(re.findall(r'["\']([^"\']*降級運作[^"\']*)["\']', ts))
    assert py_lits, '在 diagnosis.py 找不到降級標記字串'
    assert ts_lits, '在 queryHandler.ts 找不到降級標記字串'
    assert py_lits == ts_lits, (
        '兩層的降級標記不一致：\n  Python %s\n  TypeScript %s' % (py_lits, ts_lits))


def test_degraded_results_are_not_cached_by_status_either():
    """除了字串，Node 層還靠 `status === 'success'` 擋一道。

    Fog 本機降階（`degraded_mode: "local_ocr"`）的 status 是 `degraded`，
    所以就算字串比對失效，它仍然不會被快取。這條測試把那道**第二層防護**
    也記錄下來——它是目前唯一不依賴中文字串的保護。
    """
    ts = open(os.path.join(ROOT, 'fog/queryHandler.ts'), encoding='utf-8').read()
    assert "status === 'success'" in ts or 'status === "success"' in ts
