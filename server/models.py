from sqlalchemy import Column, String, Integer, Float, JSON, Boolean, ForeignKey, Text
from database import Base

class TFDABaseNutrition(Base):
    __tablename__ = "tfda_base_nutrition"
    food_id = Column(String(50), primary_key=True, index=True)
    name = Column(String(255))
    category = Column(String(100))
    energy_100g = Column(Float)
    protein_100g = Column(Float)
    fat_100g = Column(Float)
    saturated_fat_100g = Column(Float, nullable=True)
    carbohydrates_100g = Column(Float)
    sugar_100g = Column(Float)
    fiber_100g = Column(Float, nullable=True)
    sodium_100g = Column(Float)

class Producer(Base):
    __tablename__ = "producers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True)
    license_number = Column(String(100), nullable=True) # 工廠登記編號
    risk_level = Column(String(20), default="Low") # Low, Medium, High
    safety_history = Column(JSON, nullable=True) # 歷年稽查紀錄
    last_audit_date = Column(String(50), nullable=True)

class Product(Base):
    __tablename__ = "products"
    barcode = Column(String(50), primary_key=True, index=True)
    name = Column(String(255))
    brand = Column(String(100))
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=True) # 關聯生產商
    manufacturer = Column(String(255), nullable=True)
    country_of_origin = Column(String(100), nullable=True)
    
    # 營養標示 (Per 100g/ml)
    calories = Column(Float) # Energy (kJ/kcal)
    protein = Column(Float) # 正面因素
    fat = Column(Float)
    saturated_fat = Column(Float, nullable=True) # 負面因素
    carbohydrates = Column(Float)
    sugar = Column(Float) # 負面因素
    fiber = Column(Float, nullable=True) # 正面因素
    sodium = Column(Float) # 負面因素 (mg)
    
    # 標章與其他
    serving_size = Column(Float, nullable=True)
    servings_per_container = Column(Float, nullable=True)
    other_nutrition = Column(JSON, nullable=True)
    ingredients_list = Column(JSON, nullable=True)
    ingredients_raw = Column(Text)
    allergens = Column(JSON)
    is_estimated = Column(Boolean, default=True)
    ref_food_id = Column(String(50), ForeignKey("tfda_base_nutrition.food_id"), nullable=True)
    processing_level = Column(Integer, nullable=True)
    processing_description = Column(Text, nullable=True)
    certifications = Column(JSON, nullable=True)
    
    # 預存之 AI 診斷總結
    overall_summary = Column(Text, nullable=True)
    additives_summary = Column(Text, nullable=True)
    safety_events_summary = Column(Text, nullable=True)

class Additive(Base):
    __tablename__ = "additives"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(String(50), unique=True, index=True, nullable=True)
    name_zh = Column(String(255), index=True)
    name_en = Column(String(255), index=True, nullable=True)
    aliases = Column(JSON, nullable=True)
    ins_or_e_number = Column(String(100), index=True, nullable=True)
    category = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    
    food_tech_purpose = Column(Text, nullable=True)
    adi = Column(String(255), nullable=True)
    jecfa_summary = Column(Text, nullable=True)
    iarc_class = Column(String(50), nullable=True)
    medical_caution = Column(Text, nullable=True)
    transparency_level = Column(Integer, nullable=True)
    
    is_allergen = Column(Boolean, default=False)
    allergen_details = Column(JSON, nullable=True)
    
    risks = Column(JSON, nullable=True)
    regulatory_status_tw = Column(String(255), nullable=True)
    description_confidence = Column(String(100), nullable=True)
    description_sources = Column(JSON, nullable=True)

