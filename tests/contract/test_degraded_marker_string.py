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

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
MARKER = '診斷引擎暫時降級運作。'

def _strip_comments(src):
    """掃描原始碼前先去註解。

    這個檔案守的字串本身就寫在各處的說明文字裡，不去掉的話會掃到註解而
    假通過。本專案已因此誤判過三次。
    """
    src = re.sub(r'"""' + r'.*?' + r'"""', '', src, flags=re.S)
    src = re.sub(r'/' + r'\*.*?\*' + r'/', '', src, flags=re.S)
    src = re.sub(r'^\s*#.*$', '', src, flags=re.M)
    return re.sub(r'^\s*//.*$', '', src, flags=re.M)


def test_fog_still_recognises_the_marker_for_old_cached_results():
    """Fog 的守門一字不可改——改了就認不出舊快取裡帶標記的結果。"""
    src = _strip_comments(
        open(os.path.join(ROOT, 'fog/queryHandler.ts'), encoding='utf-8').read())
    assert src.count(MARKER) >= 1, (
        'queryHandler.ts 找不到降級標記。資料庫與 Fog 快取裡可能還有舊版'
        '產生、帶著這個標記的結果，拿掉守門會讓那句話被重新快取後繼續服務。')


def test_cloud_no_longer_produces_the_marker():
    """Cloud 不可以把它加回來。

    它出現在 Cloud 端只有一種成因：有人又接了會失敗的外部呼叫去產生總結。
    總結是衍生值——由 score_breakdown 與添加物清單算出來——沒有可失敗的
    外部相依，也就沒有「降級的診斷」這種狀態。
    """
    for rel in ('server/module_d/diagnosis.py', 'server/module_d/summary.py',
                'server/module_d/response_builder.py'):
        src = _strip_comments(
            open(os.path.join(ROOT, rel), encoding='utf-8').read())
        assert MARKER not in src, (
            '%s 又出現降級標記。總結若需要「失敗時的替代文字」，'
            '代表它依賴了不該依賴的外部呼叫。' % rel)


def test_degraded_results_are_not_cached_by_status_either():
    """除了字串，Node 層還靠 `status === 'success'` 擋一道。

    Fog 本機降階（`degraded_mode: "local_ocr"`）的 status 是 `degraded`，
    所以就算字串比對失效，它仍然不會被快取。這條測試把那道**第二層防護**
    也記錄下來——它是目前唯一不依賴中文字串的保護。
    """
    ts = open(os.path.join(ROOT, 'fog/queryHandler.ts'), encoding='utf-8').read()
    assert "status === 'success'" in ts or 'status === "success"' in ts
