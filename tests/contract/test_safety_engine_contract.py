"""食安事件引擎（Module C）的對外契約 —— 實作完成前先立好。

為什麼契約先行：
本引擎目前停用（server/main.py 的 SAFETY_EVENTS_ENABLED = False），但 module_c/query.py
的讀取迴圈**已經寫好**，只是沒有呼叫端。若等實作接上再定介面，屆時要同時改 Cloud
組裝、Fog 正規化、App 型別與 UI——那正是先前個人化遷移時踩過的坑。先把對外形狀釘住，
實作完成時 merge 只需「填實作」，不必順手改一堆介面。

本檔釘住三件事：
  1. get_events_payload() 的輸出形狀（用 SQLite 記憶體庫跑真實查詢，非假資料）
  2. 三態語義：未啟用 / 已查詢無紀錄 / 有紀錄 —— 這是目前 App 端無法區分的關鍵缺口
  3. 呈現原則的不可退讓處（不生成句子、社群不參與評分、role 未定不猜測）

實作端要滿足的條件見檔末 REQUIREMENTS。
"""
import pytest

pytest.importorskip("sqlalchemy", reason="需要 SQLAlchemy 才能載入 ORM 模型")
pytest.importorskip("psycopg2", reason="server/database.py 在 import 時即建立 engine，需要驅動")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import models  # noqa: E402
from module_c.query import (  # noqa: E402
    ROLE_LABELS,
    SCOPE_DISCLAIMER,
    SOCIAL_LABEL,
    get_events_payload,
)


@pytest.fixture
def db():
    """真實的 ORM 查詢，跑在記憶體 SQLite 上。

    事件三張表只用可攜型別（Integer/String/ForeignKey），故可脫離 PostgreSQL 測試。
    這比用假的 db stub 好——stub 只會驗證我對 SQLAlchemy 的想像。
    """
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine, tables=[
        models.Event.__table__,
        models.EventSource.__table__,
        models.EventManufacturer.__table__,
    ])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db, *, producer_id=1, role=None, tiers=("official", "news", "social")):
    ev = models.Event(name="2026 某某油品事件", start_date="2026-03", kind="incident")
    db.add(ev)
    db.flush()
    for i, tier in enumerate(tiers):
        db.add(models.EventSource(
            event_id=ev.id, title=f"原文標題 {tier}", url=f"https://example.com/{tier}",
            source_tier=tier, published_date="2026-03-01",
        ))
    db.add(models.EventManufacturer(event_id=ev.id, producer_id=producer_id, role=role))
    db.commit()
    return ev


# ─── 1. payload 形狀 ──────────────────────────────────────────────────────────

def test_payload_top_level_keys_are_frozen(db):
    """App 與 Cloud 組裝端都依賴這三個鍵。"""
    payload = get_events_payload(db, producer_id=1)
    assert set(payload) == {"events", "has_records", "disclaimer"}


def test_severity_is_deliberately_absent(db):
    """severity 刻意不存在——只能由官方裁罰紀錄推導，而該紀錄實測為零筆。

    若日後有人加回這個欄位，代表評分依據改變，必須重新檢視 health_score 的計算。
    """
    _seed(db)
    payload = get_events_payload(db, producer_id=1)
    assert "severity" not in payload
    for ev in payload["events"]:
        assert "severity" not in ev


def test_event_item_shape(db):
    _seed(db)
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert set(ev) == {
        "event_id", "event_name", "kind", "start_date",
        "role", "role_label", "sources", "social_links", "social_label",
    }
    # 事件本身沒有 summary 欄位——呈現原則是照抄原文標題，不生成句子
    assert "summary" not in ev


def test_citable_sources_carry_tier_and_link(db):
    _seed(db)
    ev = get_events_payload(db, producer_id=1)["events"][0]
    for s in ev["sources"]:
        assert set(s) == {"title", "url", "tier", "published_date"}
        assert s["url"].startswith("http")


def test_official_sources_are_ordered_first(db):
    """官方來源必須排在新聞之前——呈現順序本身是信任分層的一部分。"""
    _seed(db, tiers=("news", "official"))
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert [s["tier"] for s in ev["sources"]] == ["official", "news"]


# ─── 2. 社群層的特殊待遇 ──────────────────────────────────────────────────────

def test_social_is_separated_and_never_mixed_into_sources(db):
    """社群內容未經證實，混進 sources 等於系統代為背書。"""
    _seed(db, tiers=("official", "social"))
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert [s["tier"] for s in ev["sources"]] == ["official"]
    assert len(ev["social_links"]) == 1
    assert ev["social_label"] == SOCIAL_LABEL


