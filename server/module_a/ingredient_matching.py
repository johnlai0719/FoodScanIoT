"""
Module A — 成分正規化、比對添加物知識庫

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

GROUP_ZH_TO_EN = {
    "孕婦": "pregnant", "哺乳期婦女": "pregnant",
    "嬰幼兒": "child", "兒童": "child", "兒童及青少年": "child",
    "六個月以下嬰兒": "child", "一歲以下嬰幼兒": "child",
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
# 兩個資料庫都沒命中時的一般成分說明。刻意只陳述**本系統的狀態**，不描述該項目是什麼——
# 判為「非添加物」不等於已確認是食品原料，它也可能是配不到的添加物（2026-08-03 定調）。
NOT_IN_DB_DESC_INGREDIENT = "標示成分，本系統無收錄資料。"
# 類別統稱專用說明。與「未收錄」必須分開——兩者的成因與責任歸屬完全不同：
#   未收錄 → 本系統資料庫的缺口
#   類別統稱 → **標示端**未載明實際品項，資訊在來源就已遺失
# 用同一句話帶過會把標示的不透明說成我們的資料缺口，方向是反的。
GENERIC_TERM_DESC = (
    "標示僅載明類別名稱「{term}」，未指出實際使用的品項，故無法對應到具體物質。"
    "此為食品標示法規允許之寫法，非本系統資料缺漏。")
NOT_IN_DB_DESC = "此項未收錄於本系統添加物資料庫，無法提供說明。未收錄不代表安全或不安全。"
# 列於食藥署「未確認安全性尚不得使用之原料」清單者專用。
# 只陳述它列於哪一份官方清單，不判斷該產品是否違規——標示寫法、同名異物、
# 辨識誤差都可能造成命中，是否違規為主管機關職權，非本系統可認定。
RESTRICTED_RAW_DESC = (
    "此名稱列於食藥署「未確認安全性尚不得使用之原料」清單。"
    "本系統僅陳述此一官方紀錄，不就本產品是否合規作判斷，實際情形請以主管機關認定為準。")


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


def _load_raw_material_names(cursor) -> list[str]:
    """
    載入食藥署原料清單的可比對名稱(中文名／外文名／學名,已拆開並列的別名)。

    中文名欄位常以「；」並列多個俗名(如「栝樓；天花粉」),需拆開才不會漏比對。
    """
    try:
        # 必須排除「未確認安全性尚不得使用之原料」（2026-08-03 修）。
        #
        # 該清單共 1,702 筆，其中 532 筆屬此分類——那是**禁用清單**，不是可食清單。
        # 原本未加條件全部載入，導致標示上出現大麻、罌粟、大麻二酚時，系統顯示
        # 「食藥署食品原料清單收錄之原料」，把「不得使用」講成了「官方收錄」，
        # 意思完全相反。此類名稱改由 _load_restricted_raw_material_names 另行載入。
        cursor.execute(
            "SELECT name_zh, name_foreign, name_sci FROM raw_materials "
            "WHERE category_minor IS NULL OR category_minor NOT LIKE %s",
            ("%不得使用%",))
        rows = cursor.fetchall()
    except Exception:
        return []  # 表不存在(尚未匯入)時靜默降級,不影響既有流程

    names = set()
    for r in rows:
        for field in ("name_zh", "name_foreign", "name_sci"):
            val = r.get(field)
            if not val:
                continue
            for piece in re.split(r"[;；,，\n]", str(val)):
                piece = normalize_text(piece)
                if len(piece) >= 2:
                    names.add(piece)
    # 由長到短:先試較長的名稱,避免短名先命中造成過度寬鬆的比對
    return sorted(names, key=len, reverse=True)


def _load_restricted_raw_material_names(cursor) -> list[str]:
    """
    載入食藥署原料清單中「未確認安全性尚不得使用之原料」的可比對名稱。

    與可食原料清單分開處理（2026-08-03）：命中此清單**不是**正面依據，不可計入
    涵蓋率，也不可沿用「清單收錄之原料」的說明——那會把禁用講成核准。
    命中時據實陳述它列於哪一份清單即可，仍不對該產品作合規與否的判斷。
    """
    try:
        cursor.execute(
            "SELECT name_zh, name_foreign, name_sci FROM raw_materials "
            "WHERE category_minor LIKE %s", ("%不得使用%",))
        rows = cursor.fetchall()
    except Exception:
        return []

    names = set()
    for r in rows:
        for field in ("name_zh", "name_foreign", "name_sci"):
            val = r.get(field)
            if not val:
                continue
            for piece in re.split(r"[;；,，\n]", str(val)):
                piece = normalize_text(piece)
                if len(piece) >= 2:
                    names.add(piece)
    return sorted(names, key=len, reverse=True)


def _match_raw_material(norm: str, names: list[str]) -> str | None:
    """
    比對原料清單。**先精確相等,再子字串且名稱須達 3 字**。

    這個兩段式規則是實測決定的(2026-07-26,以資料庫內 6 筆真實商品共 22 種
    相異成分測試):
    - 只用子字串且門檻 2 字 → 「檸檬酸」會命中原料「檸檬」,屬誤判
    - 只用子字串且門檻 3 字 → 「蔗糖」只有兩字,會被漏掉
    - 先精確相等再子字串≥3 → 蔗糖、麥芽糊精、棕櫚油、葡萄柚皆正確命中,
      且該批資料中零誤判

    註:本函式只在添加物側完全沒命中時才會被呼叫,故「檸檬酸」這類本就會先
    命中添加物庫者不受影響;此規則防的是添加物庫漏掉時的二次誤判。
    """
    for n in names:
        if n == norm:
            return n
    for n in names:
        if len(n) >= 3 and n in norm:
            return n
    return None


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
      1. 正式名或別名與成分名**完全相等** —— 最可信，直接採用
      2. INS／E 編號相符 —— 官方編號，可信度次之
      3. 子字串命中 —— 取**匹配長度最長**者（比對到越多字，偶然吻合的機率越低）
    同級之間取匹配字串較長者。完全沒有命中則回傳 None。
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
        elif a['_n_ins'] and a['_n_ins'] in norm:
            hit = (1, len(a['_n_ins']))
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


def match_ingredients(ing_list_raw, vision_data, cursor, vector_rag,
                      ingredients_raw=None) -> dict:
    """
    比對成分清單與添加物知識庫,輸出分類結果。

    2026-07-26 新增涵蓋率統計(coverage):原本「比對不上添加物庫就當成一般
    成分」是預設而非確認,導致「真的是原料」與「其實是添加物只是寫法對不上」
    混在同一堆,無從得知一張成分表實際涵蓋多少。現在額外記錄每項成分的判定
    依據,使涵蓋率成為可量測的數字。

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
      coverage: 分類涵蓋率統計(見 _build_coverage)
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
        a['_n_ins'] = normalize_text(a.get('ins_or_e_number') or '')
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
    raw_material_names = _load_raw_material_names(cursor)
    restricted_raw_names = _load_restricted_raw_material_names(cursor)

    calculated_ingredient_types = {}
    # 每項成分的判定依據,供涵蓋率統計:
    #   additive_db / vector / raw_material_db / water / none
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

        # 原料庫正面比對：僅在添加物側完全沒命中時才查，添加物判定優先度不變
        # （同一名稱兩邊都收錄時仍以添加物為準，行為與本次新增前一致）。
        if not match and matched_by is None:
            if any(_match_raw_material(c, raw_material_names) for c in candidates):
                matched_by = "raw_material_db"
            elif any(_match_raw_material(c, restricted_raw_names) for c in candidates):
                # 列於「未確認安全性尚不得使用」清單。刻意排在可食清單之後判斷，
                # 且**不計入涵蓋率**（見 _build_coverage 的 covered_bases）——
                # 這不是「查到了所以有依據」，而是查到了一份性質相反的紀錄。
                matched_by = "restricted_raw_material"

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
        elif ai_desc:
            final_desc = ai_desc
            final_purpose = ai_purpose
            # 如果資料庫已有此項但沒描述，或根本沒有此項，則加入更新清單
            db_updates.append({
                "name": ing,
                "description": ai_desc,
                "purpose": ai_purpose,
                "exists": bool(match),
                "id": match['id'] if match else None
            })
        elif match:
            final_desc = f"此成分為{match['food_tech_purpose'] or '食品添加物'}，建議依個人體質適量攝取。"
            final_purpose = match['food_tech_purpose']
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
        #   1. 添加物庫命中（精確／別名／編號／向量）→ additive
        #   2. 其餘一律 → ingredient
        # 注意「ingredient」在此僅表示「非添加物」，不代表已確認為食品原料；
        # 是否確認為原料、或根本未收錄，另由 match_basis 與 basic_detail 區分
        # （generic_term／raw_material_db／none 三態），呈現端據此誠實標示。
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
                group_risks.append({
                    "group": en_group,
                    "riskLevel": CONCERN_TO_LEVEL.get(r.get("concern", "caution"), 1),
                    "reason": r.get("ai_reasoning") or r.get("source_quote") or zh_group,
                    "confidence": r.get("confidence", ""),
                })

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
            elif matched_by == "raw_material_db":
                basic_desc = "食藥署食品原料清單收錄之原料。"
            elif matched_by == "restricted_raw_material":
                basic_desc = RESTRICTED_RAW_DESC
            elif matched_by == "generic_term":
                basic_desc = GENERIC_TERM_DESC.format(
                    term=normalize_text(_strip_brackets(ing)))
            else:
                basic_desc = NOT_IN_DB_DESC_INGREDIENT
            basic_detail.append({
                "name": ing,
                "sourceLabel": source_label,   # 由哪一項標示展開而來（未展開者為 None）
                "isAdditive": False,
                "inDatabase": matched_by in ("raw_material_db", "water"),
                # 標示僅給類別名，無法判定為添加物或原料——這是第三種狀態，
                # 不應歸入任一邊（見 GENERIC_TERM_DESC）。
                "isGenericTerm": matched_by == "generic_term",
                "description": basic_desc,
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
    統計本張成分表的分類涵蓋率。

    「已涵蓋」定義為**有本系統資料庫的正面依據**（添加物庫、向量比對、原料庫、
    水類），不包含只靠 Gemini 分類或完全無依據者——把 LLM 的判斷算進涵蓋率
    等於自己給自己灌水，那個數字就失去意義了。

    unknown_items 保留原始名稱，這是後續要補哪些資料的直接清單。
    """
    total = len(match_basis)
    by_basis = {}
    for b in match_basis.values():
        by_basis[b] = by_basis.get(b, 0) + 1

    covered_bases = ("additive_db", "vector", "raw_material_db", "water")
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
        # 分母已排除類別統稱；欲還原成「佔全部成分」請用 covered / total
        "coverage_rate": round(covered / denominator, 3) if denominator else None,
        "denominator": denominator,
        "by_basis": by_basis,
        "unknown_items": unknown_items,
        "generic_terms": generic_items,
    }
