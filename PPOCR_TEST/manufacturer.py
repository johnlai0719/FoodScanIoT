#!/usr/bin/env python3
"""從 OCR 全文抽出 `manufacturer`。

**為什麼現在做**：整份 JSON 裡 `name`／`brand`／`manufacturer`／
`certification_marks` 四欄至今是 0——不是做得差，是 `emit_json.build()`
直接寫 `None`，從來沒有任何一層在處理（`_meta.unfilled` 有記）。
2026-09-07 量過四欄的天花板，`manufacturer` 是最該先做的一個：

    欄位              正解有   兩讀取器聯集全文含   文字裡有錨點
    manufacturer      150        115 (76.7%)      119 (79.3%)
    name              173        137 (79.2%)      134 (77.5%)

`manufacturer` 的**錨點率幾乎等於天花板**——讀得到的案例幾乎都有
「製造商：」這種標籤可抓。`name` 有 22% 沒有錨點，那些只能靠字級大小與
位置判斷，需要座標，而 vlcrop 的輸出 **6306 行全部沒有 box**
（rec 是 HunyuanOCR，不吐框），所以 name 的後備只能走 PP-OCR。

**兩個讀取器都要餵。** 這欄印在包裝側邊、不在成分區裡，PP-OCR 讀整張圖
（89 案完全含）贏過只讀裁切區的 vlcrop（98 案，但近似較多）；
聯集 115 案。跟 `allergy.py` 相反——那欄印在成分表旁邊，vlcrop 大勝。

## 優先序（來自 `正解欄位定義.md`）

    製造商 ＞ 委製商 ＞ 負責廠商 ＞ 進口商／輸入業者 ＞ 代理商 ＞ 只列公司名者

越靠近實際生產端對食安越相關：進口商換了配方不變，製造廠換了就變了。
法規依據是食安法 §22「製造廠商**或**國內負責廠商」，兩者都列時取前者。
9 案同時列出兩種以上角色（全為進口食品），這條優先序就是為了讓它們有唯一答案。

**角色標籤本身不抄進正解**，只抄公司名。

## 三個從資料逼出來的規則

**一、「廠商地址」不是廠商。** `c122_台糖高級酥炸粉` 全文只有
「廠商地址：臺南市東區生產路68號」，抓 bare「廠商」會得到一個地址。
所以 bare 錨點後面接「地址」就不算。

**二、要在地址開始的地方切，但「台北廠」要留。**
`c08` 的正解是 `統一超食代(股)公司台北廠`，而 OCR 是
「製造廠商：統一超食代（股）公司台北廠，廠址：新北市土城區…」。
先用停止詞（廠址／地址／電話…）切掉尾巴，再取**最後一個公司字尾**收邊。

**三、`c11`／`c12` 的正解把地址黏進來了**（`屏榮食品(股)桃園市大溪區…`），
`正解欄位定義.md` 已標記待修。這支**不會**跟著黏，所以那兩案會算沒中——
那是正解的問題，修正解不是修這裡。
"""
import re

# ── 優先序 ───────────────────────────────────────────────────────────────
# 含「製造」的一律最高，這樣「製造及負責廠商」「製造廠商」都會落到第一級，
# 不必逐一列舉 OCR 可能的寫法。
TIERS = [
    # `廠` 後面接「址」的是「製造廠址：」，那是地址欄不是廠商（c150）。
    (1, re.compile(r'(?:製造|制造)(?:及負責)?(?:商|廠商|廠(?!址)|者)')),
    (2, re.compile(r'委(?:製|制|託|托)(?:商|製造|加工)')),
    (3, re.compile(r'負責廠商|負責人')),
    (4, re.compile(r'(?:進|输|輸)(?:口|入)(?:商|業者|者)')),
    (5, re.compile(r'(?:總)?代理(?:商)?')),
    (6, re.compile(r'廠商|公司名稱')),
]

# 停止詞：這些之後的字都是地址、電話或別的欄位，不屬於公司名
STOP = re.compile(
    r'廠址|地址|電話|電詁|客服|服務專線|消費者|申訴|網址|原產地|產地|'
    r'保存|有效|日期|統一編號|營養|成分|內容量|淨重|www|http|TEL|FAX|'
    r'[（(]?\d{2,4}[)）]?[-\s]?\d{6,8}', re.I)

# 地址起頭：縣市 + 幾個字之內出現區鄉鎮或路街——公司名裡的「台灣」「香港商」
# 不會命中這個組合，所以不會誤切「香港商亞洲費列羅有限公司台灣分公司」。
ADDR = re.compile(r'[縣市].{0,6}?[區鄉鎮村里]|[縣市].{0,8}?[路街道]\S*段|\d+號')

# 公司字尾：用來收邊。`廠` 要排除「廠址」「廠商」，前者已被 STOP 切掉。
SUFFIX = (
    r'(?:分)?公司|企業社|企業行|企業|商行|工作室|工廠|農場|牧場|食品廠|'
    r'株式會社|株式会社|有限会社|合同会社|'
    r'[^廠址商]廠(?![址商])|'
    r'(?:Co|Ltd|Inc|Corp|Company|Limited|Corporation|GmbH|S\.?A)\.?')