def test_social_links_carry_no_interpretation(db):
    """只給標題與連結。不計數、不歸納主題、不生成句子——任何聚合都等於代為判讀。"""
    _seed(db, tiers=("social", "social"))
    ev = get_events_payload(db, producer_id=1)["events"][0]
    for link in ev["social_links"]:
        assert set(link) == {"title", "url"}


def test_social_label_absent_when_no_social_sources(db):
    _seed(db, tiers=("official",))
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert ev["social_links"] == []
    assert ev["social_label"] is None


# ─── 3. role：未判定時不得猜測 ────────────────────────────────────────────────

def test_role_unset_yields_no_label(db):
    """role 由人工於審核台判定。未判定時寧可留白，也不能預設為任一角色——
    把主動配合下架的下游業者標成違規者，是會造成實質傷害的錯誤。"""
    _seed(db, role=None)
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert ev["role"] is None
    assert ev["role_label"] is None


@pytest.mark.parametrize("role", list(ROLE_LABELS))
def test_known_roles_map_to_labels(db, role):
    _seed(db, role=role)
    ev = get_events_payload(db, producer_id=1)["events"][0]
    assert ev["role_label"] == ROLE_LABELS[role]


# ─── 4. 三態語義（目前 App 端無法區分的關鍵缺口）─────────────────────────────

def test_has_records_false_when_producer_has_none(db):
    """「已查詢，無公開紀錄」——這是有價值的正面資訊，必須與「未查詢」區分。

    目前 App 收到空陣列時無法判斷是哪一種，因而一律隱藏該區塊
    （見 APP/src/screens/HomeScreen.tsx 的 hasSafetyEvents）。
    引擎上線後，Cloud 必須把 has_records 傳到 App，才能顯示「查證後無不良紀錄」。
    """
    payload = get_events_payload(db, producer_id=999)
    assert payload["events"] == []
    assert payload["has_records"] is False


def test_has_records_true_when_events_exist(db):
    _seed(db)
    assert get_events_payload(db, producer_id=1)["has_records"] is True


def test_disclaimer_always_present(db):
    """範圍聲明不可省略——這是本模組唯一的立場宣告：
    揭露「食品是否曾出問題」之事實，非「誰該負責」之判斷。"""
    for pid in (1, 999):
        assert get_events_payload(db, producer_id=pid)["disclaimer"] == SCOPE_DISCLAIMER
    assert "非對責任歸屬之判斷" in SCOPE_DISCLAIMER


def test_query_never_exposes_candidates():
    """讀取迴圈只查已發布的三張表，永不觸及 EventCandidate 候選佇列——
    使用者不應看到「處理中」的事件，也不該從回應推測審核進度。

    以 AST 檢查實際的屬性存取，而非字串比對：query.py 的 docstring 本來就會提到
    EventCandidate（說明它「不」碰候選佇列），純字串搜尋會誤判。
    """
    import ast
    import inspect

    import module_c.query as q

    tree = ast.parse(inspect.getsource(q))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert "EventCandidate" not in referenced, (
        "讀取迴圈觸及了候選佇列——使用者會看到未經審核的事件"
    )
    # 反向確認測試有效：已發布的三張表確實有被引用
    assert {"Event", "EventSource", "EventManufacturer"} <= referenced


# ─── 實作端待滿足的條件 ──────────────────────────────────────────────────────
#
# 本檔已釘住 module_c/query.py 的輸出。實作接上時，還需要下列變更，屆時
# 對應的契約測試（tests/contract/test_cloud_response_contract.py）也要同步更新：
#
# REQUIREMENTS
#   R1. server/main.py 將 SAFETY_EVENTS_ENABLED 改為 True，並把讀取來源從
#       safety_monitor 的 safety_alerts 表換成 module_c.query.get_events_payload()。
#   R2. Cloud 回應新增欄位以區分三態。現行 food_safety_events 為扁平陣列，
#       無法表達「未啟用」與「已查詢無紀錄」的差別。建議：
#           food_safety_events: {events, has_records, disclaimer}
#       或於頂層另加 safety_events_available: bool。
#       任一選擇都會改變凍結的欄位集合 → test_top_level_keys_are_frozen 會轉紅，
#       那是預期行為，更新該清單即可。
#   R3. APP/src/types.ts 的 FoodSafetyEvent 需改為新形狀（無 summary，
#       sources 帶 tier，social_links 獨立，新增 role/role_label/kind）。
#   R4. APP/src/screens/HomeScreen.tsx 的 hasSafetyEvents 改用 has_records，
#       並為「已查詢無紀錄」設計正面呈現（目前是整區隱藏）。
#   R5. DropdownEvent.tsx 需支援 kind（incident 帶警示語氣、compilation 僅延伸閱讀）
#       與 role_label，且社群連結區塊需與可引用來源明確分離。
#   R6. 社群層永不參與 health_score / risk_level 計算——此為既有設計，
#       實作時務必確認沒有回歸。