class RawMaterial(Base):
    """
    食藥署「食品原料整合查詢平臺」原料清單（2026-07-26 匯入，1,702 筆）。

    用途不是判斷合法性——食藥署本身即說明未列於清單者不代表不得使用，故
    category_major 僅作為附註保留，不用於任何阻擋或警示邏輯。

    真正的用途是**替成分分類補上另一邊的正面依據**。原本的邏輯是「比對得上
    添加物庫就是添加物，比對不上就是一般成分」——後半句並非確認，而是預設，
    導致「真的是原料」與「其實是添加物但寫法對不上」兩者混在一起，無法得知
    一張成分表實際涵蓋了多少。有了本表即可分出第三態「兩邊都沒命中」，
    分母才成立。

    已知涵蓋範圍偏重天然物、植物、菌種、香辛料；常見加工原料（如棕櫚油、
    脫脂奶粉、麵粉）未必收錄，故本表能提高涵蓋率但不會使其達到 100%——
    這正是保留「不明」一態的原因。
    """
    __tablename__ = "raw_materials"
    id = Column(Integer, primary_key=True, index=True)
    seq = Column(String(20), nullable=True)
    category_major = Column(String(100), index=True, nullable=True)
    category_minor = Column(Text, nullable=True)
    # 名稱一律用 Text：實測來源資料的學名與外文名有多筆超過 255 字元
    # （多個學名或多個英文俗名以逗號並列於同一欄）。
    name_zh = Column(Text, nullable=True)
    name_foreign = Column(Text, nullable=True)
    name_sci = Column(Text, nullable=True)
    part = Column(Text, nullable=True)
    remarks = Column(Text, nullable=True)


class SafetyAlert(Base):
    __tablename__ = "safety_alerts"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500))
    content = Column(String(2000))
    alert_date = Column(String(50))
    keyword_used = Column(String(50))
    source_url = Column(String(500), nullable=True)
    source_type = Column(String(20), nullable=True) # official / news / social
    severity = Column(Integer, nullable=True) # 1 / 2 / 3
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=True) # 關聯特定廠商


# =============================================================================
# Module C v1 — 食安事件管線
#
# 讀寫迴圈分離：
#   讀取迴圈（使用者掃描・即時・全自動）只查詢下方三張「已發布」表。
#   寫入迴圈（排程發現・離線・人工審核）操作 EventCandidate 佇列，
#   核准後才寫入已發布表。候選佇列對使用者永遠不可見。
#
# 設計上刻意「沒有」的欄位（皆為 v1 明確否決，不是遺漏）：
#   - severity：只能由官方裁罰紀錄推導，而該紀錄對本系統廠商實測為零筆。
#   - 角色欄位（違規者／受影響者）：事件名稱已承載主體，
#     「聯華涉及中聯油脂事件」在語法上就沒有指控聯華。
# =============================================================================

class Event(Base):
    """已發布的食安事件。一年約 5–10 筆，人工建立。"""
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False)   # 事件名稱，如「2026 中聯油脂致癌油事件」
    start_date = Column(String(20), nullable=True)  # 起始日期 YYYY-MM 或 YYYY-MM-DD
    created_at = Column(String(50), nullable=True)
    # incident = 單一具體事件；compilation = 彙整報導（懶人包／編年史／歷年回顧，涵蓋多起事件）
    # 讀取端據此區分呈現：事件帶警示語氣，彙整報導僅為延伸閱讀，不帶同等份量。
    kind = Column(String(20), default="incident")


class EventSource(Base):
    """事件的來源文件。標題一律照抄原文，不生成句子。"""
    __tablename__ = "event_sources"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    title = Column(String(500), nullable=False)      # 照抄原文標題，不改寫
    url = Column(String(1000), nullable=False)
    source_tier = Column(String(20), nullable=False) # official / news / social / self_published / unknown
    published_date = Column(String(20), nullable=True)


class EventManufacturer(Base):
    """
    事件 ↔ 廠商關聯。一個事件可跨多家廠商（如中聯油脂事件同時涉及聯華、味全）。

    role（2026-07-25 新增）：此廠商在本事件中的角色，供讀取端呈現時區分
    「元凶」與「受波及的下游業者」，避免兩者被混為一談呈現成同一種
    「該廠商曾涉及食安事件」標籤——這對主動配合下架回收的下游業者並不公平，
    也可能誤導消費者。

    刻意由人工於審核台設定，不由 LLM 判定——與 clustering.py／query.py
    既有原則一致（「LLM 不判斷責任歸屬」），role 屬於需要人工查證的事實
    判斷，不是可由模型自動推論的分類任務。
    """
    __tablename__ = "event_manufacturers"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=False, index=True)
    # perpetrator（元凶／違規方）／affected_downstream（受影響下游業者）／
    # self_reported（自主通報）／None（尚未判定，呈現端須視同「未知」處理，
    # 不可預設為任何一種角色）
    role = Column(String(30), nullable=True)


