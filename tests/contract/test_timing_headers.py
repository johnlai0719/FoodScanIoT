"""延遲埋點的標頭格式：**三份實作必須一致**。

同一個格式（`key=毫秒`，`;` 分隔）在三個地方各寫了一次：

    server/telemetry.py            format_timing / parse_timing   Cloud ＋ Fog Python
    fog/queryHandler.ts            fmtTiming                      Fog Node
    APP/src/utils/telemetry.ts     parseTiming                    App

⚠ 三份實作是刻意的，但很危險。Python 那份被兩層共用（Fog 會把 `server/` 接進
`sys.path`），TS 那兩份沒得共用——App 的 Metro bundler 的 projectRoot 是 `APP/`，
跨出去會打包失敗（CLAUDE.md 的 `shared/` 那條）。所以只能用測試綁住。

本專案已經因「同一份知識放兩個地方」被咬過三次（族群詞彙導致六種族群警告
從未觸發、撇號正規化分岔一天、添加物分母至今未收斂）。這是第四個同形狀的
風險，在它咬人之前先綁。

**還有一條比格式更重要**：埋點不可以傳時間戳。決策單 #15
「五段延遲的時鐘校正方式」未定，理由是跨機器相減會被時鐘偏移汙染；
所以每層只量自己行程內的時距。最後一條測試守這件事。
"""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
PY = os.path.join(ROOT, 'server', 'telemetry.py')
TS_NODE = os.path.join(ROOT, 'fog', 'queryHandler.ts')
TS_APP = os.path.join(ROOT, 'APP', 'src', 'utils', 'telemetry.ts')

import sys
sys.path.insert(0, os.path.join(ROOT, 'server'))
import telemetry as T  # noqa: E402


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


# ── 標頭名稱：四處要一字不差 ────────────────────────────────────────────────
HEADERS = ['X-Timing-Cloud', 'X-Timing-Fog-Py', 'X-Timing-Fog-Node', 'X-Request-Id']


@pytest.mark.parametrize('name', HEADERS)
def test_header_name_is_identical_everywhere(name):
    """標頭名字打錯不會報錯，只會讓那一層的數字**永遠是空的**——
    而且要等到整批量完、發現某一欄全空才發現。"""
    py = _read(PY)
    node = _read(TS_NODE)
    app = _read(TS_APP)
    hs = _read(os.path.join(ROOT, 'APP', 'src', 'screens', 'HomeScreen.tsx'))
    assert name in py, f'{name} 不在 server/telemetry.py'
    # Node 轉發用得到 Cloud／Py 的名字，自己產生 Fog-Node 那個
    assert name in node, f'{name} 不在 fog/queryHandler.ts'
    # App 端在畫面裡讀標頭、在 utils 裡解析
    assert name in app or name in hs, f'{name} 不在 App'


# ── 格式：分隔符與 key=value 的寫法 ─────────────────────────────────────────
def test_python_roundtrip():
    src = {'vision': 10412, 'diagnosis': 2336, 'total': 13150}
    assert T.parse_timing(T.format_timing(**src)) == src


def test_none_is_omitted_not_written_as_zero():
    """「這一段沒跑」與「這一段 0 毫秒」不同。降階時 vision 根本沒跑，
    寫成 0 會讓中位數被拉低而看不出來。"""
    out = T.format_timing(vision=None, total=100)
    assert 'vision' not in out
    assert T.parse_timing(out) == {'total': 100}


def test_typescript_uses_the_same_separators():
    node = _read(TS_NODE)
    assert "join(';')" in node, 'Node 的分隔符不是 ;'
    assert '${k}=${' in node, 'Node 的 key=value 寫法不符'
    app = _read(TS_APP)
    assert "split(';')" in app, 'App 解析用的分隔符不是 ;'


def test_typescript_also_omits_null_instead_of_zero():
    """與 test_none_is_omitted_not_written_as_zero 是同一條規則的 TS 側。"""
    node = _read(TS_NODE)
    assert re.search(r'filter\(\(\[, v\]\) => v !== null', node), \
        'Node 沒有濾掉 null 的段落——會把「沒跑」寫成 0'


