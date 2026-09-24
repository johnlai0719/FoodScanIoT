"""
Module C — Stage 4：讀取迴圈（使用者掃描時的即時查詢）

與寫入迴圈徹底分離：
  - 本模組**只查詢已發布的三張表**（events / event_sources / event_manufacturers）
  - **從不觸及 EventCandidate 候選佇列**——使用者永遠看不到「處理中」的事件
  - 全自動、零人力、毫秒級（一次 SQL join）

呈現原則：照抄原文標題 + 連結 + 來源層級，**不生成任何句子**。
使用者看到的只有兩種狀態：「有事件」或「查無公開紀錄」，兩者呈現方式相同，
不洩漏審核進度。
"""
import models

# 呈現於 UI 的範圍聲明。不可省略——這是本模組唯一的立場宣告。
#
# 核心定調（2026-07-15）：本模組的資訊單位是「此食品是否曾出過問題」之**事實**，
# 而非「誰該負責」之判斷。食品曾出過問題（如曾被回收）本身是已發生的事實，
# 責任歸屬則不在系統回答範圍內，交由來源與讀者判斷。
SCOPE_DISCLAIMER = (
    "本區呈現此商品／品牌曾涉及之公開食品安全事件。"
    "重點為「該食品是否曾出過問題」之事實揭露（例如曾被回收或查獲），"
    "非對責任歸屬之判斷——列出之廠商可能為違規者、受影響者或自主通報者，"
    "實際情形請點擊來源確認。"
)

# 社群層獨立呈現，且只給連結、不做任何判讀（2026-07-15 決議：純連結列表）。
# 刻意「不」做的事：不計數（不說「有 N 個來源指稱」）、不歸納主題、不生成句子。
# 理由：社群內容未經證實，任何聚合或詮釋都等於系統代為背書。
SOCIAL_LABEL = "相關社群討論（未經證實，內容未經本系統判讀）"

# 可據以呈現的來源層級（社群不在此列，另行拆出）
_CITABLE_TIERS = ("official", "news", "self_published", "unknown")

# 來源層級的呈現順序（官方優先）
_TIER_ORDER = {"official": 0, "news": 1, "self_published": 2, "unknown": 3}

# 角色標籤（2026-07-25 新增）。role 為 None（尚未由人工判定）時不附加任何標籤，
# 不可預設為任一角色——寧可不標示，也不能標錯。
ROLE_LABELS = {
    "perpetrator": "本事件之主要責任方",
    "affected_downstream": "受本事件波及之下游業者（非本事件違規來源）",
    "self_reported": "已主動通報／配合處理",
}


def get_events_for_producer(db, producer_id: int) -> list[dict]:
    """
    查詢某廠商已發布的食安事件。

    回傳空 list 代表「查無公開紀錄」——注意這不等於「該廠商無違規」。
    召回率未知且系統不主張（Tavily 抓不到 Facebook 社團、Instagram，
    而那是真實抱怨的主要棲息地）。
    """
    if not producer_id:
        return []

    rows = (
        db.query(models.Event, models.EventManufacturer)
        .join(models.EventManufacturer, models.EventManufacturer.event_id == models.Event.id)
        .filter(models.EventManufacturer.producer_id == producer_id)
        .order_by(models.Event.start_date.desc().nullslast())
        .all()
    )

    out = []
    for ev, em in rows:
        all_sources = (
            db.query(models.EventSource)
            .filter(models.EventSource.event_id == ev.id)
            .all()
        )

        # 可引用來源（官方／新聞／廠商聲明／未分類）——照抄標題 + 連結 + 層級
        citable = [s for s in all_sources if s.source_tier in _CITABLE_TIERS]
        citable.sort(key=lambda s: _TIER_ORDER.get(s.source_tier, 9))

        # 社群層：純連結列表。只給標題與連結，不計數、不歸納、不判讀。
        social = [s for s in all_sources if s.source_tier == "social"]

        out.append({
            "event_id": ev.id,
            "event_name": ev.name,           # 照抄，不改寫
            "kind": ev.kind or "incident",   # incident=具體事件；compilation=彙整報導（延伸閱讀，不帶同等份量）
            "start_date": ev.start_date,
            # role 為 None 時（尚未由人工判定）刻意不附標籤，寧可留白也不能猜測角色
            "role": em.role,
            "role_label": ROLE_LABELS.get(em.role),
            "sources": [
                {
                    "title": s.title,        # 照抄原文標題
                    "url": s.url,
                    "tier": s.source_tier,
                    "published_date": s.published_date,
                }
                for s in citable
            ],
            "social_links": [
                {
                    "title": s.title,        # 照抄，不改寫
                    "url": s.url,
                }
                for s in social
            ],
            "social_label": SOCIAL_LABEL if social else None,
        })
    return out


def get_events_payload(db, producer_id: int) -> dict:
    """供 Cloud 主流程直接嵌入回應的完整區塊。"""
    events = get_events_for_producer(db, producer_id)
    return {
        "events": events,
        "has_records": len(events) > 0,
        "disclaimer": SCOPE_DISCLAIMER,
        # severity 刻意不存在：只能由官方裁罰紀錄推導，而該紀錄對本系統廠商實測為零筆。
        # 社群層永不參與 health_score / risk_level 之計算。
    }