class EventCandidate(Base):
    """
    候選佇列 — 使用者永遠看不到這張表。

    Stage 2 通過硬性閘門的文件落於此，等待 Stage 3 人工審核。
    LLM 只在此處提供「分群建議 + 理由」，不具最終決定權。
    """
    __tablename__ = "event_candidates"
    id = Column(Integer, primary_key=True, index=True)

    # Stage 0 實體解析結果
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=False, index=True)
    is_ambiguous = Column(Boolean, default=False)   # 文件同時提及易混淆兄弟法人
    ambiguous_with = Column(JSON, nullable=True)    # 具體是跟誰混淆，人工需判斷主體
    # 來源：manufacturer_search（廠商導向檢索）／memory_seed（LLM 記憶種子經 Tavily 驗證）
    discovery_method = Column(String(30), nullable=True)

    # Stage 1 檢索所得（原文照抄，不改寫）
    title = Column(String(500), nullable=False)
    url = Column(String(1000), nullable=False, index=True)
    snippet = Column(Text, nullable=True)
    published_date = Column(String(20), nullable=True)

    # Stage 2 閘門結果
    source_tier = Column(String(20), nullable=True)  # official / news / social / self_published / unknown
    matched_terms = Column(JSON, nullable=True)      # 命中的受控詞彙

    # LLM 分群建議（僅供人工參考，強制附理由，不自動決定）
    llm_kind = Column(String(20), nullable=True)        # incident / compilation / not_an_event
    llm_cluster_id = Column(String(50), nullable=True)  # 同一次回合內的分組代號（A/B/...）
    llm_suggested_name = Column(String(500), nullable=True)  # 建議名稱（草稿，人工可改）
    llm_cluster_reason = Column(Text, nullable=True)

    # Stage 3 人工審核
    status = Column(String(20), default="pending", index=True)  # pending / approved / rejected
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)  # 核准後掛到哪個事件
    discovered_at = Column(String(50), nullable=True)
    reviewed_at = Column(String(50), nullable=True)


class ScanUpload(Base):
    """使用者與測試者上傳的標示照片紀錄。

    刻意與 products 分開。products 存的是「已採用的產品資料」，這張表存的是
    **原始輸入與當下的辨識結果**——同一張照片換個讀取器就會得到不同的值，
    把它塞進 products 等於讓共享資料庫跟著每次實驗變動。

    閘門沒過的照片也收。它們先前在 `_save_scan_images` 之前就被 return 掉，
    而那些正是最值得拿來改管線的樣本：能通過閘門的照片代表管線已經讀得動它了。
    gate_passed 記下當下的判定，之後要只取通過的或只取失敗的都查得到。

    sha256 是原圖位元組的雜湊，與 `測試/量化測試/manifest_tool.py` 同一套
    識別方式，收進測試集時可以直接比對是不是同一張，不必靠檔名。
    """
    __tablename__ = "scan_uploads"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(String(32), index=True)          # ISO8601、UTC
    tester_id = Column(String(64), nullable=True, index=True)  # X-Tester-Id，一般使用者為 NULL
    barcode = Column(String(50), nullable=True, index=True)    # 沒掃條碼就是 NULL，不填 IMG_xxx
    image_index = Column(Integer)                        # 同一次請求裡的第幾張
    stored_path = Column(String(255))                    # /uploads/<檔名>
    sha256 = Column(String(64), index=True)
    size_bytes = Column(Integer)
    request_id = Column(String(64), nullable=True, index=True) # X-Request-Id，可與三層計時對起來
    vision_backend = Column(String(32), nullable=True)   # 當下的讀取器，換模型後才分得出新舊樣本
    gate_passed = Column(Boolean, default=False, index=True)
    gate_reason = Column(String(255), nullable=True)     # 沒過的原因，通過時為 NULL
    recognized = Column(JSON, nullable=True)             # 當下的辨識結果，不是正解
