"""
Module A — 成分正規化、比對添加物知識庫

⚠ 另有一份**平行實作**：`PPOCR_TEST/sim_match.py`（評估與實驗用，不連資料庫）。
   兩份分岔過，方向是那邊落後於這邊：

     類別詞不得比對物質庫（甜味劑→阿斯巴甜）  這邊 2026-07-30 修，那邊 2026-09-10 才補
     `調味劑(A、B、C)` 展開成三項            這邊有（`_expand_generic_items`），那邊沒有

   後果：**評估報告的添加物數字一直低估這支的實際能力**（177 案少算 173 個）。
   動這裡的比對規則時，順手到那邊看一眼要不要一起改。

從 main.py 抽出(2026-07-24)。邏輯與原本逐字相同,僅一處必要調整:

  原本以 `'vision_data' in locals()` 判斷「本次分析是否有圖片辨識結果」,
  這個隱式狀態現在改為明確的 `vision_data` 參數(無圖片或圖片辨識失敗時傳
  None)。main.py 呼叫端已將 `vision_data` 於函式最前面明確初始化為 None,
  行為與原本完全一致。

  副作用發現:原本的 `locals()` 寫法在「有效條碼 + 圖片未通過品質閘門」這條
  路徑下,`vision_data` 會被賦值為 None 但仍存在於 locals() 中,導致
  `vision_data.get(...)` 對 None 呼叫而拋出 AttributeError——這是原本程式碼
  就存在的潛在崩潰,此次改為明確參數後一併修正(改用 `if vision_data:` 判斷,
  而非先呼叫 `.get()` 才判斷是否存在)。
"""
import json
import os
import re

# 文字正規化與括號處理已移到 ingredient_parser（2026-07-30）：解析端與比對端必須共用
# 同一套正規化，各留一份必然分岔。normalize_text 在此重新匯出，main.py 的既有 import
# 不受影響。
from module_a.ingredient_parser import (
    normalize_text,
    strip_brackets as _strip_brackets,
    parse_ingredients_raw,
    _PAREN_RE,
)


# ─── 跨層族群詞彙的權威來源 ────────────────────────────────────────────────────
# 這兩張表原本定義在 match_ingredients() 內部，外部無法 import，導致 Fog 與 App
# 各自複製了一份、且方向相反（App 比對英文碼、Fog 卻先轉成中文），六種族群警告
# 因此從未觸發。上提至模組層（2026-08-04）後由 tests/contract/ 強制跨層一致。
#
# 重要：GROUP_ZH_TO_EN 的「值域」就是 groupRisks[].group 能出現的全部值。不在這張
# 表內的中文族群會被靜默丟棄（見下方 group_risks 組裝處的 `continue`），所以
# 「高血壓患者」「血鐵沉著症患者」等資料庫既有標籤永遠不會傳到前端——高血壓與
# 糖尿病的個人化只能靠營養素閾值，不能靠 groupRisks。
CONCERN_TO_LEVEL = {"caution": 1, "avoid": 3, "danger": 5}

# 主管機關／國際評估機構的網域。用來判定證據層級，不用來判定可信度。
_REGULATOR_DOMAINS = (
    "efsa.europa.eu", "who.int", "fao.org", "inchem.org",
    "fda.gov", "fsa.gov.uk", "mhlw.go.jp", "fda.gov.tw",
)


def _evidence_scope(source_url: str, source_type) -> str | None:
    """證據層級。判不出來就回 None——不猜。

    「某族群有臨床風險」「法規要求標示」「機制上可能相關」不是同一個層級，
    全壓成 concern 等級會損失證據語意。但層級只有讀過該篇才分得出來，
    唯一能從網址機械判定的是「這是不是主管機關／國際評估機構的文件」。

    其餘（direct_clinical／review_article／observational／mechanistic）需要人
    逐篇認定，留 None 等人工補。**不由系統推論**——那正是這一層要避免的事。
    """
    url = (source_url or "").lower()
    if any(d in url for d in _REGULATOR_DOMAINS):
        return "regulatory_or_authority"
    if (source_type or "").strip().lower() == "official":
        return "regulatory_or_authority"
    return None

GROUP_ZH_TO_EN = {
    "孕婦": "pregnant", "哺乳期婦女": "pregnant",
    "嬰幼兒": "child", "兒童": "child", "兒童及青少年": "child",
    "六個月以下嬰兒": "child", "一歲以下嬰幼兒": "child",
    # 2026-09-13：庫內實際出現但漏收的寫法。L-麩酸那筆因此從未觸發過——
    # 不在這張表內的中文族群會被下面的 `continue` 靜默丟掉，不報錯。
    # ⚠ 新增中文鍵不影響值域（EXPECTED_GROUP_CODES 仍是七個英文碼），
    #   所以 test_group_vocabulary 不會紅；守這件事的是 test_group_coverage。
    "幼童與兒童": "child",
    "慢性腎臟病患者": "kidney_disease",
    "氣喘患者": "asthma",
    "阿斯匹靈過敏者": "aspirin_allergy",
    "苯酮尿症患者": "pku",
    "過敏體質者": "allergy", "對牛奶過敏者": "allergy",
}


# 資料庫查無此項時的統一標示。刻意不用「無」「未分類」「安全」等字眼——
# 那會把「本系統沒有這筆資料」呈現成「經評估沒有疑慮」，是相反的意思。
NOT_IN_DB_LABEL = "本系統未收錄"
# 有收錄、但該欄位本身沒有內容時用這個。與「未收錄」是兩件事：
# 前者是我們沒有這個品項，後者是有品項但缺這一項資料。兩者都不等於「無風險」。
NO_FIELD_DATA_LABEL = "資料庫未載明"
# 添加物庫沒命中時的一般成分說明。刻意只陳述**本系統的狀態**，不描述該項目是什麼——
# 判為「非添加物」不等於已確認是食品原料，它也可能是配不到的添加物（2026-08-03 定調）。
# 2026-08-06 範圍限縮後，原料清單不再比對，本句涵蓋的項目大幅增加（原先命中原料庫者
# 全數落入此類）。措辭維持不變仍然成立：它講的是「本系統沒有這項資料」，而非「這是什麼」。
NOT_IN_DB_DESC_INGREDIENT = "標示成分，本系統無收錄資料。"
# 類別統稱專用說明。與「未收錄」必須分開——兩者的成因與責任歸屬完全不同：
#   未收錄 → 本系統資料庫的缺口
#   類別統稱 → **標示端**未載明實際品項，資訊在來源就已遺失
# 用同一句話帶過會把標示的不透明說成我們的資料缺口，方向是反的。
GENERIC_TERM_DESC = (
    "標示僅載明類別名稱「{term}」，未指出實際使用的品項，故無法對應到具體物質。"
    "此為食品標示法規允許之寫法，非本系統資料缺漏。")
NOT_IN_DB_DESC = "此項未收錄於本系統添加物資料庫，無法提供說明。未收錄不代表安全或不安全。"
# RESTRICTED_RAW_DESC（「未確認安全性尚不得使用之原料」警示）於 2026-08-06 隨範圍
# 限縮一併移除，見 match_ingredients 內原料比對段落之已知限制說明。


