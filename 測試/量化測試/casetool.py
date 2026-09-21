#!/usr/bin/env python3
# 測試案例的建檔與一致性檢查。
#
# 為何需要：新增一個案例要同時動四個地方——照片放進 images/<case_id>/、
# cases.json 加一筆、ground_truth/<case_id>.json 建正解、manifest 重新產生。
# 漏掉任一個的後果都不會立刻報錯，而是安靜地少評一案或多算一案；
# 要補 25 個案例時，這種錯誤幾乎必然發生一次。
#
# 用法：
#   python casetool.py check                       # 檢查四處是否一致（先跑這個）
#   python casetool.py new c50_保健食品膠囊 --non-food \
#       --desc="某某葉黃素膠囊外盒" --difficulty=small_text
#   python casetool.py new c60_反光鋁袋洋芋片 --category=snack \
#       --desc="某某洋芋片" --difficulty=glare,crease
#
# new 只建骨架，正解仍須人工填寫——那是本測試集唯一不可由程式產生的東西。
# 非食品案例例外：其正解只有 is_food_label=false 一個欄位，可直接寫完。
import json
import os
import re
import shutil
import sys

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, 'cases.json')
GT = os.path.join(HERE, 'ground_truth')
IMAGES = os.environ.get('EVAL_IMAGE_ROOT') or os.path.join(HERE, 'images')
MANIFEST = os.path.join(HERE, 'manifest.json')

_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.heic')

# 刻意只留四個（2026-08-10 自七個縮減）：特徵要少到每個都養得起樣本——
# 10 張新案例攤給七種條件是每種 n=1，切片無判讀價值。被移除者的去處見
# README「困難食品」一節（small_text 由品項軸涵蓋、low_light 由 blurry 承接、
# angled/occluded 待切片顯示需要再加回）。
VALID_DIFFICULTY = {'glare', 'curved', 'crease', 'blurry'}

# 四個標籤分屬兩族（2026-08-11）。分族的依據是「重拍能不能改善」，
# 而這決定了失敗時的正確處置：
#   shot 拍攝造成 —— 可由重拍或改變角度改善，App 應提示使用者重拍
#   pkg  包裝自帶 —— 圓瓶仍是圓的、皺褶仍在，重拍無效，須由系統自行處理
# 另一個作用是把四個薄切片併成兩個較厚的（實測 12 案 / 8 案，遠優於各自 1–8 案）。
# 注意 glare 概念上屬拍攝端，但實測 8 案中 7 案為零食——鋁箔材質使然，
# 故它與品項軸的共線程度接近 pkg 族，解讀時不可視為獨立因子（見 README）。
DIFFICULTY_FAMILY = {'blurry': 'shot', 'glare': 'shot',
                     'curved': 'pkg', 'crease': 'pkg'}
# 2026-08-11 自七類縮減為四類＋非食品。canned_food(1 案)與 supplement_food(2 案)
# 併入 snack——理由同 difficulty 詞彙的縮減：n=1、n=2 的切片沒有判讀價值，
# 類別要少到每格養得起樣本。案例本身未刪除，只是不再各自成為一個切片。
# non_food 不是品項而是負例類別，獨立於前四類之外，v3.0 要補的 15 案全在此格。
#   beverage 飲料／prepared_meal 調理食品（便當、飯糰、三明治、涼麵等）
#   snack 零食／instant_noodle 泡麵／non_food 非食品
# 2026-09-02 自四類擴為六類。0901 批加入 100 案，其中醬料 16、餅乾 23
# 各自都養得起一個切片（現有 snack 才 11 案、instant_noodle 4 案），
# 而且它們在**包裝形狀與反光特性**上與既有類別不同——醬料是玻璃／PET 曲面、
# 餅乾是紙盒霧面，那正是教授 08-29 指定的選品維度。
# 糖果 5 案併入 snack、湯粉／調味粉 5 案併入 sauce，理由同上方那條原則：
# n=5 的切片讀不出東西。
#   sauce 醬料與調味料（不直接食用，用於調味）
#   biscuit 餅乾（含夾心餅、蘇打餅、薄餅）
VALID_CATEGORY = {'beverage', 'prepared_meal', 'snack', 'instant_noodle',
                  'sauce', 'biscuit', 'non_food'}