def test_parse_is_tolerant_of_garbage():
    """埋點壞了不該讓分析失敗。壞段落丟掉，不拋例外。"""
    assert T.parse_timing('vision=abc;total=100') == {'total': 100}
    assert T.parse_timing('') == {}
    assert T.parse_timing(None) == {}


# ── request id：會被原樣轉發，所以必須過濾 ──────────────────────────────────
@pytest.mark.parametrize('bad', [
    'a\r\nX-Injected: 1',      # 標頭注入
    'a' * 65,                  # 過長
    '有中文',                   # 標頭不可放非 ASCII
    '',
    None,
    123,
])
def test_bad_request_ids_are_rejected(bad):
    assert T.safe_request_id(bad) == ''


def test_app_generated_ids_are_accepted():
    """App 產生的形狀（`app-<base36>-<rand>`）必須通得過 Cloud 的過濾，
    否則三層的紀錄併不起來——而且不會有任何錯誤訊息。"""
    for sample in ['app-m0abc12-x7k9qp2f', 'app-1-a']:
        assert T.safe_request_id(sample) == sample


# ── 最重要的一條：不可傳時間戳 ──────────────────────────────────────────────
def test_no_wall_clock_timestamps_in_the_timing_path():
    """決策單 #15：跨機器相減會被時鐘偏移汙染，所以只傳時距。

    `Stopwatch` 必須用單調時鐘。`time.time()` 會被 NTP 校時往前或往後跳
    （Pi 開機後校時是常態），量到負值或暴增的區間。
    """
    py = _read(PY)
    assert 'time.monotonic' in py
    assert 'time.time()' not in py, \
        'server/telemetry.py 不該用 time.time()——它會被校時跳動'


def test_app_records_only_its_own_clock_difference():
    """App 的 total_ms 必須是自己兩次 Date.now() 相減，
    不可與伺服器回來的任何時刻比對。"""
    hs = _read(os.path.join(ROOT, 'APP', 'src', 'screens', 'HomeScreen.tsx'))
    assert 'total_ms: Date.now() - t0' in hs


