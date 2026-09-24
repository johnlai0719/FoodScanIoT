"""「只用本機辨識」的跨層契約。

使用者可在 App 設定中選擇只用邊緣節點辨識，照片不轉送雲端。它與「雲端掛了
才降階」走**同一條產出路徑**（local_ocr），但觸發來源不同，而那個差別決定了
兩件事不可以做：

1. **不可讀快取。** 快取裡是雲端算過的完整結果。拿它回應等於沒有照使用者的
   選擇做，而畫面會顯示成完整分析，使用者無從察覺照片其實沒有被重新辨識、
   或者上一次其實送過雲端。

2. **本機辨識未就緒時不可退回雲端。** 那會把照片送出去，正好是使用者選這個
   選項要避免的事。必須明確回報錯誤。

這兩條都是「靜默做了使用者沒同意的事」，與個人化資料不離開裝置是同一類界線。
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
HEADER = "X-Local-Only"


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _strip_comments(src):
    """掃描原始碼前先去註解。

    本檔與實作的註解都會提到這些字串，不去掉會掃到自己寫的說明而假通過。
    本專案已因此誤判過三次。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r'"""' + r".*?" + r'"""', "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return re.sub(r"^\s*#.*$", "", src, flags=re.M)


# ── 標頭要穿過三層 ──────────────────────────────────────────────────────────
def test_app_sends_the_header_when_the_option_is_on():
    src = _strip_comments(_read("APP", "src", "screens", "HomeScreen.tsx"))
    assert HEADER in src, "App 沒有送出 %s" % HEADER
    assert "localOnly" in src, "App 沒有這個選項的狀態"


def test_fog_node_reads_and_forwards_the_header():
    server = _strip_comments(_read("fog", "server.ts"))
    handler = _strip_comments(_read("fog", "queryHandler.ts"))
    assert HEADER in server, "fog/server.ts 沒有讀 %s" % HEADER
    assert HEADER in handler, "fog/queryHandler.ts 沒有把標頭往下帶"


def test_fog_python_acts_on_the_header():
    src = _strip_comments(_read("fog", "main.py"))
    assert HEADER in src, "fog/main.py 沒有讀 %s" % HEADER
    assert "local_ocr.analyze" in src


# ── 不可讀快取 ──────────────────────────────────────────────────────────────
def test_node_does_not_serve_cache_in_local_only_mode():
    """Node 層是第一道。"""
    node = _strip_comments(_read("fog", "queryHandler.ts"))
    line = [l for l in node.split("\n") if "getCache(barcode)" in l]
    assert line, "找不到讀取快取的那一行"
    assert any("localOnly" in l for l in line), \
        "只用本機辨識時仍會讀快取——那會回傳雲端算過的完整結果"


def test_python_does_not_fall_through_to_the_cloud_branch():
    """Python 層是第二道。

    處理必須發生在轉發雲端的那段之前並直接回傳，否則兩層任一處漏掉，
    照片就會被送出去。
    """
    src = _strip_comments(_read("fog", "main.py"))
    i = src.find(HEADER)
    assert i > 0, "定位失敗"
    # 不可比對「檔案裡第一個 requests.post」——第一個是健康檢查用的，
    # 位置在處理函式之前，比了永遠會紅（2026-09-17 第一版就是這樣錯的）。
    # 要守的是：這個分支在走到轉發之前就 return 了。
    j = src.find("requests.post(", i)
    assert j > i, "找不到本機分支之後的雲端轉發"
    seg = src[i:j]
    assert "return await run_in_threadpool" in seg,         "只用本機辨識的分支沒有在轉發雲端之前回傳"


# ── 未就緒時不可退回雲端 ────────────────────────────────────────────────────
def test_not_ready_returns_an_error_instead_of_using_the_cloud():
    """回 503 而不是改送雲端。

    ⚠ 這一條最容易在日後被「改善容錯」的名義改掉。退回雲端會讓照片在使用者
    明確選擇不外送的情況下離開裝置，那不是容錯，是違反使用者的選擇。
    """
    src = _strip_comments(_read("fog", "main.py"))
    i = src.find(HEADER)
    seg = src[i:i + 1200]
    assert "is_ready()" in seg, "沒有檢查本機 OCR 是否就緒"
    assert "503" in seg, "未就緒時沒有回報錯誤"
    assert "requests.post" not in seg, "未就緒時仍可能走到雲端轉發"


# ── 結果要標示成因 ──────────────────────────────────────────────────────────
def test_app_distinguishes_user_choice_from_cloud_failure():
    """同一個畫面、兩種成因，文案不可共用。

    使用者自己選的模式若用故障的語氣呈現（「離線模式」），會被讀成系統出問題。
    """
    src = _strip_comments(_read("APP", "src", "screens", "HomeScreen.tsx"))
    assert "resultWasLocalOnly" in src, "沒有記住這次結果是不是本機模式產生的"
    assert "本機辨識" in src, "結果頁沒有區分本機模式的標題"
    assert "離線模式" in src, "雲端故障那一種的文案不該一併移除"


def test_the_option_states_what_is_missing():
    """設定頁必須寫明少了什麼。

    本機辨識沒有健康評分與添加物比對。不講的話使用者會以為結果只是慢一點。
    """
    src = _read("APP", "src", "screens", "HomeScreen.tsx")
    # 錨在**設定頁的標題**上。只找「只用本機辨識」會命中檔案上方的註解，
    # 那裡當然沒有能力說明（2026-09-17 第一版就是這樣錯的）。
    i = src.find("]}>只用本機辨識")
    assert i > 0, "設定頁找不到這個選項"
    seg = src[i:i + 900]
    assert "健康評分" in seg and "添加物" in seg, \
        "設定說明沒有講明本機模式缺少哪些能力"