# 正解的產生方式。記錄它是為了能算出「只看手工轉錄案例」的分數——
# LLM 起草的正解與受測對象可能共用同一種誤讀，那一格會變成「模型答對」，
# 分數在模型最容易出錯的地方被系統性灌水（同一個坑 ingredient_types 踩過一次，
# 見 score_eval.py 該區塊的廢止理由）。有了這個欄位，兩組數字可以互相對照，
# 差距本身就是校對品質的證據。
#   hand         人工逐字轉錄
#   llm_checked  LLM 起草、人工校對過
#   unknown      2026-08-11 開始記錄之前建立的案例，來源已不可考
VALID_GT_SOURCE = {'hand', 'llm_checked', 'unknown'}

# 包裝形狀與反光材質（教授 08-29 建議 13）。
#
# **這兩欄是選品的描述,不是分析的切片。** 教授要的是能證明配對組涵蓋了不同的
# 物理特性——影響 OCR 的是曲面、皺褶、鋁箔鍍膜,不是「這是零食還是飲料」,
# 所以分層維度要用材質與形狀,不要用商品類別。
#
# ⚠ **不可以拿它分組報數字。** 32 組切成四種材質是 n=8,量不出任何東西
# （添加物層 57 案都分辨不出 8 點以下的差異）。配對實驗的 README 自己就寫了
# 「材質只作事後描述,不拿來分組報數字」。
#
# 值是**商品的屬性,不是照片的屬性**——低反光臂與高反光臂共用同一個值。
VALID_PKG_SHAPE = {
    'carton',       # 平面紙盒
    'can',          # 圓柱罐
    'pouch',        # 軟袋
    'bottle',       # 曲面瓶
    'shrink_wrap',  # 收縮膜
    'tray',         # 塑膠盒（含微波餐盒）
    'cup',          # 杯裝（杯麵、杯湯）
}
VALID_REFLECT = {
    'foil',            # 鋁箔鍍膜（軟袋鍍鋁內層：洋芋片、調理包）
    'glossy_plastic',  # 亮面塑膠膜
    'matte_paper',     # 霧面紙（紙盒、紙罐）
    'clear_film',      # 透明膜（看得到內容物）
    'metal',           # 金屬罐（鋁罐、鐵罐）      2026-09-06 補
    'glass',           # 玻璃瓶罐                2026-09-06 補
}
# **判準：reflect 看最外層，pkg_shape 看容器**（2026-09-06 定）。
#   reflect     記「光打得到的那一面」——這欄描述的是反光，而反光產生在最外層。
#               杯麵外面套印刷收縮膜就記那層膜；紙罐包亮面膜記膜不記紙；
#               裡面是什麼材質，光到不了就與辨識無關。
#   pkg_shape   記容器本身的幾何。外面包一層膜不會讓杯子變成別的東西，
#               套了收縮膜的杯麵仍是 cup。`shrink_wrap` 只給「沒有硬容器、
#               整包就是一層膜」的情況——所以目前 0 案是對的，不是漏填。
#   clear_film  意思是**最外層是透明膜**，不是「看得到食物」。整袋印刷、
#               只有一小塊開窗的（c147 炒麵袋）算 glossy_plastic。
#
# 實物判準（照片上看不出來的兩組）：
#   紙罐 vs 金屬罐    看罐底有沒有金屬捲邊
#   鋁箔 vs 亮面塑膠  看撕口斷面或內層是不是銀色
#
# metal 與 glass 是 2026-09-06 補的。教授舉例時列的四個值
# （鋁箔鍍膜／亮面塑膠／霧面紙／透明膜）是照**軟性包裝**寫的，
# 但 31 個配對案例裡有 13 案（42%）是鋁罐、玻璃罐或紙罐——沒有正確選項可選，
# 結果 21 個 can 幾乎全被硬塞成 glossy_plastic，把真正的鋁箔軟袋
# （多力多滋、卡迪那）跟鋁罐混進同一格。補值是為了讓紀錄對應實物，
# 不是改變定義；這欄不進任何數字，改動可逆。

