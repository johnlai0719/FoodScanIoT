"""
Module A — 成分原文解析（切段 → 巢狀括號樹 → 待比對項目）

為何需要（2026-07-30，以 48 個真實案例實測）：辨識層其實把成分欄位的原文完整
抄下來了，括號與巢狀結構都在，是**模型自己在整理成清單時把細項收掉了**——
48 案的原文可拆出 1,245 項，模型給的清單只剩 755 項。最極端的一案（吉士豬肉堡）
原文有三層巢狀、寫了幾十種成分，清單只留下麵包、豬肉漢堡肉、起司片等 7 項，
複合食品裡明明寫出來的添加物（如麵糰改良劑內的維生素C、起司片內的己二烯酸鉀）
整個消失。

因此本模組改成不吃模型整理過的清單，直接從原文自己拆。這樣不必改 prompt、
不必重跑辨識、不花 API 費用，資料本來就在。

三個階段各自獨立、可分別驗證：
  1. extract_section  — 從整段標示文字中切出「成分欄位」那一段
  2. parse_tree       — 把巢狀括號解析成樹（容錯：括號不配對也要能解析）
  3. flatten_items    — 依括號語意判定表，把樹攤平成「該送去比對的項目」

本模組刻意不碰資料庫。判斷某個詞是不是類別統稱需要官方類別清單，那份清單存在
資料庫裡，故以 is_generic 參數注入（見 flatten_items）；未注入時退回內建的
標示慣用寫法，功能降級但不中斷，離線回測即靠這條路徑。

文字正規化的原始碼原本在 ingredient_matching.py，2026-07-30 移到此處——比對端與
解析端必須用同一套正規化，否則排版差異會讓同一物質對不上（見 normalize_text）。
ingredient_matching.py 改為從本模組 import，對外行為不變。
"""
import re


# ---------------------------------------------------------------- 文字正規化