# 食品標示上的「類別統稱」。法規允許業者只寫類別而不列出實際品項
# （例如成分只寫「香料」），此時任何資料庫都不可能比對到具體物質——
# 那是標示制度的設計，不是本系統的資料缺口。
#
# 處理方式分兩種：
#   1. 後面帶括號（如「乳化劑(脂肪酸甘油酯)」）→ 取括號內的實際品項比對
#   2. 單獨出現、沒有括號（如「香料」）→ 標記為 generic_term，
#      **排除於涵蓋率的分母之外**，但另外計數呈報，不靜默丟棄
#
# 清單來源（2026-07-26 改）：主體由**添加物資料庫的 category 欄位衍生**，
# 而非手工維護——那是官方分類，官方新增類別會自動跟著更新，少一份要補的清單。
# 僅補充「標示慣用寫法」，因官方類別名與標示寫法未必一致：
#   官方「品質改良用、釀造用及食品製造用劑」→ 標示寫「品質改良劑」
#   官方「粘稠劑（糊料）」                  → 標示常只寫「糊料」
_GENERIC_TERMS_EXTRA = {
    "品質改良劑", "糊料", "香辛料", "色素", "酸味劑", "安定劑",
    "凝固劑", "膠質", "酵素", "萃取物", "營養強化劑",
}
# category 中不具指涉意義者，不可當統稱（標示不會寫「其他」當成分）
_GENERIC_TERMS_SKIP = {"其他"}

# 由資料庫衍生的完整統稱集合（首次比對時載入，之後快取）
_generic_terms_cache: set | None = None


def _load_generic_terms(cursor) -> set:
    """
    從添加物資料庫的 category 欄位衍生類別統稱，再併入標示慣用寫法。

    衍生方式：去除括號內容（「粘稠劑（糊料）」→「粘稠劑」）、依頓號拆分
    （「品質改良用、釀造用及食品製造用劑」→ 各段），保留 2 字以上者。
    資料庫不可用時退回僅使用 _GENERIC_TERMS_EXTRA，不中斷流程。
    """
    global _generic_terms_cache
    if _generic_terms_cache is not None:
        return _generic_terms_cache

    terms = set(_GENERIC_TERMS_EXTRA)
    try:
        # 不可加 DISTINCT：category 為 json 欄位，PostgreSQL 無對應的相等運算子，
        # 會拋 UndefinedFunction 而被下方 except 吞掉，導致官方類別一個都載不進來
        # （2026-07-26 實際踩過，且不會報錯、只會安靜地少做事）。
        cursor.execute("SELECT category FROM additives WHERE category IS NOT NULL")
        for row in cursor.fetchall():
            raw = row.get("category")
            vals = raw if isinstance(raw, list) else [raw]
            for v in vals:
                if not v:
                    continue
                for piece in re.split(r"[、,，/]", _strip_brackets(str(v))):
                    t = normalize_text(piece)
                    if len(t) >= 2 and t not in _GENERIC_TERMS_SKIP:
                        terms.add(t)
                # 括號內容本身也可能是標示用語（「粘稠劑（糊料）」的「糊料」）
                for grp in _PAREN_RE.findall(str(v)):
                    t = normalize_text(grp)
                    if len(t) >= 2 and t not in _GENERIC_TERMS_SKIP:
                        terms.add(t)
    except Exception:
        pass
    _generic_terms_cache = terms
    return terms


# 添加物知識庫中的「物質名」集合（正式名＋別名，首次比對時載入並快取）。
# 與類別清單互為獨立訊號：實測兩者幾乎互斥（僅「酵素」「甜味劑」重疊），
# 故可互相佐證，避免「不在類別清單」被一律當成物質名。
_substance_names_cache: set | None = None


def _load_substance_names(cursor) -> set:
    """載入添加物庫的物質名（含別名），供括號語意判斷佐證用。"""
    global _substance_names_cache
    if _substance_names_cache is not None:
        return _substance_names_cache
    names = set()
    try:
        cursor.execute("SELECT name_zh, aliases FROM additives")
        for row in cursor.fetchall():
            for part in re.split(r"[;；]", str(row.get("name_zh") or "")):
                n = normalize_text(part)
                if n:
                    names.add(n)
            al = row.get("aliases")
            if isinstance(al, str):
                try:
                    al = json.loads(al)
                except Exception:
                    al = []
            for x in (al or []):
                n = normalize_text(x)
                if n:
                    names.add(n)
    except Exception:
        pass
    _substance_names_cache = names
    return names