# 正解不唯一而不計分的理由。**值域現在鎖死,不得因為分數難看而新增。**
#
# 起因 c134_旺旺小小酥：一個包裝內並排兩個商品的營養標示（輕辣 571 大卡／
# 香蔥雞汁 555 大卡），兩欄都是對的。讀取器沒有任何線索知道該挑哪一欄,
# 把它算成錯是在量「標註者挑了哪一欄」,不是在量系統。
#
# ⚠ 這個機制的存在本身有風險——它讓「事後排除不方便的案例」變得容易,
# 而那正是教授從 07-30 起一直在防的事。三道約束：
#   1. 理由必須是**包裝的性質**,不能是分數的性質
#   2. 排除哪些欄位由理由決定（下面這張表）,不可逐案挑
#   3. 值域要改必須留紀錄,不能靜默加一個
#
# 分數上其實不需要這個欄位：正解留 null 時 score_eval.num_ok() 回 None,
# 那一格本來就跳過。它存在是為了**可稽核**——120 案裡有 23 案 nutrition 全空,
# 沒有這個標記就分不出「刻意排除」與「忘了填」。
VALID_EXCLUDE_REASON = {'multi_product_panel'}
EXCLUDE_FIELDS_BY_REASON = {
    'multi_product_panel': ('nutrition', 'nutrition_per_serving',
                            'servings_per_container'),
    # serving_size 不在內：c134 兩個口味都是 30 公克,沒有歧義
}

# 逐字原文本身的瑕疵，**來源是包裝印刷而非轉錄錯誤**。
#
# 起因 c60_乖乖玉米脆條蝦乖乖：`蝦粉(…、蝦香精))` 多印一個收尾括號，對照照片
# 確認是廠商印錯。正解的規矩是逐字照抄，所以那個括號必須留著——但那會讓
# `gt_split.py` 的機械切分永遠失敗、每次跑都叫人「去修 raw」，遲早有人真的去改，
# 逐字原文就毀了。標記起來，把「印刷錯誤」與「還沒轉錄好」分開。
#
# ⚠ 值域鎖死，標之前要對著照片確認。切分時該案的括號深度夾在 0 以上
# （多出來的收尾括號＝後面的成分回到最上層）。
VALID_RAW_ISSUE = {'print_error_bracket'}

# 高反光臂的存放處。有這個資料夾的案例就是配對案例,pkg_shape/reflect 必填。
PAIR_DIRTY = os.path.join(HERE, 'images_pair_dirty')


def paired_ids():
    """有拍高反光配對臂的 case_id。掃 _待轉（未轉檔）與 images（已就位）兩處。"""
    out = set()
    for sub in ('_待轉', 'images'):
        root = os.path.join(PAIR_DIRTY, sub)
        if not os.path.isdir(root):
            continue
        for cat in os.listdir(root):
            d = os.path.join(root, cat)
            if os.path.isdir(d):
                out |= {cid for cid in os.listdir(d) if os.path.isdir(os.path.join(d, cid))}
    return out

# 認證標章的標準名稱。**只收食品驗證**——2026-09-02 定案。
#
# 範圍：包裝材料認證（FSC，紙材來源）與素食標示（全素／奶素／植物五辛素）
# **不收**。前者與食品安全無關；後者不是第三方驗證而是廠商自我宣告，
# 性質不同，要收的話應另立欄位。
#
# 一律用英文簡稱，比對時逐字相同才算對。
# ⚠ 下面三個**沒有官方英文簡稱，是本專案自訂的慣例**，不要當成標準引用：
#     HEALTHFOOD  健康食品標章（小綠人）。官方英文只有 "Health Food Mark"
#     FRESHMILK   鮮乳標章。官方英文只有 "Fresh Milk Mark"
#     ORGANIC     有機農產品驗證
# 其餘為官方簡稱。
#
# ⚠ **這個欄位的天花板只有 64%**：現有 25 個標章裡，OCR 文字讀得到的只有 16 個
# （2026-09-02 實測）。TQF 有些版本只有圖案沒有字母，標註者辨識的是**圖形**。
# 也就是說這不是 OCR 任務，驗收時要註明「僅計文字可讀者」。
VALID_CERT = {
    'TQF',         # Taiwan Quality Food 台灣優良食品
    'CAS',         # Certified Agricultural Standards 台灣優良農產品
    'TAP',         # Traceable Agricultural Product 產銷履歷
    'HACCP',
    'ISO22000',
    'HALAL',       # 清真
    'ORGANIC',     # 有機農產品驗證（自訂）
    'HEALTHFOOD',  # 健康食品／小綠人（自訂）
    'FRESHMILK',   # 鮮乳標章（自訂）
}