# 排版用的引號／連字號等，需統一成 ASCII 形式才比對得到。
# 全形轉半形只涵蓋 U+FF01–FF5E，不含這些字元（2026-07-26 實測：添加物資料庫
# 存的是「5’-次黃嘌呤核苷磷酸二鈉」（彎引號 U+2019），標示上寫的是
# 「5'-次黃嘌呤核苷磷酸二鈉」（直引號 U+0027），兩者永遠對不上，導致整組
# 核苷酸類調味劑配不到，還被「磷酸氫二鈉」以片段吻合撿走）。
_PUNCT_CANON = {
    "‘": "'", "’": "'", "ʼ": "'", "´": "'", "`": "'",
    "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}


def normalize_text(text: str) -> str:
    if not text: return ""
    text = "".join([chr(ord(c) - 0xfee0) if 0xff01 <= ord(c) <= 0xff5e else c for c in text])
    text = "".join(_PUNCT_CANON.get(c, c) for c in text)
    return re.sub(r'\(.*?\)|（.*?）|\s+', '', text)


# 涵蓋圓括號、全形括號與大括號——實測標示上三種都會出現
# （「雞肉{雞肉, 糖, 辣椒, …}」即為大括號寫法）。
_PAREN_RE = re.compile(r"[（({\[]([^（()）{}\[\]]*)[）)}\]]")


def strip_brackets(text: str) -> str:
    """
    反覆移除括號內容，直到沒有括號為止。

    _PAREN_RE 的字元類刻意排除括號，故單次 sub 只能拿掉**最內層**；遇到巢狀
    （如「海苔粉[…乳化劑(脂肪酸甘油酯)…]」）外層會整段留下，拿去比對就會配到
    括號內的添加物（2026-07-26 實測誤配來源）。重複套用即可由內而外剝乾淨。
    """
    prev = None
    while prev != text:
        prev = text
        text = _PAREN_RE.sub("", text)
    return text


# ------------------------------------------------------- 階段一：切出成分段落

# 成分欄位的起始標記。冒號可有可無——實測有標示寫「成份米、肉鬆(…)」直接接內容。
_START_RE = re.compile(r"(成\s*分|成\s*份|原\s*料|配\s*料)(?:表)?\s*[:：]?\s*")

# 起始標記前方允許出現的字元。這個限制是必要的（2026-07-30 實測）：
# 有標示把過敏原說明放在成分後面的括號裡——「水、生奶、…、脂肪酸甘油酯。
# (過敏原資訊:本產品含有奶類成分)」，那個「成分」若被當成起始標記，整張成分表
# 會被切掉只剩尾巴。要求標記前必須是欄位邊界（開頭、項目符號、空白、換行、
# 右括號），即可排除夾在中文句子裡的「成分」二字。
_START_BOUNDARY = set(" \t\r\n●•·※*)）]】>-—│|")

# 成分欄位之後才會出現的欄位。掃到其中最早出現的一個就截斷——後面全是重量、
# 保存方式、過敏原警語等，不是成分。
_END_MARKERS = [
    "淨重", "總重", "重量", "內容量", "容量",
    "有效期限", "有效日期", "保存期限", "保存條件", "保存方法", "保存方式",
    "製造日期", "包裝日期", "生產日期",
    "營養標示", "熱量",
    "咖啡因含量", "本品含咖啡因", "本產品含咖啡因",
    "過敏原", "本產品含", "本產品與", "本產品為", "本產品經",
    "食用方法", "食用前", "注意事項", "警語",
    "廠商", "製造商", "委製", "委託", "地址", "電話", "服務專線", "客服",
    "原產地", "產地",
    "素食", "全素", "葷食",
]
_END_RE = re.compile("|".join(re.escape(m) for m in _END_MARKERS))

# 「豬肉來源:加拿大」這類產地補述。要求後面接冒號，才不會誤砍
# 「麥芽糊精(玉米來源)」這種寫在括號內的原料說明。
_SOURCE_RE = re.compile(r"[^、,，。;；]{0,6}來源\s*[:：]")

# 開頭的標示欄位（品名、日期等）。沒有成分標記時要先把這些剝掉，否則
# 「品名:X 製造日期:Y 麵(高筋麵粉、…)」會把品名和日期當成兩項成分
# （2026-07-30 實測 c35 涼麵即為此情形）。
_LEADING_FIELD_RE = re.compile(
    r"^\s*[●•·※*]*\s*(?:品名|產品名稱|商品名稱|製造日期|有效日期|有效期限|保存期限|"
    r"保存條件|淨重|總重|內容量|廠商|製造商|地址|電話|原產地|產地)\s*[:：]?[^\s]*\s*")
# 剝完欄位名後可能殘留的時間／數量碎片（如「有效日期:2026.07.02 17時」的「17時」）。
_LEADING_JUNK_RE = re.compile(
    r"^\s*(?:[●•·※*、,，]|\d+\s*(?:時|點|分|秒|年|月|日|公克|克|kg|g|毫升|公升|ml|L)"
    r"|\d{2,4}[./\-]\d{1,2}(?:[./\-]\d{1,2})?)\s*", re.I)

# 夾在成分段裡的說明性括號。這些不是成分，是警語或製程說明，
# 留著會被當成一項成分去比對（實測「(過敏原資訊:本產品含有奶類成分)」）。
_NOTE_KEYWORDS = ("過敏", "同一工廠", "同一產線", "同一生產", "生產製程", "請留意",
                  "請避免", "食物過敏", "本產品", "本品", "警語", "請小心",
                  "開封", "冷藏", "冷凍", "保存",
                  # 產地補述（「牛肉(原產地：尼加拉瓜、澳洲、紐西蘭)」）。不移除的話
                  # 括號內有三項，會被判成複合食品而把三個國名列成成分。
                  "原產地", "產地", "來源")
_NOTE_PAREN_RE = re.compile(r"[（(]([^（()）]*)[）)]")

# 整段只是過敏原警語的判斷依據（2026-07-30 實測 c42 蔥鹽炙燒豬肉便當：
# 原文從頭到尾都是「本產品含麩質之穀物(來自飯體麵)、大豆…及其製品。豬肉來源:加拿大」，
# 一項成分都沒有，模型卻把六個過敏原當成成分填進清單。這是把警語讀成成分，
# 比漏抓嚴重——使用者會看到一份完全錯的成分表）。
_ALLERGEN_ONLY_RE = re.compile(r"(本產品|本品|產品).{0,10}(含|使用).{0,60}(及其製品|過敏)")


def _drop_note_parens(text: str) -> str:
    """移除說明性括號（過敏原、製程、保存方式），保留成分用的括號。"""
    def repl(m):
        return "" if any(k in m.group(1) for k in _NOTE_KEYWORDS) else m.group(0)
    return _NOTE_PAREN_RE.sub(repl, text)


def _find_at_depth0(text: str, pattern: re.Pattern) -> int:
    """
    找出 pattern 第一個**不在括號內**的出現位置，找不到回傳 len(text)。

    只認括號外的（2026-07-30 實測）：滿漢大餐拌麵寫「牛肉(原產地：尼加拉瓜、
    澳洲、紐西蘭)、水、酸菜(…)…」，「原產地」寫在括號裡是這項牛肉的補述，
    不是成分欄位結束。不看深度就會在此截斷，44 項只剩 12 項。
    """
    depth = 0
    ends = {m.start(): m for m in pattern.finditer(text)}
    for i, ch in enumerate(text):
        if depth == 0 and i in ends:
            return i
        if ch in _OPEN_CHARS:
            depth += 1
        elif ch in _CLOSE_CHARS:
            depth = max(0, depth - 1)
    return len(text)


def _strip_leading_fields(text: str) -> str:
    """反覆剝除開頭的標示欄位與殘留碎片，直到剝不動為止。"""
    prev = None
    while prev != text:
        prev = text
        text = _LEADING_FIELD_RE.sub("", text)
        text = _LEADING_JUNK_RE.sub("", text)
    return text


def extract_section(raw: str) -> tuple[str, str | None]:
    """
    從標示原文中切出成分欄位那一段。

    回傳 (成分段文字, 取不到時的原因)。原因為 None 表示成功。
    刻意回傳原因而非空字串了事：「照片上沒有成分欄位」與「有成分但解析失敗」
    對使用者的意義完全不同，呈現端要能分辨。
    """
    if not raw or not raw.strip():
        return "", "empty"
    text = raw.strip()

    # 一、先找成分欄位的起始標記，**必須先做這一步**（2026-07-30 修）。
    #     順序反了會出事：若先砍結尾欄位，「品名:X 製造日期:Y 麵(高筋麵粉…」
    #     會在「製造日期」處被截斷，成分一項都不剩；「(本產品為辣味) 成分:…」
    #     則在第 1 個字就被「本產品」砍掉。結尾欄位只有在成分開始之後才算結尾。
    #     標記須位於欄位邊界（見 _START_BOUNDARY），且取第一個有效者。
    head = None
    for m in _START_RE.finditer(text):
        if m.start() == 0 or text[m.start() - 1] in _START_BOUNDARY:
            if len(text) - m.end() >= 4:
                head = text[m.end():]
                break
    if head is None:
        # 沒有成分標記（很多飲料直接列成分）→ 開頭可能是品名、日期等欄位，先剝掉
        head = _strip_leading_fields(text)

    # 二、再截掉成分之後的欄位（重量、保存、過敏原…），只認括號外的
    head = head[:min(_find_at_depth0(head, _END_RE),
                     _find_at_depth0(head, _SOURCE_RE))]

    head = _drop_note_parens(head).strip(" 　\t\r\n。，,、;；:：●•·※*")

    if not head:
        # 截斷後什麼都不剩：若原文本身就是一段過敏原警語，如實回報，
        # 不可退回去用模型清單——那份清單裡裝的就是被誤讀的過敏原。
        if _ALLERGEN_ONLY_RE.search(text):
            return "", "allergen_warning_only"
        return "", "no_section"
    return head, None


# ------------------------------------------------- 階段二：巢狀括號 → 樹狀結構

_OPEN_CHARS = "（({[［｛"
_CLOSE_CHARS = "）)}]］｝"
_SEP_CHARS = "、,，;；/。"


def _new_node(name: str, children: list) -> dict:
    return {"name": name.strip(), "children": children}


def _flush(nodes: list, name_buf: list, pending: dict | None) -> None:
    """把累積中的文字收成一個節點，或併入剛吃完括號的節點。"""
    text = "".join(name_buf).strip()
    if not text:
        return
    if pending is not None:
        # 括號後面還跟著文字（如「維生素E(抗氧化劑)等」），視為同一項的一部分
        pending["name"] = (pending["name"] + text).strip()
    else:
        nodes.append(_new_node(text, []))


def _parse_level(s: str, i: int) -> tuple[list, int]:
    """
    解析同一層的兄弟節點，回傳 (節點清單, 下一個位置)。

    容錯是硬需求，不是加分項（2026-07-30 實測）：辨識出來的原文括號經常不配對——
    c35 涼麵有一個多出來的右括號「黃梔子色素(麥芽糊精、黃梔子色素、水))」，
    c09 麻婆豆腐燴飯的「辣豆瓣醬(…)」提早關閉、後面還孤零零留著「八角)」。
    嚴格的文法解析在這裡會整段失敗，反而不如原本的做法；故多出來的右括號直接
    收掉當作本層結束，到結尾還沒關的括號則自動補上。
    """
    nodes: list = []
    name_buf: list = []
    pending: dict | None = None
    while i < len(s):
        ch = s[i]
        if ch in _OPEN_CHARS:
            children, i = _parse_level(s, i + 1)
            base = "".join(name_buf).strip()
            if pending is not None and not base:
                # 相鄰的兩組括號都屬於同一項（實測「品質改良劑(醋酸鈉)(無水)」）
                pending["children"].extend(children)
            else:
                pending = _new_node(base, children)
                nodes.append(pending)
            name_buf = []
            continue
        if ch in _CLOSE_CHARS:
            _flush(nodes, name_buf, pending)
            return nodes, i + 1
        if ch in _SEP_CHARS:
            _flush(nodes, name_buf, pending)
            name_buf, pending = [], None
            i += 1
            continue
        name_buf.append(ch)
        i += 1
    _flush(nodes, name_buf, pending)
    return nodes, i


def parse_tree(section: str) -> list:
    """
    把成分段解析成樹。節點為 {"name": 括號外的名稱, "children": [子節點]}。

    最外層要用迴圈續解，不能只呼叫 _parse_level 一次（2026-07-30 實測）：
    多出來的右括號會讓它提早返回，剩下的字串就整段被丟掉。麻婆豆腐燴飯的原文
    在「…甜味劑(甘草酸鈉),八角),澱粉,…」處有一個孤立的右括號，只解析一次的話
    後面 30 幾項成分全部不見（48 項掉到 18 項）。從返回位置接著解即可。
    """
    if not section:
        return []
    nodes: list = []
    i = 0
    while i < len(section):
        part, i = _parse_level(section, i)
        nodes.extend(part)
    return [n for n in nodes if n["name"] or n["children"]]


# --------------------------------------------- 階段三：依判定表攤平成待比對項目

# 括號內含多少項才視為「複合食品」（本身是一種食品，括號內是它的配方），
# 而非「加了註記的單一物質」。實測分界很清楚：
#   單一物質＋註記 → 「醋酸鈉(無水)」「維生素C(抗氧化劑)」皆為 1 項
#   複合食品       → 「辣豆瓣醬(辣椒、黃豆、蠶豆、鹽、糖、…)」為 12 項
_COMPOUND_MIN_PARTS = 3

# 未注入官方類別清單時的退路。與 ingredient_matching 內的同名集合一致，
# 該處會另外由官方 category 欄位衍生出完整清單再注入。
_FALLBACK_GENERIC_TERMS = {
    "品質改良劑", "糊料", "香辛料", "色素", "酸味劑", "安定劑",
    "凝固劑", "膠質", "酵素", "萃取物", "營養強化劑",
    "乳化劑", "抗氧化劑", "防腐劑", "著色劑", "調味劑", "甜味劑",
    "香料", "膨脹劑", "粘稠劑", "黏稠劑", "保色劑", "漂白劑",
}

# 括號內常見的「註記」而非成分。這些詞單獨拉出來比對必然查無，
# 列出來只會讓使用者以為系統漏了一堆東西。
_ANNOTATION_ONLY = {"無水", "液狀", "粉狀", "顆粒", "天然", "人工", "食品級",
                    "非基因改造", "基因改造", "含", "微量", "適量"}
# 括號內的來源／比例補述（「來自飯體麵」「玉米來源」「70%」）
_ANNOTATION_RE = re.compile(r"^(?:來自|源自|取自|添加|約)|來源$|^\d+(?:\.\d+)?%?$")

# 括號內只寫產地的情形（「豬油(西班牙)」「牛肉(原產地：澳洲)」）。這是標示上很常見
# 的寫法，且沒有「原產地」三個字可辨識，只能認地名——括號內是國名就不是成分。
# 清單有限、也不可能窮盡，認不出來的地名會被當成一項成分而查無收錄，不會誤配。
_ORIGIN_NAMES = {
    "台灣", "臺灣", "中國", "大陸", "香港", "美國", "加拿大", "墨西哥", "巴西",
    "阿根廷", "智利", "秘魯", "尼加拉瓜", "哥斯大黎加", "日本", "韓國", "泰國",
    "越南", "印尼", "馬來西亞", "菲律賓", "新加坡", "印度", "澳洲", "紐西蘭",
    "西班牙", "義大利", "法國", "德國", "荷蘭", "比利時", "英國", "愛爾蘭",
    "丹麥", "瑞士", "奧地利", "波蘭", "俄羅斯", "烏克蘭", "土耳其", "埃及",
    "南非", "以色列", "沙烏地阿拉伯", "挪威", "瑞典", "芬蘭", "希臘", "葡萄牙",
}


def _clean_item_name(name: str) -> str:
    """
    去掉項目名稱前後的贅字。

    「含」要拿掉（實測「香料(含醋酸、甘油)」的括號內拆出「含醋酸」，
    去掉「含」才配得到醋酸）；「及其製品」「等」屬列舉語尾，一併去掉。
    """
    # 項目符號要一起去掉：截斷結尾欄位時只砍到欄位名，前面那個「●」會留在
    # 最後一項成分上（實測「羧甲基纖維素鈉 ●」），帶著它去比對必然查無。
    name = name.strip(" 　\t\r\n。，,、;；:：●•·※*")
    name = re.sub(r"^(?:含|添加|另含|並含)", "", name)
    name = re.sub(r"(?:及其製品|及製品|等)$", "", name)
    return name.strip()


def _is_annotation(name: str) -> bool:
    n = normalize_text(name)
    return ((not n) or n in _ANNOTATION_ONLY or n in _ORIGIN_NAMES
            or bool(_ANNOTATION_RE.search(n)))


def _is_annotated(node: dict, is_generic, is_substance=None) -> bool:
    """
    括號內是否只是註記（而非組成）。用於區分「醋酸鈉(無水)」與「香油(大豆油、芝麻油)」。

    兩個訊號，任一成立即視為註記：
      1. 括號內每一項都是註記、產地或類別名（無水、西班牙、抗氧化劑）
      2. 外層本身就是添加物庫收錄的物質名——那括號內只可能是它的補述
         （需注入 is_substance；離線時只用訊號 1）
    """
    kids = node["children"]
    if all(_is_annotation(c["name"]) or is_generic(normalize_text(c["name"]))
           for c in kids):
        return True
    if is_substance and is_substance(normalize_text(strip_brackets(node["name"]))):
        return True
    return False


def _default_is_generic(norm_name: str) -> bool:
    return (norm_name in _FALLBACK_GENERIC_TERMS
            or (norm_name.startswith("複方") and norm_name[2:] in _FALLBACK_GENERIC_TERMS))


def _render(node: dict) -> str:
    """把節點還原成標示上的寫法。比對端的候選名稱規則吃的是這個形式。"""
    if not node["children"]:
        return node["name"]
    inner = "、".join(c["name"] for c in node["children"] if c["name"])
    return f"{node['name']}({inner})" if inner else node["name"]


def flatten_items(tree: list, is_generic=None, is_substance=None) -> list:
    """
    把樹攤平成「該送去比對的項目」，依括號語意判定表處理四種情形：

      括號外是統稱？ 括號內項數   判定                  例
      ─────────────────────────────────────────────────────────────
      是             ≥1          用括號內的品項        乳化劑(脂肪酸甘油酯)
      是             0           純統稱，不可評估      香料
      否             ≥3          複合食品              辣豆瓣醬(辣椒、黃豆…)
      否             1–2         物質＋註記，外層優先  醋酸鈉(無水)

    與 2026-07-26 版有兩處差別：

    一、複合食品那一列：原本只取外層名稱、括號內整段丟掉，理由是避免整項被組成中的
        添加物帶偏（「辣豆瓣醬」被判成添加物）。現在改成**外層照舊只當一項食品，
        另外把括號內的組成也各自列成獨立項目**，兩件事分開處理即可同時成立——
        外層不會被帶偏，裡面寫明的添加物也不再消失（實測 48 案有 47 個添加物名稱
        是這樣整個被丟掉的）。

    二、1–2 項那一列不再一律當「物質＋註記」（2026-07-30 修）。原規則是以項目數
        分界，但「香油(大豆油、芝麻油)」「植物油(葵花籽油、棕櫚油)」只有兩項，
        括號內卻是真正的組成，照原規則會把兩種油整個丟掉。改為看括號內是什麼：
          括號內全是註記／類別／產地（無水、抗氧化劑、西班牙）→ 物質＋註記，用外層
          否則                                              → 視為複合食品
        判斷「外層是否為添加物庫收錄的物質」可再佐證，以 is_substance 注入。

    回傳 [{"name", "path", "kind", "occurrences"}]：
      name        送去比對的名稱（保留標示寫法，如「維生素C(抗氧化劑)」）
      path        它在標示上的位置（如 ["麵包", "麵糰改良劑"]），供呈現端說明來源
      kind        top / compound / component / generic_item / generic_only
      occurrences 同一個名稱在整張標示上出現幾次

    is_generic／is_substance 分別傳入「是否為官方類別統稱」與「是否為添加物庫收錄的
    物質名」的判斷函式（皆吃正規化後的名稱）。未傳入時用內建的標示慣用寫法，
    判定較保守但不需要資料庫，離線回測即走這條路徑。
    """
    is_generic = is_generic or _default_is_generic
    out: list = []
    seen: dict = {}

    def emit(name: str, path: tuple, kind: str):
        name = _clean_item_name(name)
        if not name or _is_annotation(name):
            return
        key = normalize_text(name)
        if not key:
            return
        if key in seen:
            # 同名項目只呈現一次（「水」「糖」在複合食品裡動輒重複五六次），
            # 但記下次數，需要時可還原它在標示上出現幾遍。
            seen[key]["occurrences"] += 1
            return
        item = {"name": name, "path": list(path), "kind": kind,
                "occurrences": 1}
        seen[key] = item
        out.append(item)

    def walk(nodes: list, path: tuple, kind_leaf: str):
        for node in nodes:
            name = _clean_item_name(node["name"])
            kids = node["children"]
            if not kids:
                emit(name, path, kind_leaf)
                continue
            outer_norm = normalize_text(strip_brackets(name))
            if is_generic(outer_norm):
                # 統稱帶品項：括號內才是實際使用的物質，統稱本身不列
                before = len(out)
                walk(kids, path + (name,), "generic_item")
                if len(out) == before:
                    # 括號內全是註記、一項都沒留下 → 退回當純統稱處理，
                    # 標為不可評估而不是靜默消失
                    emit(name, path, "generic_only")
            elif len(kids) >= _COMPOUND_MIN_PARTS or not _is_annotated(node, is_generic, is_substance):
                # 複合食品：外層是食品本身，括號內是它的配方，兩者都要列
                emit(name, path, "compound" if path else "top")
                walk(kids, path + (name,), "component")
            else:
                # 物質＋註記：外層才是物質本名，維持標示寫法交給比對端判斷方向
                emit(_render(node), path, kind_leaf)

    walk(tree, (), "top")
    return out


def parse_ingredients_raw(raw: str, is_generic=None, is_substance=None) -> dict:
    """
    三個階段一次跑完，供比對端呼叫。

    回傳 {"items", "tree", "section", "reason"}。reason 非 None 時 items 必為空，
    呈現端應據此說明「這張照片沒讀到成分欄位」，不可拿模型整理的清單頂替
    （見 extract_section 的 allergen_warning_only）。
    """
    section, reason = extract_section(raw)
    if reason:
        return {"items": [], "tree": [], "section": "", "reason": reason}
    tree = parse_tree(section)
    items = flatten_items(tree, is_generic, is_substance)
    return {"items": items, "tree": tree, "section": section,
            "reason": None if items else "no_items"}
