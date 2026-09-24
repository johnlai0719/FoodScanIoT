"""五段延遲的埋點：**每一層只量自己行程內的耗時，用 HTTP 標頭往回帶。**

為什麼不傳時間戳
--------------
決策單 #15「五段延遲的時鐘校正方式」至今未定，理由是**跨機器相減會被時鐘
偏移汙染**——手機、Pi、雲端主機三個時鐘，任兩個相減出來的數字都含未知偏移，
而偏移可能比要量的延遲本身還大。

這裡的作法繞過整個問題：每層用自己的單調時鐘量「我這段花多久」，把**時距**
（不是時刻）放進標頭。時距不需要校正。代價是量不到「純網路傳輸時間」，
只能用相鄰兩層的差推估（A 層總時間 − B 層總時間 = 網路 ＋ A 層自身開銷），
那個推估**仍然無需時鐘校正**。

為什麼走標頭不走 JSON
--------------------
`tests/contract/test_cloud_response_contract.py` 是
`set(response.keys()) == EXPECTED_TOP_LEVEL_KEYS`（嚴格相等），在 body 加欄位
會讓契約測試紅、也等於改了 App 的資料契約。標頭不動契約，而 Fog 本來就在用
（`X-Cache`）。降階與錯誤回應也一樣帶得到標頭——那正是最需要量的那些次。

放在哪裡
--------
在 `server/` 而不是 `fog/`：Cloud 的 Dockerfile 是 `COPY . .`、context 為
`./server`，放在 fog 底下容器裡就 import 不到。Fog 反過來沒問題，它本來就會
把 `server/` 接進 `sys.path`（見 `fog/local_ocr.py` 用 `module_a` 的方式）。

格式
----
`key=毫秒` 用 `;` 分隔，ASCII only（標頭不可放中文）：

    X-Timing-Cloud:   vision=10412;diagnosis=2336;total=13150
    X-Timing-Fog-Py:  upstream=13200;localocr=0;total=13910
    X-Timing-Fog-Node: upstream=13950;total=14100

`X-Request-Id` 由 App 產生並逐層轉發，用來把三層的紀錄併成一列。
"""
import re
import time

REQUEST_ID_HEADER = "X-Request-Id"
CLOUD_HEADER = "X-Timing-Cloud"
FOG_PY_HEADER = "X-Timing-Fog-Py"
FOG_NODE_HEADER = "X-Timing-Fog-Node"

# 標頭值只允許這些字元。**不是潔癖**：request id 來自 App，會被原樣轉發到
# Cloud，未過濾的字串可以夾帶 CRLF 造成標頭注入。
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def safe_request_id(value) -> str:
    """不合格就回空字串——寧可少一個關聯鍵，不要把使用者輸入原樣塞進標頭。"""
    if isinstance(value, str) and _SAFE_ID.match(value):
        return value
    return ""


def format_timing(**segments) -> str:
    """`vision=10412;diagnosis=2336`。值為 None 的段落略過，不寫成 0——
    「沒有這一段」與「這一段是 0 毫秒」是兩件事（降階時 vision 根本沒跑）。"""
    parts = []
    for k, v in segments.items():
        if v is None:
            continue
        parts.append("%s=%d" % (k, round(float(v))))
    return ";".join(parts)


def parse_timing(value: str) -> dict:
    """標頭字串 → dict。壞掉的段落丟掉而不是拋例外：埋點壞了不該讓分析失敗。"""
    out = {}
    for part in (value or "").split(";"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        try:
            out[k] = int(v.strip())
        except ValueError:
            continue
    return out


class Stopwatch:
    """量時距用。`time.monotonic` 而不是 `time.time`——後者會被 NTP 校時
    往前或往後跳，量到負數或暴增的區間（Pi 開機後校時是常態）。"""

    def __init__(self):
        self._t0 = time.monotonic()
        self._marks = {}

    def lap(self, name):
        """回傳一個 context manager，記錄這段的毫秒數。"""
        return _Lap(self, name)

    def _record(self, name, ms):
        self._marks[name] = self._marks.get(name, 0) + ms

    def ms(self, name=None):
        if name is None:
            return (time.monotonic() - self._t0) * 1000.0
        return self._marks.get(name)

    def segments(self, **extra):
        d = dict(self._marks)
        d.update({k: v for k, v in extra.items() if v is not None})
        d["total"] = self.ms()
        return d


class _Lap:
    def __init__(self, sw, name):
        self._sw, self._name = sw, name

    def __enter__(self):
        self._t = time.monotonic()
        return self

    def __exit__(self, *exc):
        self._sw._record(self._name, (time.monotonic() - self._t) * 1000.0)
        return False   # 不吞例外：埋點不該改變錯誤流程