# 食品案例的正解骨架。欄位名與 score_eval.py 讀取的一致；填不到的留 null，
# **不可填 0**——「標示上沒有」與「含量為零」是兩件事，混淆會讓評分失真。
#
# ⚠ **`nutrition`（每 100 公克）整欄是 null 通常不是漏抄。** 台灣的營養標示
# 法規允許兩種格式：
#     格式一   每一份量 ＋ 每份 ＋ 每 100 公克（或毫升）
#     格式二   每一份量 ＋ 每份 ＋ **每日參考值百分比**
# 用格式二的商品，每 100 公克那一欄根本不存在。2026-09-07 清點時 25 案是這個
# 狀態，一度被當成轉錄疏漏；查 OCR 文字後 14 案直接讀到「每日參考值」、
# **25 案沒有任何一案出現「每 100」**，放大照片也確認表頭是「每份｜每日參考值
# 百分比」。**那 25 案留空是正確的，不要去「修」它。**
#
# `score_eval.num_ok()` 對 null 的格子直接跳過，所以 B 類的分母本來就是
# 「標示上真的有印的格子」，會因商品而異——這是設計，不是缺陷。
#
# 「每日參考值百分比」目前**刻意不收**：下游要的是每份的絕對值（Nutri-Score
# 與閾值警示都用絕對量），百分比是拿參考值換算出來的衍生量。這是範圍宣告，
# 要寫進驗收文件，不是默默沒有。
FOOD_SKELETON = {
    "is_food_label": True,
    "name": "", "brand": "", "manufacturer": "",
    "ingredients_raw": "",
    "ingredients_list": [],
    "nutrition": {k: None for k in
                  ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
                   'carbohydrates', 'sugar', 'fiber', 'sodium']},
    "nutrition_per_serving": {k: None for k in
                              ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
                               'carbohydrates', 'sugar', 'fiber', 'sodium']},
    "serving_size": None,
    "servings_per_container": None,
    "allergy_warning": "",
    "certification_marks": [],
}


def load_cases():
    return json.load(open(CASES, encoding='utf-8'))