def _vector_rag_enabled() -> bool:
    """
    是否啟用向量語義比對。**預設停用**（2026-08-03）。

    停用理由（皆為 48 案實測）：

      一、與本系統定位相衝。添加物採官方正面表列，「是不是添加物」是查表問題，
          有標準答案。語義比對回答的是「像不像」，命中時無出處可寫、人工無從
          複查，與「字字有出處」的主張不一致。

      二、實測價值低。261 個添加物判定中 256 項（98.1%）靠完全相等或別名即命中；
          會走到向量層的只有 32 項，且其中四項只是撇號寫法不同（正規化該處理的），
          其餘多為本就不該送進去的食品（酸菜白肉湯、奶精、酸白菜）。

      三、誤配風險無界。門檻僅為餘弦相似度 ≥0.65 且取最相似者，只要庫中有任一筆
          勉強夠像就必然回傳，命中後直接標為添加物並計入涵蓋率。相較之下字串層
          有比例門檻與微生物字尾兩道保護。

      四、成本與延遲。啟動時索引 804 筆（約 9 次呼叫，且僅存於記憶體，每次重啟
          都要重算）；查詢端平均每次掃描 1.55 次、單案最多 7 次 API 往返。

    停用後這些項目改標為「未收錄」——那是誠實的狀態，優於一個無法追溯的猜測。
    涵蓋率會因此下降，屬預期內：原本由向量撐起的部分本就不該計入正面依據。

    要恢復（如做開／關對照實驗）：環境變數 VECTOR_RAG_ENABLED 設為 1／true／yes。
    """
    return os.getenv("VECTOR_RAG_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _is_substance_name(name: str) -> bool:
    """該詞是否為添加物庫收錄的物質名（正式名或別名）。"""
    names = _substance_names_cache or set()
    return name in names


def _is_generic_term(name: str) -> bool:
    """
    是否為類別統稱。「複方」前綴以程式處理，不逐一列舉——
    標示上的「複方著色劑」「複方品質改良劑」皆為「複方＋類別名」的組合。
    """
    terms = _generic_terms_cache if _generic_terms_cache is not None else _GENERIC_TERMS_EXTRA
    hit = name in terms or (name.startswith("複方") and name[2:] in terms)
    if not hit:
        return False
    # 兩份清單重疊時（實測僅「甜味劑」「酵素」）一律**優先視為類別**。
    #
    # 理由：這類詞在標示上若帶括號（如「甜味劑(甘草酸鈉)」），括號內已載明實際
    # 品項，那是更精確的資訊；當成物質名處理會忽略標示已提供的細節，實測會讓
    # 「甜味劑(甘草酸鈉)」配到阿斯巴甜——明顯錯誤。
    #
    # 不帶括號時（如成分只寫「酵素」）視為類別亦屬正確：標示未指明品項，
    # 應標為「不可評估」而非隨意配一個同名物質。
    return True


# 括號內含多少個項目才視為「複合成分」（本身是一種食品，內含多種原料），
# 而非「加了註記的單一物質」。實測分界很清楚：
#   單一物質＋註記 → 「醋酸鈉(無水)」「維生素C(抗氧化劑)」皆為 1 項
#   複合成分       → 「辣豆瓣醬(辣椒、黃豆、蠶豆、鹽、糖、…)」為 12 項
_COMPOUND_MIN_PARTS = 3


def _is_compound(ing: str) -> bool:
    """
    判斷是否為「複合成分」：本身是一種食品，括號內列出其組成。

    為何需要（2026-07-26 實測，20 個真實標示）：比對規則是「資料庫名稱為成分名的
    子字串」，故只要複合成分的組成裡藏有任一添加物名（如「辣豆瓣醬(…甜味劑(甘草
    酸鈉)…)」），整項就會被判定為添加物。實測 152 項人工標為原料者中有 23 項
    因此誤配，比漏抓（17 項）更嚴重——漏抓只是少講一項，誤配是把食物標成添加物。

    判定依括號內的項目數：類別統稱後接的括號（「調味劑(甲、乙)」）不算複合成分，
    因為括號外本來就不是食品。
    """
    outer = normalize_text(_strip_brackets(ing))
    if not outer or _is_generic_term(outer):
        return False
    # 取「第一個左括號」到「最後一個右括號」之間的整段內容再數項目。
    # 不能只用 _PAREN_RE.findall：該正則不處理巢狀，遇到
    # 「辣豆瓣醬(…甜味劑(甘草酸鈉)…)」只會抓到最內層的一組，
    # 項目數變成 1 而漏判為非複合成分（2026-07-26 實測案例）。
    m = re.search(r"[（({\[](.*)[）)}\]]", ing, re.S)
    if not m:
        return False
    inside = m.group(1)
    parts = [x for x in re.split(r"[、,，/]", inside) if normalize_text(x)]
    return len(parts) >= _COMPOUND_MIN_PARTS


def _candidate_names(ing: str) -> list[str]:
    """
    把一項成分展開成數個待比對候選名稱，依優先序排列。

    括號的方向在真實標示上並不一致（2026-07-26 以 20 個真實案例實測）：
      「乳化劑(β-胡蘿蔔素)」  → 有用的名稱在括號**內**
      「維生素C(抗氧化劑)」   → 在括號**外**
      「醋酸鈉(無水)」        → 在括號**外**（括號內只是型態註記）
      「調味劑(甲、乙)」      → 在括號**內**，而且不只一項
    故不能一律取某一邊，改為兩邊都產生候選、依序比對，誰命中用誰。

    優先序規則：括號外若是類別統稱（如「乳化劑」），把括號內的實際品項排前面；
    否則以括號外為主（那才是物質本名）。
    """
    outer = normalize_text(_strip_brackets(ing))

    # 取「第一個左括號」到「最後一個右括號」之間的整段，再依分隔符拆項。
    # 不能用 _PAREN_RE.findall：該正則不處理巢狀，遇到
    # 「品質改良劑(醋酸鈉(無水)、甘胺酸、檸檬酸)」只會抓到最內層的「無水」，
    # 真正的三個品項完全取不到，整項因而落入「查無」（2026-07-26 實測）。
    # 拆出的每一項再交給 normalize_text，其本身會移除殘留的內層括號
    # （「醋酸鈉(無水)」→「醋酸鈉」）。
    inners = []
    m_all = re.search(r"[（({\[](.*)[）)}\]]", ing, re.S)
    if m_all:
        for piece in re.split(r"[、,，/]", m_all.group(1)):
            piece = normalize_text(piece)
            if len(piece) >= 2:
                inners.append(piece)

    if _is_compound(ing):
        # 複合成分：只用外層名稱比對。括號內是它的組成，不是它本身，
        # 拿去比對會讓整項被組成中的添加物帶偏（見 _is_compound）。
        cands = [outer] if outer else []
    elif _is_generic_term(outer):
        cands = inners + ([outer] if outer else [])
    else:
        cands = ([outer] if outer else []) + inners

    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# 原料清單比對相關的三個函式（_load_raw_material_names、
# _load_restricted_raw_material_names、_match_raw_material）於 2026-08-06 移除，
# 系統範圍限縮為只查食品添加物。`raw_materials` 資料表與其匯入腳本
# （database_scripts/import_raw_materials.py）保留未動，日後要恢復可從版控取回；
# 移除的僅是查詢路徑。原料側自有一套「先精確相等、再子字串且名稱須達 3 字」的
# 門檻（2026-07-26 以 22 種相異成分實測校準），該規則連同其校準紀錄一併退場。
# 決策與代價見 match_ingredients 內原料比對段落。


# ── 近似提示 ──────────────────────────────────────────────────────────────────
# OCR 讀錯一兩個形近字時（實例：5'-次黃「膦」呤、L-「鈦」酸鈉、「烏」嘌呤），
# 子字串比對整串落空。這裡**不修改讀到的字**，只附上「資料庫裡最接近的是誰」，
# 由使用者自己判斷。
#
# ⚠ 這與已被否決的「字典後修正」是兩件事。那個把 OCR 的字換掉再當事實往下算，
#   實測 F1 70.0 → 53.0、精確度 84.6% → 48.7%。這裡只提示、不替換、
#   **不進添加物計數也不進評分**——一旦進去就變回編造。
#
# 門檻由實測決定（2026-09-13，`PPOCR_TEST/near_miss2.py`，177 案 vlcrop 輸出）：
#
#   長度門檻   比例    會提示   猜對   猜錯   猜對率
#   （無）     0.10      6      4      2    66.7%
#   （無）     0.34     48     20     28    41.7%
#   ≥8 字      0.25     10      7      3    70.0%
#
# **長度門檻是關鍵**。沒有它時猜錯的長相是「葡萄糖漿→葡萄糖酸」「玉米糖漿→
# 玉米糖膠」「乳清蛋白→乳鐵蛋白」——看起來合理但substantively 是不同物質，
# 使用者分辨不出來。那些全是 4–6 字：短名滿是鄰居，長化學名的鄰居稀疏。
# 加上 ≥8 字之後，殘餘的錯全部收斂在核苷酸同族內（磷酸二鈉 vs 單磷酸鹽），
# 誤導性低得多，而且原文就擺在旁邊。
NEAR_MISS_MIN_LEN = 8
NEAR_MISS_MAX_RATIO = 0.25


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein。名稱短（3–15 字），不必優化。"""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near_miss(norm: str, knowledge: list):
    """回傳 {officialName, distance, scannedLength} 或 None。

    只在**現行比對已經失敗**時呼叫——所以「改一個字落到另一個真添加物」
    那個風險在這裡不存在：失敗代表這串字不是任何一個真添加物。
    """
    if not norm or len(norm) < NEAR_MISS_MIN_LEN:
        return None
    limit = int(len(norm) * NEAR_MISS_MAX_RATIO)
    if limit < 1:
        return None
    best, bd = None, limit + 1
    for a in knowledge:
        for cand in [a['_n_zh']] + list(a['_n_zh_parts']) + list(a['_n_aliases']):
            if not cand or abs(len(cand) - len(norm)) > limit:
                continue
            d = _edit_distance(norm, cand)
            if d < bd:
                best, bd = a, d
    if best is None or bd > limit:
        return None
    return {"officialName": best['name_zh'], "distance": bd,
            "scannedLength": len(norm)}


def _best_additive_match(norm: str, knowledge: list):
    """
    在添加物庫中找出最合適的一筆，而非「第一個命中就停」。

    為何不能一命中就停（2026-07-26 實測）：比對規則是「資料庫名稱為成分名的
    子字串」，短名一定也會命中含它的長名，因此誰先被檢查誰就贏。原本靠
    `ORDER BY LENGTH(name_zh) DESC` 想讓長名先試，但排的是**資料庫名稱**的長度，
    「碳酸鈉、無水碳酸鈉」（9 字）反而排在「碳酸氫鈉」（4 字）之前，於是
    「小蘇打」被碳酸鈉攔截——即使碳酸氫鈉的官方俗名就是小蘇打。
    同源錯誤還有「醋酸鈉(無水)→醋酸」「活性乳酸菌→乳酸」。

    改為收集所有命中者，再依可信度排序取最佳：
      優先序 0：正式名或別名與成分名**完全相等** —— 最可信，直接採用
      優先序 2：子字串命中 —— 取**匹配長度最長**者（比對到越多字，偶然吻合的機率越低）
    同級之間取匹配字串較長者。完全沒有命中則回傳 None。

    優先序 1 原為「INS／E 編號相符」，2026-08-06 移除。理由：48 案 1,092 個標示項目
    實測，該層命中 **0 次**——我國標示法規要求以品名或通用名稱標示，包裝上不寫 INS
    編號（寫 E 編號是歐盟產品的習慣）。本檔其餘規則（比例門檻、生物字尾、DL-）皆由
    實測誤配案例掙來，此層無任何命中案例佐證，故依同一標準移除。日後若納入進口品
    掃描，再依證據加回。
    （移除時另曾主張「庫內編號若為裸數字，『咖啡因150mg』會誤命中 INS 150」，核對
    backup.sql 後確認不成立：ins_or_e_number 一律帶前綴（`INS 331`、`E518`），標示需
    寫出「INS 150」字樣才會觸發，故此層是死碼而非地雷。移除仍以「0 命中」為據。）
    優先序編號保留空號 1，使 0／2 與既有紀錄、稽核腳本的語義維持一致。
    """
    # 子字串命中時，匹配部分至少要佔成分名的一定比例，否則視為偶然吻合而不採用。
    # 依據（2026-07-26 實測）：
    #   「活性乳酸菌」命中「乳酸」→ 2/5 = 40%，實為微生物，不該配
    #   「檸檬酸鈉」命中「檸檬酸」→ 3/4 = 75%，正確
    #   「焦糖糖漿」命中「焦糖」   → 2/4 = 50%，糖漿非著色劑，不該配
    # 0.6 可分開上述案例；完全相等與編號相符不受此限（本就是最高可信度）。
    MIN_SUBSTR_RATIO = 0.6

    # 以「菌」等生物名稱結尾者，一律不接受子字串命中。比例門檻擋不住這一類
    # （2026-07-30 實測：「乳酸菌」命中「乳酸」是 2/3 = 67%，高於門檻）。
    # 微生物與它名稱裡包含的那個酸是兩回事，且沒有任何添加物是靠這種包含關係
    # 命名的，故直接依字尾排除；完全相等仍可命中（庫內若真有同名品項不受影響）。
    ORGANISM_SUFFIXES = ("菌", "菌種", "菌粉", "黴", "酵母")

    # 標示寫 DL-（消旋物）時，不接受以子字串命中 L-（單一光學異構物）版本。
    #
    # 這不是機率問題而是字面包含關係（2026-08-03 實測）：「L-丙胺酸」整串就包在
    # 「DL-丙胺酸」裡，佔 6/7 = 86%，遠高於比例門檻，故只要庫內只有 L- 版就**必然**
    # 誤配。庫內 L- 開頭 36 項、DL- 開頭 9 項，其中 31 項只有 L- 版而落在此陷阱上。
    #
    # 兩者是不同物質、不同 INS 編號，不可互相頂替。庫內若真有 DL- 版，走的是完全
    # 相等（優先序 0），不受本規則影響；沒有 DL- 版時則標為未收錄——那是正確答案。
    is_racemic = norm.upper().startswith("DL-")

    def _hits_l_form(a) -> bool:
        """該筆是否為 L- 版（正式名、分段名或別名任一以 L- 開頭）。"""
        cands = [a['_n_zh'] or ""] + list(a['_n_zh_parts']) + list(a['_n_aliases'])
        return any(c.upper().startswith("L-") for c in cands if c)

    best = None  # (優先序, 匹配長度, 該筆資料)
    for a in knowledge:
        hit = None
        if a['_n_zh'] and a['_n_zh'] == norm:
            hit = (0, len(a['_n_zh']))
        elif any(p == norm for p in a['_n_zh_parts']):
            hit = (0, len(norm))
        elif any(al == norm for al in a['_n_aliases']):
            hit = (0, len(norm))
        elif a['_n_zh'] and a['_n_zh'] in norm:
            hit = (2, len(a['_n_zh']))
        elif any(p and p in norm for p in a['_n_zh_parts']):
            hit = (2, max(len(p) for p in a['_n_zh_parts'] if p and p in norm))
        elif a.get('name_en') and a['name_en'].lower() in norm.lower():
            hit = (2, len(a['name_en']))
        else:
            matched_alias = next((al for al in a['_n_aliases'] if al and al in norm), None)
            if matched_alias:
                hit = (2, len(matched_alias))
        if hit is None:
            continue
        if hit[0] == 2 and norm and hit[1] / len(norm) < MIN_SUBSTR_RATIO:
            continue  # 匹配比例過低，視為偶然吻合
        if hit[0] == 2 and norm.endswith(ORGANISM_SUFFIXES):
            continue  # 微生物名稱，不是它包含的那個物質
        if hit[0] == 2 and is_racemic and _hits_l_form(a):
            continue  # 消旋物不可頂替單一光學異構物（見 is_racemic）
        cand = (hit[0], -hit[1], a)
        if best is None or cand[:2] < best[:2]:
            best = cand
        if hit[0] == 0:
            break  # 完全相等已是最高可信度，無須再找
    return best[2] if best else None


def _expand_ingredients(ing_list: list) -> list[tuple]:
    """
    把「類別統稱(品項A、品項B、品項C)」展開成各自獨立的成分。

    回傳 [(用於比對與呈現的名稱, 原始標示字串或 None)]，原始字串僅在展開時才有值。

    為何要展開（2026-07-26 實測，20 個真實標示）：食品標示常以
    「調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉、…)」一項載明多個添加物，
    最多一項含 7 個。原本一項成分只對應一筆結果，使用者只會看到其中一個，
    添加物數量被嚴重低估——該批 5 項標示實際載明 18 個添加物，只呈現 5 個。
    這不是比對規則的問題，是「一項成分對應一筆」的結構假設與真實標示不符。

    **複合成分不展開**（見 _is_compound）：「辣豆瓣醬(辣椒、黃豆、…)」括號內是
    該食品的組成配方，不是標示所宣告的添加物清單，展開會把食材誤列為添加物。
    """
    out = []
    for ing in ing_list:
        if not ing:
            continue
        outer = normalize_text(_strip_brackets(ing))
        if _is_generic_term(outer) and not _is_compound(ing):
            parts = [c for c in _candidate_names(ing) if c != outer]
            if parts:
                out.extend((p, ing) for p in parts)
                continue
        out.append((ing, None))
    return out


def _items_from_raw_text(ingredients_raw) -> dict | None:
    """
    改用標示原文自行解析出待比對項目（2026-07-30）。

    為何不再用模型整理好的清單：辨識層抄下來的原文完整保留括號與巢狀結構，
    模型卻在整理成清單時把複合食品內部的細項收掉了——48 案原文可拆出 1,245 項，
    清單只剩 755 項，其中 47 個是標示明明寫出來的添加物名稱（如麵糰改良劑內的
    維生素C、起司片內的己二烯酸鉀）。解析原文不必改 prompt、不必重跑辨識、
    不花 API 費用，資料本來就在。

    回傳 None 表示原文不可用，呼叫端應退回模型清單；回傳 dict 則一律採用其結果，
    即使 items 為空——空的原因（如整段其實是過敏原警語）比拿一份錯的清單頂替重要。
    """
    if not ingredients_raw or not str(ingredients_raw).strip():
        return None
    parsed = parse_ingredients_raw(str(ingredients_raw),
                                  is_generic=_is_generic_term,
                                  is_substance=_is_substance_name)
    # 原文有字但一段成分都切不出來（如整張照片只拍到過敏原警語）→ 仍採用此結果。
    # 不可退回模型清單：實測那份清單裡裝的就是被誤讀成成分的六個過敏原。
    if parsed["reason"] in ("empty", "no_section"):
        return None
    return parsed


def _category_list(match) -> list:
    """把資料庫的 category 正規化成字串陣列。

    這一欄在不同批次的匯入下出現過三種形狀：json 陣列、單一字串、None。
    收斂成一種，讓下游（App 的類別統計）只需處理陣列。
    """
    if not match:
        return []
    raw = match.get("category")
    if raw is None:
        return []
    vals = raw if isinstance(raw, list) else [raw]
    out = []
    for v in vals:
        t = str(v).strip()
        if t and t not in out:
            out.append(t)
    return out


def match_ingredients(ing_list_raw, vision_data, cursor, vector_rag,
                      ingredients_raw=None) -> dict:
    """
    比對成分清單與添加物知識庫,輸出分類結果。

    2026-07-26 新增判定依據統計(coverage):原本「比對不上添加物庫就當成一般
    成分」是預設而非確認,導致「真的是原料」與「其實是添加物只是寫法對不上」
    混在同一堆,無從得知一張成分表實際涵蓋多少。現在額外記錄每項成分的判定
    依據,使其成為可量測的筆數。
    (2026-08-06:其中的 coverage_rate 百分比已移除,只留筆數與未收錄清單。
     理由見 _build_coverage ——那個比率量的是商品配方,不是系統能力。)

    注意:此統計**不改變** calculated_ingredient_types 的值(仍只有
    additive / ingredient),既有回傳格式與 Fog 端相容性不受影響。

    2026-07-30 起改以**標示原文**為比對來源(見 _items_from_raw_text),模型整理過的
    清單只在原文缺漏時作為退路。ing_list 回傳值刻意維持原本的模型清單不變——
    它另供 Nutri-Score 判斷是否含非營養性甜味劑,改動會連帶改變評分等級,
    那是另一件事,不併入本次變更。

    參數:
      ing_list_raw: product['ingredients_list'],可能是 JSON 字串或 list
      vision_data: 本次 Gemini 視覺辨識結果 dict,無圖片或辨識失敗時為 None
      cursor: 資料庫 cursor(RealDictCursor),用於查詢 additives 表
      vector_rag: 全域 VectorRAG 單例,用於語義比對補償
      ingredients_raw: 標示上「成分」欄位的完整原文,無則退回模型清單

    回傳 dict:
      ing_list: 解析後的成分清單(list[str])
      basic: 一般成分名稱列表
      chemical: 添加物詳細資訊列表
      calculated_ingredient_types: {成分名稱: "additive" | "ingredient"}
      parse: 本次成分來源與解析結果(見 _items_from_raw_text)
      coverage: 各判定依據的筆數與未收錄項目清單(見 _build_coverage;無百分比)
    """
    ing_list = ing_list_raw
    if isinstance(ing_list, str):
        ing_list = json.loads(ing_list)

    # 抓取完整的添加物知識庫資訊
    cursor.execute("SELECT id, record_id, name_zh, name_en, aliases, ins_or_e_number, category, food_tech_purpose, adi, medical_caution, iarc_class, description, risks, description_sources FROM additives ORDER BY LENGTH(name_zh) DESC")
    knowledge = cursor.fetchall()

    # 預先正規化資料庫側的名稱與別名。比對時兩邊都必須經過同一套正規化，
    # 否則排版差異（如彎引號 ’ vs 直引號 '）會讓同一物質永遠對不上——
    # 2026-07-26 實測：整組核苷酸類調味劑因此全部漏配，還被「磷酸氫二鈉」
    # 以片段吻合撿走。此處算一次，避免每項成分重複正規化 804 筆。
    for a in knowledge:
        # 品名欄位偶爾一格塞多個名稱（官方原樣，如「醋酸鈉； 醋酸鈉（無水）」），
        # 需拆開才比對得到，否則正規化後成為「醋酸鈉;醋酸鈉」而與「醋酸鈉」不相等。
        a['_n_zh'] = normalize_text(a.get('name_zh') or '')
        a['_n_zh_parts'] = [x for x in
                            (normalize_text(p) for p in re.split(r"[;；、]", a.get('name_zh') or ''))
                            if x]
        # 不再預先正規化 ins_or_e_number：編號比對層已移除（見 _best_additive_match）。
        # 欄位本身仍取用於呈現，只是不參與比對。
        _al = a.get('aliases')
        if isinstance(_al, str):
            try:
                _al = json.loads(_al)
            except Exception:
                _al = [_al]
        a['_n_aliases'] = [normalize_text(x) for x in _al if x] if isinstance(_al, list) else []

    # --- 初始化向量 RAG 知識庫（僅在啟用且需要時）---
    if _vector_rag_enabled() and not vector_rag.is_initialized and knowledge:
        vector_rag.update_knowledge_base(knowledge)

    basic, chemical = [], []
    # 一般成分的逐項說明（與 basic 同序）。供下游取代原本寫死的單一句子。
    basic_detail = []
    CHEM_CHARS = ["酯", "鈉", "鉀", "酸", "磷", "聚", "精", "醇", "膠", "素"]

    # 準備資料庫更新清單 (智能補完;目前呼叫端未消費此結果,沿用原邏輯保留計算)
    db_updates = []

    _load_generic_terms(cursor)    # 由官方 category 衍生類別統稱（快取）
    _load_substance_names(cursor)  # 添加物庫物質名，供括號語意判斷佐證（快取）

    calculated_ingredient_types = {}
    # 每項成分的判定依據,供涵蓋率統計:
    #   additive_db / vector / water / generic_term / none
    match_basis = {}

    # 成分來源:優先解析標示原文,原文缺漏才退回模型整理的清單（2026-07-30）
    parsed = _items_from_raw_text(ingredients_raw)
    if parsed is not None:
        # 解析器已依括號語意判定表展開過（統稱取括號內品項、複合食品連內部組成
        # 一併列出），故不再套用 _expand_ingredients，否則會重複展開。
        # source_label 取上一層的名稱，讓呈現端能說明「這項來自哪一個複合食品」。
        expanded = [(it["name"], it["path"][-1] if it["path"] else None)
                    for it in parsed["items"]]
        parse_info = {"source": "raw_text", "reason": parsed["reason"],
                      "n_items": len(parsed["items"]), "section": parsed["section"]}
    else:
        # 類別統稱帶品項者先展開成獨立成分（見 _expand_ingredients）
        expanded = _expand_ingredients(ing_list)
        parse_info = {"source": "model_list", "reason": None,
                      "n_items": len(expanded), "section": ""}
    for ing, source_label in expanded:
        if not ing: continue
        norm = normalize_text(ing)
        # 括號可能把實際品項包在裡面或外面，方向不一致，故兩邊都產生候選（見 _candidate_names）
        candidates = _candidate_names(ing) or [norm]
        match = None
        # 若為水等純原料成分，直接跳過添加物比對，避免誤判
        if norm in ["水", "純水", "熱水", "冰水", "蒸餾水", "礦泉水", "飲用水", "water"]:
            match = None
        else:
          for norm in candidates:
            # 類別統稱不得拿去比對物質庫，**括號內的也不行**（2026-07-30 實測）。
            # 原本只擋「整項就是統稱」的情形，漏了統稱寫在括號裡的寫法：
            # 「甘草(甜味劑)」的外層甘草在庫內查無，於是改試括號內的「甜味劑」，
            # 而該詞同時被收為添加物品名，結果配到阿斯巴甜——標示上根本沒有這個東西。
            if _is_generic_term(norm):
                continue
            match = _best_additive_match(norm, knowledge)
            if match:
                break
          norm = normalize_text(ing)  # 還原，供後續向量／原料比對與顯示使用

        # 純類別統稱（判定為類別、且括號未載明品項）不得比對物質庫。
        # 「甜味劑」「酵素」等詞同時收錄於官方類別與添加物庫，若仍往物質庫查，
        # 會隨意配到一個同名物質（實測：甜味劑→阿斯巴甜、酵素→酵素製劑），
        # 等於在標示未指明品項時替它選了一個——與「香料」應標為不可評估相矛盾。
        if _is_generic_term(normalize_text(_strip_brackets(ing))) and len(candidates) <= 1:
            match = None

        matched_by = "additive_db" if match else None
        if not match and norm in ["水", "純水", "熱水", "冰水", "蒸餾水", "礦泉水", "飲用水", "water"]:
            matched_by = "water"

        # 向量 RAG 補償：只有含化學性字元的成分才做語義匹配，避免「水」等原料誤判。
        # 預設停用（2026-08-03），見 _vector_rag_enabled。
        if _vector_rag_enabled() and not match:
            for cand in candidates:
                if any(c in cand for c in CHEM_CHARS):
                    match = vector_rag.find_nearest(cand)
                    if match:
                        matched_by = "vector"
                        break

        # 原料清單比對（可食清單與「不得使用」清單兩者）已於 2026-08-06 全部移除，
        # 系統範圍限縮為「只查食品添加物」。
        #
        # 移除理由：本系統要回答的是「這張標示裡有哪些食品添加物、各自的風險資訊為何」。
        # 為了替糖、鹽、棕櫚油這類基礎食材蓋一個「已確認為原料」的章，就得維護第二套
        # 名稱解析規則（原料側自有的「精確相等，再子字串且≥3 字」門檻），承擔第二套
        # 誤配風險，而該結論對使用者的決策沒有影響——知道「蔗糖是原料」不改變任何事。
        # 範圍縮小後，非添加物一律僅回報為非添加物，不再宣稱它是什麼。
        #
        # **已知限制（範圍限縮的代價，刻意記錄）**：「未確認安全性尚不得使用之原料」
        # 清單（532 筆，如大麻、罌粟、大麻二酚）的警示一併移除。該類成分現與其他
        # 未收錄項目顯示相同，使用者無從區別。此為明示的範圍決定，非疏漏；日後若
        # 要恢復，應作為獨立的警示功能，而非併回涵蓋率判定。

        # 純類別統稱（如成分只寫「香料」）：法規允許不列出實際品項，任何資料庫
        # 都不可能比對到。標記為 generic_term，涵蓋率計算時排除於分母之外，
        # 但另行計數呈報——這是標示制度的限制，不是本系統的資料缺口。
        if not match and matched_by is None and _is_generic_term(normalize_text(_strip_brackets(ing))):
            matched_by = "generic_term"

        # 獲取 AI 產出的專業描述 (從 vision_data 或 ingredient_details 獲取)
        ai_info = {}
        if vision_data:
            ai_info = vision_data.get('ingredient_details', {}).get(ing, {})

        ai_desc = ai_info.get('desc')
        ai_purpose = ai_info.get('purpose')

        # 決策邏輯：1. 優先使用資料庫 -> 2. 資料庫沒有或沒描述則記錄待更新 -> 3. 使用 AI 產出的描述
        if match and match['description']:
            final_desc = match['description']
            final_purpose = match['food_tech_purpose']
        elif match:
            # 資料庫**有這一項但沒有說明**：標示未載明，不拿模型生成的描述頂上。
            #
            # 2026-09-22：原本這種情形會掉到下面的 `elif ai_desc`，於是顯示的是
            # 視覺模型當場產生的說明。知識庫的填寫紀律是「只填有確定性文獻可佐證
            # 的部分」，需要毒理或醫學判斷的欄位一律留空（iarc_class、
            # jecfa_summary、medical_caution 都是 0 覆蓋），而留空的欄位若在呈現時
            # 被模型補上，那條紀律就等於沒有——使用者看到的仍是未查證的內容，
            # 而且它旁邊還掛著這一項的正式出處連結，看起來像有根據。
            #
            # 用與 caution／iarcRating 同一個標籤：見 NO_FIELD_DATA_LABEL 的說明，
            # 不可寫成「無」或「無特定風險」——那會被讀成「經評估無需注意」。
            final_desc = NO_FIELD_DATA_LABEL
            final_purpose = match['food_tech_purpose']
            # 仍然記進待更新清單，讓「哪些項目缺說明」查得到。
            # 註：清單目前只收集不套用（見本檔末尾註解掉的自動補齊），
            # 補齊與否是資料層的決定，不在這裡做。
            db_updates.append({
                "name": ing,
                "description": None,
                "purpose": ai_purpose,
                "exists": True,
                "id": match['id'],
            })
        elif ai_desc:
            final_desc = ai_desc
            final_purpose = ai_purpose
            # 走到這裡表示資料庫**根本沒有這一項**（有這一項但缺說明的情形已由
            # 上面的 `elif match` 接走），故 exists 必為 False。
            db_updates.append({
                "name": ing,
                "description": ai_desc,
                "purpose": ai_purpose,
                "exists": False,
                "id": None,
            })
        # 註：原本此處還有一條 `elif match` 分支，內容是
        #     f"此成分為{food_tech_purpose}，建議依個人體質適量攝取。"
        # 2026-09-22 移除，兩個理由：
        #   1. 它已被上面新增的 `elif match` 完全遮蔽，永遠不會執行。
        #   2. food_tech_purpose 裝的是食藥署的使用範圍及限量原文（含劑量與換行，
        #      最長 319 字），套進那個句型會產生一大段不成話的文字；而
        #      「建議依個人體質適量攝取」本身也是憑空給的建議，沒有出處。
        elif matched_by is None:
            # 兩個資料庫都沒命中：不得以「無特定風險紀錄」帶過。沒有紀錄的原因是
            # 未收錄，不是評估後認定無風險，兩者對使用者的意義完全相反。
            final_desc = NOT_IN_DB_DESC
            final_purpose = None
        else:
            final_desc = "一般成分，目前無特定風險紀錄。"
            final_purpose = None

        # 成分歸屬完全依官方正面表列，不採用 Gemini 的分類（2026-07-26 決議）。
        #
        # 我國食品添加物採正面表列制：列於《食品添加物使用範圍及限量暨規格標準》
        # 者方為合法添加物。「是不是添加物」是查表問題，不是判斷問題，故不交由
        # 模型即時判定——模型判斷無官方依據、不可重現，且無正解可供驗證
        # （見 A1 對 ingredient_types 停止評估之說明）。
        #
        # 判定順序：
        #   1. 添加物庫命中（精確／別名／向量）→ additive
        #   2. 其餘一律 → ingredient
        # 注意「ingredient」在此僅表示「非添加物」，不代表已確認為食品原料；
        # 它也可能是配不到的添加物。範圍限縮後（2026-08-06）系統不再嘗試確認它是
        # 什麼，match_basis 於此側只剩 water／generic_term／none 三態，呈現端據此
        # 誠實標示——差別在「標示只給類別名」與「本系統沒有這項資料」。
        is_additive = bool(match)
        calculated_ingredient_types[ing] = "additive" if is_additive else "ingredient"

        # 兩個資料庫都沒命中且非水／統稱 → none（未收錄）。
        # 不再有 gemini_only 這個依據：成分歸屬已完全改依官方清單，
        # 模型分類不參與，故也不作為涵蓋率的判定依據。
        if matched_by is None:
            matched_by = "none"
        match_basis[ing] = matched_by


        if is_additive:
            # 處理化學食品添加物之中英文名稱
            display_name = ing
            if match:
                if match.get('name_en'):
                    display_name = f"{match['name_en']} ({ing})"
                elif match.get('aliases'):
                    try:
                        aliases_list = match['aliases']
                        if isinstance(aliases_list, str):
                            aliases_list = json.loads(aliases_list)
                        if isinstance(aliases_list, list):
                            en_name = next((a for a in aliases_list if re.match(r'^[A-Za-z0-9\s\-\(\)\.\,\']+$', a)), None)
                            if en_name:
                                display_name = f"{en_name.strip()} ({ing})"
                    except Exception:
                        pass

            # 解析 risks 欄位並轉換為 Fog 可讀格式
            risks_raw = match.get('risks') if match else []
            if isinstance(risks_raw, str):
                try:
                    risks_raw = json.loads(risks_raw)
                except Exception:
                    risks_raw = []

            group_risks = []
            for r in (risks_raw or []):
                zh_group = r.get("group", "")
                en_group = GROUP_ZH_TO_EN.get(zh_group)
                if not en_group:
                    continue

                # ── 證據納入門檻 ────────────────────────────────────────────
                # 沒有可追溯的來源就不輸出。庫內有 5 條標著 confidence
                # "verified" 卻 source_type "none"、網址空白——那是 schema 上的
                # 矛盾：宣稱已驗證卻指不出依據。與其在畫面上替它解釋，不如
                # 不輸出；資料本身保留，要複核或補出處隨時看得到。
                #
                # ⚠ 門檻是「指得出來源」，不是「風險大小」。不輸出**不代表沒有
                #   風險**，只代表這一條沒有達到系統的納入門檻。同理，755 種沒有
                #   risks 的添加物，空白也不可被讀成安全。
                source_url = (r.get("source_url") or "").strip()
                source_quote = (r.get("source_quote") or "").strip()
                if not source_url and not source_quote:
                    continue

                group_risks.append({
                    "group": en_group,
                    "riskLevel": CONCERN_TO_LEVEL.get(r.get("concern", "caution"), 1),
                    # 模型對來源的解讀。與下面的 sourceQuote 刻意分成兩個欄位：
                    # 原文寫了什麼、模型怎麼讀它，是兩件事，混在一起就分不出
                    # 哪一句要負責。
                    "reason": r.get("ai_reasoning") or source_quote or zh_group,
                    # 來源真正寫了什麼。2026-09-22 之前這一欄**從未送到 App**，
                    # 只在 ai_reasoning 缺席時被當成替代文字——真正的證據沒上桌，
                    # 畫面上只剩模型的解讀。
                    "sourceQuote": source_quote,
                    "sourceUrl": source_url,
                    # 證據狀態。**刻意不叫 confidence，值也不再有 "verified"。**
                    # 庫內全部 65 條都是 reviewed_by_human=false，沒有任何一條經過
                    # 人工逐筆複核，此時把 verified 呈現成「已驗證」會答不出
                    # 「誰驗證的」。這一欄要表達的一直都是「附有可追溯的來源證據」，
                    # 不是「內容已被確認為真」。
                    "evidenceStatus": "source_backed",
                    # 證據層級。能從來源網域機械判定的才填，判不出來就留 None——
                    # 「這是臨床研究還是綜述」要讀過該篇才知道，由系統猜等於
                    # 又做了一次無法驗證的推論。
                    "evidenceScope": _evidence_scope(source_url, r.get("source_type")),
                    # ⚠ ai_reasoning 是模型產生的（有 sourceQuote 佐證，但未經逐筆
                    #   人工複核）。誠實標出來，呈現端必須據此加註，不可讓它看起來
                    #   像已審定的結論。缺這個鍵時一律視為 False。
                    "reviewedByHuman": bool(r.get("reviewed_by_human")),
                })
                # 註：sourceTitle 與 sourceYear 自 2026-09-22 起不再輸出。
                # 實測庫內同一個網址（PMC4017440）掛了 8 種不同標題、26 條記錄，
                # 其中有明顯對不上的；年份格式也混（1998 是數字、"2022" 是字串、
                # 有空字串也有 null），21 條根本沒有標題。網址與引述是一致的，
                # 標題與年份是模型填的。**錯誤的 metadata 比缺 metadata 更糟**——
                # 它看起來正式，反而讓人誤信。欄位仍留在資料庫，只是不送出。

            adi_val = None
            if match and match.get('adi'):
                clean_adi = str(match['adi']).strip().lower()
                if clean_adi not in ["unknown", "none", "", "null", "見標示"]:
                    adi_val = match['adi']

            chemical.append({
                "name": display_name,
                "sourceLabel": source_label,   # 由哪一項標示展開而來（未展開者為 None）
                "isAdditive": True,
                "officialName": match['name_zh'] if match else ing,
                # 官方類別（防腐劑、著色劑…）。資料庫的 category 是 json 陣列，
                # 一項添加物可能同時屬多類，故原樣送出陣列而不攤平成字串——
                # 前端要依類別分組統計，攤平了就得再解析一次。
                # 比對不到資料庫時為空陣列，**不是 None**：前端一律當陣列處理，
                # 少一個 null 判斷就少一種當掉的方式。
                "category": _category_list(match),
                "purpose": final_purpose or "食品添加物",
                "adiValue": adi_val,
                # 查無資料時一律標示「未收錄」，不可填「無」或「未分類」——
                # 「無」會被讀成「經評估無需注意」，而 IARC 本身就有「無法歸類」
                # （Group 3）這個正式分類，填「未分類」會被誤認為官方評估結論。
                "caution": ((match.get('medical_caution') or NO_FIELD_DATA_LABEL)
                            if match else NOT_IN_DB_LABEL),
                "iarcRating": ((match.get('iarc_class') or NO_FIELD_DATA_LABEL)
                               if match else NOT_IN_DB_LABEL),
                "inDatabase": bool(match),
                "description": final_desc,
                "groupRisks": group_risks,
                "risk_level": "medium" if match or ai_desc else "low",
                "description_sources": match.get('description_sources') or [] if match else []
            })
        else:
            basic.append(ing)
            # 一般成分也要誠實區分「有依據」與「只是沒比對到」。
            # 原本下游對每一項一律寫「天然成分，提供基礎營養」，那是對所有
            # 未命中項目的無依據宣稱——系統並不知道它是不是天然的。
            if matched_by == "water":
                basic_desc = "水。"
            elif matched_by == "generic_term":
                basic_desc = GENERIC_TERM_DESC.format(
                    term=normalize_text(_strip_brackets(ing)))
            else:
                basic_desc = NOT_IN_DB_DESC_INGREDIENT
            # 近似提示：只對「查無此項」的給，水與類別統稱不給——
            # 那兩者不是沒配到，是本來就不該配（見 _near_miss）。
            near = None
            if matched_by not in ("water", "generic_term"):
                for cand_name in _candidate_names(ing):
                    near = _near_miss(cand_name, knowledge)
                    if near:
                        break
            basic_detail.append({
                "name": ing,
                "sourceLabel": source_label,   # 由哪一項標示展開而來（未展開者為 None）
                "isAdditive": False,
                "inDatabase": matched_by == "water",
                # 標示僅給類別名，無法判定為添加物或原料——這是第三種狀態，
                # 不應歸入任一邊（見 GENERIC_TERM_DESC）。
                "isGenericTerm": matched_by == "generic_term",
                "description": basic_desc,
                # 疑似對象。**不是判定**——呈現端必須標成未確認，
                # 且不可計入添加物數量或評分。None 代表沒有夠接近的鄰居。
                "nearMiss": near,
            })

    # 執行資料庫智能補完 (暫時註解以確保穩定性;沿用原狀,未啟用)
    # if db_updates:
    #     print(f"[INFO] 發現 {len(db_updates)} 個缺失添加物，準備智能補齊...")
    #     for up in db_updates:
    #         try:
    #             if up['exists']:
    #                 cursor.execute(
    #                     "UPDATE additives SET description = %s, food_tech_purpose = %s WHERE id = %s",
    #                     (up['description'], up['purpose'], up['id'])
    #                 )
    #             else:
    #                 cursor.execute(
    #                     "INSERT INTO additives (name, description, food_tech_purpose) VALUES (%s, %s, %s)",
    #                     (up['name'], up['description'], up['purpose'])
    #                 )
    #             db.commit()
    #         except Exception as e:
    #             print(f"[WARN] 智能補齊添加物失敗: {up['name']}, Error: {e}")
    #             db.rollback()


    return {
        "ing_list": ing_list,
        "basic": basic,
        "basic_detail": basic_detail,
        "chemical": chemical,
        "calculated_ingredient_types": calculated_ingredient_types,
        "coverage": _build_coverage(match_basis),
        "parse": parse_info,
    }


def _build_coverage(match_basis: dict) -> dict:
    """
    統計本張成分表各判定依據的**筆數**，並列出未收錄項目清單。

    「已涵蓋」定義為**有本系統資料庫的正面依據**（添加物庫、向量比對、水類），
    不包含只靠 Gemini 分類或完全無依據者——把 LLM 的判斷算進來
    等於自己給自己灌水，那個數字就失去意義了。

    **`coverage_rate` 已於 2026-08-06 移除，且不應再加回來。**
    該比率的分母是整張標示的成分數，但本系統刻意只負責其中的添加物，故它實際上
    量的是「這張標示裡添加物佔多少」——那是**商品配方的屬性，不是系統能力的屬性**。
    A2 的分類數字即為證據：飲料 50–62%、便當飯類 15–25%、鮮乳 **0%**。鮮乳得 0
    並非系統在鮮乳上失敗，而是鮮乳沒有添加物——系統表現完美卻得零分。一個「完美
    表現得 0 分」的指標，量的不是它宣稱要量的東西。範圍限縮為只查添加物後，該比率
    幾乎等同「添加物佔比」的同義詞，更無保留價值。

    比對品質改看三個可行動的數字，皆不需要比率：
      1. 誤配筆數（安全指標，須守住）——`測試/量化測試/audit_additive_matches.py`
      2. 官方名稱探針偵測率（能力指標，可重跑）——`eval_official_name_coverage.py`
      3. 未收錄項目**清單**（工作清單，用絕對筆數與名稱，不用百分比）——即下方
         unknown_items，這是後續要補哪些資料的直接清單，也是本函式真正的產出。
    """
    total = len(match_basis)
    by_basis = {}
    for b in match_basis.values():
        by_basis[b] = by_basis.get(b, 0) + 1

    covered_bases = ("additive_db", "vector", "water")
    covered = sum(by_basis.get(b, 0) for b in covered_bases)
    unknown_items = [k for k, v in match_basis.items() if v == "none"]

    # 純類別統稱不計入分母：法規允許成分只寫「香料」而不列出實際品項，
    # 那類項目在任何資料庫都不可能比對到，計入分母等於因標示制度的設計而
    # 懲罰系統。**但不靜默丟棄**——另行列出項目與計數，讓兩個數字都看得到。
    generic_items = [k for k, v in match_basis.items() if v == "generic_term"]
    denominator = total - len(generic_items)

    return {
        "total": total,
        "covered": covered,
        "unknown": len(unknown_items),
        # denominator 保留（total 扣除類別統稱）：它說明「有多少項是可評估的」，
        # 是一個筆數而非比率。要不要拿它去除，由讀數字的人自行決定並自負口徑說明
        # 之責——本函式不再代為算出一個會被到處引用的百分比。
        "denominator": denominator,
        "by_basis": by_basis,
        "unknown_items": unknown_items,
        "generic_terms": generic_items,
    }