def _strip_comments(src):
    """去掉 // 與 /* */ 註解。不這樣做的話「註解裡提到 base64」會讓下面
    那條測試誤判——第一版就是這樣紅的。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def test_records_counts_not_contents():
    """紀錄會被匯出分享，所以不放照片與成分原文，只放「有幾項」。

    檢查的是**實際寫進紀錄的欄位**（HomeScreen 那個 append 呼叫與
    ScanRecord 的欄位宣告），不是整份檔案的字串——註解裡提到什麼無所謂。
    """
    app = _strip_comments(_read(TS_APP))
    hs = _read(os.path.join(ROOT, "APP", "src", "screens", "HomeScreen.tsx"))
    m = re.search(r"Telemetry\.append\(\{(.*?)\n      \}\)", hs, re.S)
    assert m, "在 HomeScreen 找不到 Telemetry.append({...}) 呼叫"
    appended = _strip_comments(m.group(1))

    assert "n_additives" in app and "n_additives" in appended
    for leaky in ["ingredients_raw", "ingredients_list", "label_images",
                  "nutrition"]:
        assert leaky not in appended, "量測紀錄不該含 %s（會被匯出分享）" % leaky
        assert leaky not in app, "ScanRecord 不該宣告 %s" % leaky

    # 圖片本身不可進紀錄，但「有幾張」可以。所以 uploadedImages 只准以
    # `.length` 出現——`n_images: uploadedImages.length` 是對的，
    # `images: uploadedImages` 就是把整包 base64 存進去。
    for m2 in re.finditer(r"uploadedImages(\.\w+)?", appended):
        assert m2.group(1) == ".length", (
            "量測紀錄只能記圖片張數，不能記圖片本身：%s" % m2.group(0))


# ── request id 要真的逐層轉發，不只是名字出現在檔案裡 ────────────────────────
# 2026-09-13 實測：X-Request-Id 在 queryHandler.ts 裡只出現在「從 Python 回應複製」
# 的清單中，App 送的 id 在 Node 就被丟掉，三層的紀錄併不起來——而上面那條
# 「名字要一致」的測試照樣是綠的。

def test_node_passes_request_id_into_handle_query():
    server = _read(os.path.join(ROOT, 'fog', 'server.ts'))
    assert re.search(r"handleQuery\([^)]*X-Request-Id", server), \
        'server.ts 沒把請求的 X-Request-Id 交給 handleQuery()'


def test_node_forwards_request_id_on_every_call_to_python():
    node = _read(TS_NODE)
    n_fetch = node.count('fetch(CLOUD_API_URL')
    assert n_fetch >= 1
    assert node.count('headers: upstreamHeaders') == n_fetch, \
        'queryHandler.ts 有打 Python 的 fetch 沒帶 upstreamHeaders（其中含 X-Request-Id）'
    m = re.search(r"const upstreamHeaders[^;]*;", node, re.S)
    assert m and 'X-Request-Id' in m.group(0)


def test_python_forwards_request_id_on_every_call_to_cloud():
    main = _read(os.path.join(ROOT, 'fog', 'main.py'))
    assert 'headers=cloud_headers()' not in main, \
        'fog/main.py 有打 Cloud 的呼叫只用 cloud_headers()，沒帶 request id'
    assert main.count('_h[T.REQUEST_ID_HEADER] = rid') == 2, \
        '帶圖與無圖兩條打 Cloud 的路徑都要轉發 request id'


def test_node_and_python_accept_the_same_request_ids():
    """Node 先過濾再轉發；兩邊規則不同時，合格的 id 可能在 Node 就被擋掉。"""
    node = _read(TS_NODE)
    m = re.search(r"const SAFE_REQUEST_ID = /(.+)/;", node)
    assert m, 'queryHandler.ts 找不到 SAFE_REQUEST_ID'
    assert m.group(1) == T._SAFE_ID.pattern


# ── 測試模式（X-Bypass-Cache）────────────────────────────────────────────────
def test_bypass_uses_a_dedicated_header_not_cache_control():
    """**不可以沿用 `Cache-Control: no-cache`。**

    App 每一次請求都送那個標頭（HomeScreen 的 fetch 裡固定有），
    拿它當跳過快取的判準等於永久關閉快取——那就量不出「Fog 的快取
    有沒有幫上忙」，而那正是這個切換鈕要服務的實驗。
    """
    node = _read(TS_NODE)
    server = _read(os.path.join(ROOT, "fog", "server.ts"))
    assert "X-Bypass-Cache" in server, "fog/server.ts 沒有讀 X-Bypass-Cache"
    assert "bypassCache" in node
    # ⚠ 去註解再檢查。寫「不該用 Cache-Control」這種說明本身會讓這條紅掉
    #   ——同一個錯誤在本檔與 test_degraded_local_contract 已經各犯過一次。
    code = _strip_comments(server)
    assert "Cache-Control" not in code,         "fog/server.ts 不該拿 Cache-Control 當跳過快取的判準"


def test_bypass_skips_reading_but_still_writes():
    """跳過**讀**、照常**寫**。關掉寫入的話快取本身就量不出來了。"""
    node = _read(TS_NODE)
    assert "bypassCache ? null : getCache(barcode)" in node,         "跳過快取的實作方式變了，請確認它只影響讀取"
    # setCache 不得被 bypassCache 包住
    for line in node.split(chr(10)):
        if "setCache(" in line:
            assert "bypass" not in line, "寫入快取不該受 bypassCache 影響"


def test_telemetry_records_whether_bypass_was_on():
    """不記的話資料會混著命中快取與完整路徑，中位數不代表任何一種。"""
    app = _read(TS_APP)
    hs = _read(os.path.join(ROOT, "APP", "src", "screens", "HomeScreen.tsx"))
    assert "bypass_cache" in app, "ScanRecord 沒有宣告 bypass_cache"
    assert "bypass_cache: bypassCache" in hs, "紀錄沒有帶上 bypass_cache"