def save_cases(data):
    with open(CASES, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def images_for(case_id, category):
    """列出 images/<category>/<case_id>/ 底下的圖片，回傳相對於 images root 的路徑。"""
    d = os.path.join(IMAGES, category, case_id)
    if not os.path.isdir(d):
        return []
    return [f'images/{category}/{case_id}/{fn}' for fn in sorted(os.listdir(d))
            if fn.lower().endswith(_EXTS)]


def gt_path(case_id, category):
    return os.path.join(GT, category, f'{case_id}.json')


def scan_gt():
    """{case_id: 所在類別資料夾}。底線開頭的資料夾為歸檔區，略過。"""
    out = {}
    if not os.path.isdir(GT):
        return out
    for cat in os.listdir(GT):
        d = os.path.join(GT, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for fn in os.listdir(d):
            if fn.endswith('.json'):
                out[fn[:-5]] = cat
    return out


def scan_img_dirs():
    """{case_id: 所在類別資料夾}。"""
    out = {}
    if not os.path.isdir(IMAGES):
        return out
    for cat in os.listdir(IMAGES):
        d = os.path.join(IMAGES, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for cid in os.listdir(d):
            if os.path.isdir(os.path.join(d, cid)):
                out[cid] = cat
    return out


# ─── check ────────────────────────────────────────────────────────────────────

def check():
    data = load_cases()
    cases = {c['case_id']: c for c in data['cases']}
    problems = []

    gt_at = scan_gt()          # case_id → 正解所在的類別資料夾
    img_at = scan_img_dirs()   # case_id → 圖片所在的類別資料夾
    _paired = paired_ids()     # 有高反光配對臂者，pkg_shape/reflect 必填

    for cid in sorted(set(cases) - set(gt_at)):
        problems.append(f"[缺正解] {cid} 在 cases.json 內，但 ground_truth/ 底下找不到"
                        f" → 該案會被靜默略過，不列入任何指標")
    for cid in sorted(set(gt_at) - set(cases)):
        problems.append(f"[孤兒正解] ground_truth/{gt_at[cid]}/{cid}.json"
                        f" 沒有對應的 cases.json 項目")
    for cid in sorted(set(img_at) - set(cases)):
        problems.append(f"[未登記] images/{img_at[cid]}/{cid}/ 存在，"
                        f"但 cases.json 沒有這個案例")

    for cid, c in sorted(cases.items()):
        # 圖片實際存在
        for rel in c.get('images', []):
            p = os.path.join(IMAGES, rel[len('images/'):]) if rel.startswith('images/') \
                else os.path.join(HERE, rel)
            if not os.path.exists(p):
                problems.append(f"[圖片不存在] {cid} → {rel}")
        if not c.get('images'):
            problems.append(f"[無圖片] {cid} 沒有列出任何照片")

        # 欄位完整
        sv = c.get('set_version')
        if not sv:
            problems.append(f"[缺 set_version] {cid} → 不會出現在任何版本切片中")
        elif not re.fullmatch(r'v\d+\.\d+', sv):
            problems.append(f"[版號格式錯誤] {cid} → '{sv}'，應為 v<主>.<次>（如 v2.1）")
        cat = c.get('category')
        if not cat:
            problems.append(f"[缺 category] {cid}")
        elif cat not in VALID_CATEGORY:
            problems.append(f"[未知 category] {cid} → '{cat}'（可用：{sorted(VALID_CATEGORY)}）")

        # 類別現在同時存在於兩處：cases.json 的欄位、以及資料夾位置。
        # 兩者必須一致——分類改了卻只改一邊，就會出現「JSON 說是零食、
        # 檔案卻放在飲料資料夾」的狀態，人工審核與程式評分看到的分類不同。
        # 此檢查是允許用資料夾表達分類的前提。
        if cat and cid in img_at and img_at[cid] != cat:
            problems.append(f"[分類不一致] {cid} category={cat}，"
                            f"但圖片放在 images/{img_at[cid]}/")
        if cat and cid in gt_at and gt_at[cid] != cat:
            problems.append(f"[分類不一致] {cid} category={cat}，"
                            f"但正解放在 ground_truth/{gt_at[cid]}/")
        for d in c.get('difficulty') or []:
            if d not in VALID_DIFFICULTY:
                problems.append(f"[未知 difficulty] {cid} → '{d}'"
                                f"（可用：{sorted(VALID_DIFFICULTY)}）")

        # 標章只收 VALID_CERT 裡的英文簡稱（2026-09-02 定案）。
        # 沒有這道檢查，`CAS` 與 `CAS優良農產品` 這種同物異名會靜默累積，
        # 一年後沒人知道兩者是不是同一個標章。
        gt_at_cat = gt_at.get(cid)
        if gt_at_cat:
            import json as _json
            _p = os.path.join(GT, gt_at_cat, cid + '.json')
            if os.path.exists(_p):
                _g = _json.load(open(_p, encoding='utf-8'))
                for m in _g.get('certification_marks') or []:
                    if m not in VALID_CERT:
                        problems.append(
                            f"[未知標章] {cid} → '{m}'（可用：{sorted(VALID_CERT)}）")

        # 包裝形狀／反光材質：配對案例必填（教授建議 13 要求證明選品涵蓋
        # 不同物理特性）；非配對案例填了也檢查值，沒填不算問題
        for fld, vocab in (('pkg_shape', VALID_PKG_SHAPE), ('reflect', VALID_REFLECT)):
            v = c.get(fld)
            if v and v not in vocab:
                problems.append(f"[未知 {fld}] {cid} → '{v}'（可用：{sorted(vocab)}）")
            elif not v and cid in _paired:
                problems.append(f"[缺 {fld}] {cid} 有高反光配對臂 → 無法說明選品涵蓋範圍")

        ri = c.get('raw_issue')
        if ri and ri not in VALID_RAW_ISSUE:
            problems.append(f"[未知 raw_issue] {cid} → '{ri}'"
                            f"（可用：{sorted(VALID_RAW_ISSUE)}）")

        # 不計分理由：值域鎖死，且宣告了就必須真的留空（否則等於兩套正解）
        er = c.get('exclude_reason')
        if er:
            if er not in VALID_EXCLUDE_REASON:
                problems.append(f"[未知 exclude_reason] {cid} → '{er}'"
                                f"（可用：{sorted(VALID_EXCLUDE_REASON)}）")
            else:
                _gp = gt_path(cid, gt_at.get(cid, cat or ''))
                if os.path.exists(_gp):
                    _g = json.load(open(_gp, encoding='utf-8'))
                    for fld in EXCLUDE_FIELDS_BY_REASON[er]:
                        v = _g.get(fld)
                        filled = (any(x is not None for x in v.values())
                                  if isinstance(v, dict) else v is not None)
                        if filled:
                            problems.append(
                                f"[排除欄位仍有值] {cid} 宣告 {er} 但 {fld} 有填"
                                f" → 正解不唯一時填了等於自己選一個答案")

        gs = c.get('gt_source')
        if not gs:
            problems.append(f"[缺 gt_source] {cid} → 不會出現在 by_gt_source 切片中")
        elif gs not in VALID_GT_SOURCE:
            problems.append(f"[未知 gt_source] {cid} → '{gs}'"
                            f"（可用：{sorted(VALID_GT_SOURCE)}）")

        # 正解與 category 是否自相矛盾
        gp = gt_path(cid, gt_at.get(cid, cat or ''))
        if os.path.exists(gp):
            try:
                gt = json.load(open(gp, encoding='utf-8'))
            except json.JSONDecodeError as e:
                problems.append(f"[正解格式錯誤] {cid} → {e}")
                continue
            flag = gt.get('is_food_label')
            if flag is None:
                problems.append(f"[正解缺 is_food_label] {cid} → 相關性閘門不會評到這案")
            elif cat == 'non_food' and flag is not False:
                problems.append(f"[矛盾] {cid} category=non_food 但正解 is_food_label={flag}")
            elif cat and cat != 'non_food' and flag is not True:
                problems.append(f"[矛盾] {cid} category={cat} 但正解 is_food_label={flag}")

    # manifest 是否跟得上
    if os.path.exists(MANIFEST):
        listed = set(json.load(open(MANIFEST, encoding='utf-8'))['files'])
        actual = set()
        for root, _, files in os.walk(IMAGES):
            for fn in files:
                if fn.lower().endswith(_EXTS):
                    rel = os.path.relpath(os.path.join(root, fn), IMAGES).replace(os.sep, '/')
                    actual.add(rel)
        if listed != actual:
            problems.append(f"[manifest 過期] 清單 {len(listed)} 張、實際 {len(actual)} 張"
                            f" → 跑 python manifest_tool.py generate")
    else:
        problems.append("[無 manifest] 跑 python manifest_tool.py generate")

    # 組成摘要：非食品比例是目前最該盯的數字
    by_ver, by_cat, n_diff = {}, {}, 0
    for c in cases.values():
        by_ver[c.get('set_version') or '?'] = by_ver.get(c.get('set_version') or '?', 0) + 1
        by_cat[c.get('category') or '?'] = by_cat.get(c.get('category') or '?', 0) + 1
        if c.get('difficulty'):
            n_diff += 1
    n = len(cases)
    n_nonfood = by_cat.get('non_food', 0)

    print(f"案例 {n} 件")
    print("  版本：" + "、".join(f"{k} {v}" for k, v in sorted(by_ver.items())))
    print("  類別：" + "、".join(f"{k} {v}" for k, v in sorted(by_cat.items())))
    print(f"  非食品 {n_nonfood} 件（{n_nonfood / n * 100:.0f}%）"
          f"{'  ← 負例過少，相關性閘門的精確率不可信' if n_nonfood < 5 else ''}")
    print(f"  已標困難度 {n_diff} 件"
          f"{'  ← 無人標註，by_difficulty 切片會是空的' if n_diff == 0 else ''}")

    if problems:
        print(f"\n{len(problems)} 個問題：")
        for p in problems:
            print("  " + p)
        return 1
    print("\n[OK] cases.json、ground_truth/、images/、manifest.json 四處一致。")
    return 0


# ─── new ──────────────────────────────────────────────────────────────────────

def new(argv):
    case_id = argv[0]
    opts = {}
    non_food = False
    for a in argv[1:]:
        if a == '--non-food':
            non_food = True
        elif a.startswith('--') and '=' in a:
            k, v = a[2:].split('=', 1)
            opts[k] = v
        else:
            sys.exit(f"看不懂的參數：{a}")

    data = load_cases()
    if any(c['case_id'] == case_id for c in data['cases']):
        sys.exit(f"{case_id} 已存在於 cases.json")

    category = 'non_food' if non_food else opts.get('category')
    if not category:
        sys.exit("食品案例需指定 --category=（或用 --non-food）")
    if category not in VALID_CATEGORY:
        sys.exit(f"未知 category：{category}（可用：{sorted(VALID_CATEGORY)}）")

    imgs = images_for(case_id, category)
    if not imgs:
        # 也接受照片暫放在 images/ 底下未分類處，代為搬到正確的類別資料夾——
        # 從手機匯入時很難記得先建對資料夾，讓工具處理比讓人記得可靠。
        loose = os.path.join(IMAGES, case_id)
        if os.path.isdir(loose):
            os.makedirs(os.path.join(IMAGES, category), exist_ok=True)
            shutil.move(loose, os.path.join(IMAGES, category, case_id))
            print(f"  照片已自 images/{case_id}/ 移至 images/{category}/{case_id}/")
            imgs = images_for(case_id, category)
        else:
            sys.exit(f"找不到照片。請把照片放進 "
                     f"{os.path.join(IMAGES, category, case_id)}/ "
                     f"（或暫放 {loose}/，本工具會代為歸位）")

    difficulty = [d for d in (opts.get('difficulty') or '').split(',') if d]
    bad = set(difficulty) - VALID_DIFFICULTY
    if bad:
        sys.exit(f"未知 difficulty：{sorted(bad)}（可用：{sorted(VALID_DIFFICULTY)}）")

    data['cases'].append({
        "case_id": case_id,
        "desc": opts.get('desc', ''),
        "barcode": "TEST",
        "images": imgs,
        "tags": {"is_food": not non_food},
        "set_version": opts.get('set-version') or opts.get('set_version') or 'v3.0',
        "category": category,
        "difficulty": difficulty,
        # 預設 hand：新案例的正解骨架是空的，填寫方式由人決定。
        # 若改用 LLM 起草，請自行改為 llm_checked——這個欄位的價值全在誠實。
        "gt_source": opts.get('gt-source') or opts.get('gt_source') or 'hand',
    })
    data['cases'].sort(key=lambda c: c['case_id'])
    save_cases(data)

    gp = gt_path(case_id, category)
    os.makedirs(os.path.dirname(gp), exist_ok=True)
    if os.path.exists(gp):
        print(f"[略過] {gp} 已存在，未覆寫")
    else:
        # 非食品的正解只有一個欄位，可直接完成；食品的骨架須人工填寫
        gt = {"is_food_label": False} if non_food else dict(FOOD_SKELETON)
        with open(gp, 'w', encoding='utf-8') as f:
            json.dump(gt, f, ensure_ascii=False, indent=2)

    print(f"已新增 {case_id}（{category}，{len(imgs)} 張照片"
          + (f"，困難度 {'/'.join(difficulty)}" if difficulty else "") + "）")
    if non_food:
        print("  正解已完成（非食品只需 is_food_label=false）")
    else:
        print(f"  → 請填寫 ground_truth/{category}/{case_id}.json，"
              f"照著照片上看得到的填，看不到的留 null（不可填 0）")
    print("  → 接著跑：python manifest_tool.py generate && python casetool.py check")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'check':
        sys.exit(check())
    elif cmd == 'new':
        if len(sys.argv) < 3:
            sys.exit("用法：casetool.py new <case_id> [--non-food | --category=X] "
                     "[--desc=X] [--difficulty=a,b]")
        new(sys.argv[2:])
    else:
        sys.exit("用法：casetool.py [check|new]")