# 公司名整體：**必須從中文字開始**。只找字尾會把前面的標籤一起吃進來——
# `c106` 的 OCR 是「製造及負責廠商/Manufactured By⏎華元食品股份有限公司」，
# 從錨點往後取前綴會得到「/Manufactured By華元食品股份有限公司」。
NAME = re.compile(r'[一-鿿][一-鿿A-Za-z0-9（）()&·\.\-]{1,22}?'
                  r'(?:' + SUFFIX + r')', re.I)
_SUF = re.compile(SUFFIX, re.I)
_BREAK = re.compile(r'[\s·，,。;；/、]')

_LEAD = re.compile(r'^[\s:：·、,，.。／/|~\-─—│\]】）)]+')
_TAIL = re.compile(r'[\s:：·、,，.。／/|~\-─—│\[【（(]+$')


def _clean(s):
    s = _LEAD.sub('', s or '')
    return _TAIL.sub('', s)


def _from(text, m):
    """錨點之後把公司名切出來。切不出合格的字尾就回 None——寧可不給。

    **逐行累積再找名字**，不是一口氣抓一段。兩種相反的失敗逼出這個做法：

        c119  OCR 把名字切成兩行「黑松股」／「份有限公司中壢廠」——不接起來就抓不到
        c101  名字後面接的是成分表碎片——接太多就吃進「二氧化矽工廠」

    所以每接一行就試著收邊，**一找到公司字尾就停**，不再往後吃。
    """
    seg = _LEAD.sub('', text[m.end():m.end() + 120])
    for cut in (STOP.search(seg), ADDR.search(seg)):
        if cut:
            seg = seg[:cut.start()]
    joined = ''
    for ln in seg.split(chr(10)):
        ln = ln.strip()
        if not ln:
            continue
        joined += ln
        mm = None
        for cand in NAME.finditer(joined):
            mm = cand
        if not mm:
            continue
        end = mm.end()
        # 貪婪延伸：字尾後面**沒有分隔符**又緊接著另一個字尾，那是同一個名字
        # 的一部分（`統一超食代(股)公司台北廠` 的「台北廠」、`…有限公司台灣分公司`）。
        while True:
            nxt = _SUF.search(joined, end, end + 8)
            if not nxt or _BREAK.search(joined[end:nxt.start()]):
                break
            end = nxt.end()
        name = _clean(joined[mm.start():end])
        return name if 3 <= len(name) <= 40 else None
    return None


def _fallback(text):
    """沒有任何角色標籤時的最後一條路。

    **這條是必要的，不是保險**：150 案裡只有 79 案（52.7%）有「製造商：」
    這類標籤，其餘 71 案標示上就只印公司名。正解定義的第六順位
    「只列公司名而未標角色者」講的就是這一群。

    但它也是最容易亂給的一條——71 案裡文字中真的含有正解的只有 25 案，
    候選去重後唯一的只有 18 案。所以**只在候選唯一時才給**：多個候選時
    分不出哪個是製造商（可能是通路商、包裝商、關係企業），猜錯會在
    C 類欄位上直接記成一次錯誤，而漏給只是不加分。
    """
    seen = []
    for m in NAME.finditer(text):
        v = _clean(m.group())
        if 3 <= len(v) <= 40 and v not in seen:
            seen.append(v)
    key = {re.sub(r'[\s（）()·、,，.。股份有限]', '', v): v for v in seen}
    return list(key.values())[0] if len(key) == 1 else None


def extract_detail(lines, fallback=True):
    """回傳 `(公司名, 優先序)`。優先序 1–6 是角色標籤，**7 代表無標籤的推測**。

    合併兩個讀取器時要用這個而不是 `extract`：長度不是可信度，
    有標籤的短名字勝過沒標籤的長名字。
    """
    text = chr(10).join(l or '' for l in lines)
    for tier, rx in TIERS:
        for m in rx.finditer(text):
            if tier == 6 and text[m.end():m.end() + 3].lstrip(' :：').startswith('地址'):
                continue
            name = _from(text, m)
            if name:
                return name, tier
    if fallback:
        v = _fallback(text)
        if v:
            return v, 7
    return None, 99


def extract(lines, fallback=True):
    """`lines` 是 OCR 的行文字列。回傳公司名或 None（**不回傳空字串**）。

    抓不到回 None 是刻意的：`main.py` 用 COALESCE 寫入，空字串會被當成
    有效值蓋掉資料庫裡的舊值（同 `allergy.py` 的理由）。
    """
    return extract_detail(lines, fallback)[0]


def merge(per_reader):
    """多個讀取器的結果擇一。`per_reader` 是 `[(name, tier), ...]`。

    先比優先序（有標籤 > 無標籤），同級再取較長者——OCR 把名字截斷
    （`c119` 的「黑松股」）比多讀幾個字常見得多。
    """
    ok = [(t, v) for v, t in per_reader if v]
    return min(ok, key=lambda x: (x[0], -len(x[1])))[1] if ok else None
