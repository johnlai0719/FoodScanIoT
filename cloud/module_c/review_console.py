"""
Module C — Stage 3 人工審核台（Streamlit）

使用者永遠看不到此介面；它只操作候選佇列，核准後才寫入已發布三張表。

功能：
- 事件管理：列出已發布事件、獨立新增空事件（可先建 bucket 再把來源指派進去）。
- 二次合併建議：LLM 建議哪些切塊碎片其實是同一事件，人工一鍵套用。
- 候選審核：每筆來源可「指派到任一事件 / 新事件 / 駁回 / 待定」——取代拖拉的彈性路由。

設計原則：LLM 分群與合併皆為建議；最終指派、命名、駁回一律人工決定。標題照抄，
歧義以紅字警示，社群層永不參與分數。

啟動：streamlit run module_c/review_console.py
"""
import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from database import SessionLocal
import models
from module_c.merge import suggest_merges, apply_merge
from module_c.grounding_discovery import discover_by_grounding

st.set_page_config(page_title="Module C 食安事件審核台", layout="wide")

TIER_BADGE = {"official": "🟢官方", "news": "🔵新聞", "self_published": "🟡廠商聲明",
              "unknown": "⚪未分類", "social": "🟣社群"}
KIND_LABEL = {"incident": "⚠️ 建議事件", "compilation": "📚 建議彙整", "not_an_event": "🚫 判為非事件",
              None: "❔ 無建議"}
# Grounding 管線各段落的處理結果標籤（見 grounding_discovery.py 的 details 欄位）。
GROUNDING_STATUS_LABEL = {
    "published": "✅ 已發布",
    "not_target": "⚪ 與本廠商無關",
    "ambiguous": "🟠 主體不清（未發布）",
    "no_sources": "⚪ 查無可追溯來源（未發布）",
    "extract_failed": "⚪ 萃取失敗",
    "duplicate": "🔁 與既有事件重複",
}
NOW = lambda: str(int(time.time()))

db = SessionLocal()
pmap = {p.id: p.name for p in db.query(models.Producer).all()}


# ---------------------------------------------------------------------------
# 側邊欄
# ---------------------------------------------------------------------------
st.sidebar.title("🔎 審核台")
st.sidebar.caption("使用者看不到此介面。候選核准後才發布。")
pending_total = db.query(models.EventCandidate).filter(models.EventCandidate.status == "pending").count()
st.sidebar.metric("待審核候選", pending_total)
st.sidebar.metric("已發布事件", db.query(models.Event).count())

# 篩選清單須涵蓋兩種來源：待審核候選（舊 Tavily 管線）與已發布事件的廠商
# （新 Grounding 管線直接寫入已發布表，不經過候選佇列，故只看候選會漏掉
#   Grounding 發布的廠商——2026-07-25 發現的問題）。
pending_producer_ids = {pid for (pid,) in db.query(models.EventCandidate.producer_id)
                        .filter(models.EventCandidate.status == "pending").distinct().all()}
published_producer_ids = {pid for (pid,) in db.query(models.EventManufacturer.producer_id).distinct().all()}
producer_ids = pending_producer_ids | published_producer_ids
opts = ["（全部）"] + [f"{pid} · {pmap.get(pid,'?')}" for pid in sorted(producer_ids)]
sel = st.sidebar.selectbox("篩選廠商", opts)
sel_pid = None if sel.startswith("（") else int(sel.split(" · ")[0])
st.sidebar.caption("🟢官方 🔵新聞 🟡廠商聲明 ⚪未分類 🟣社群")


def event_options():
    """回傳 {label: event_id}，供指派下拉。"""
    evs = db.query(models.Event).order_by(models.Event.id).all()
    return {f"[{e.id}] {'📚' if e.kind=='compilation' else '⚠️'} {e.name}": e.id for e in evs}


st.title("Module C — 食安事件人工審核")

# ---------------------------------------------------------------------------
# 0. Grounding 檢索（新管線：通過複核即直接發布，這裡讓你看到每次執行
#    「未發布的內容為什麼沒發布」，不是只看最終結果。）
# ---------------------------------------------------------------------------
with st.expander("🔍 執行 Grounding 檢索（新管線）", expanded=True):
    all_producers = db.query(models.Producer).order_by(models.Producer.id).all()
    gp = st.selectbox("選擇廠商", [f"{p.id} · {p.name}" for p in all_producers], key="grounding_producer")
    if st.button("執行檢索", key="run_grounding"):
        gpid = int(gp.split(" · ")[0])
        gname = gp.split(" · ", 1)[1]
        with st.spinner(f"正在查詢「{gname}」（呼叫 Gemini，需要幾秒到十幾秒）..."):
            st.session_state["grounding_result"] = discover_by_grounding(gpid, gname)

    gresult = st.session_state.get("grounding_result")
    if gresult:
        if gresult.get("skipped"):
            st.error(gresult["skipped"])
        elif gresult.get("events_found", 0) == 0:
            st.info(f"「{gresult['producer']}」查無相關事件。")
        else:
            st.write(f"**{gresult['producer']}** — 這次查到 {gresult['events_found']} 段內容，"
                     f"發布 {gresult['published']} 筆新事件")
            for d in gresult.get("details", []):
                label = GROUNDING_STATUS_LABEL.get(d["status"], d["status"])
                if d["status"] == "published":
                    st.success(f"{label}　[{d['event_id']}] {d['event_name']}"
                              f"（{d.get('start_date') or '日期未知'}，{d['source_count']} 筆來源）")
                else:
                    st.caption(f"{label}　{d['event_name']}　— {d.get('reason', '')}")
            st.caption("下方「已發布事件」清單需重新整理頁面才會顯示最新結果。")

# ---------------------------------------------------------------------------
# A. 事件管理
# ---------------------------------------------------------------------------
with st.expander(f"📋 已發布事件（可補充/移除來源）& ➕ 新增事件", expanded=True):
    evs_q = db.query(models.Event).order_by(models.Event.id)
    if sel_pid:
        evs_q = evs_q.join(models.EventManufacturer,
                           models.EventManufacturer.event_id == models.Event.id
                           ).filter(models.EventManufacturer.producer_id == sel_pid)
    evs = evs_q.all()
    if sel_pid and not evs:
        st.caption(f"「{pmap.get(sel_pid)}」目前沒有已發布事件。")
    for e in evs:
        links = db.query(models.EventManufacturer).filter(models.EventManufacturer.event_id == e.id).all()
        names = "、".join(pmap.get(l.producer_id, "?") for l in links)
        srcs = db.query(models.EventSource).filter(models.EventSource.event_id == e.id).all()
        kind_ic = "📚彙整" if e.kind == "compilation" else "⚠️事件"
        st.markdown(f"**[{e.id}] {kind_ic} {e.name}** ｜{e.start_date or '未知'}｜廠商：{names}｜{len(srcs)}筆來源")
        # 現有來源（可逐筆移除）+ 手動補充來源
        for s in srcs:
            sc1, sc2 = st.columns([0.9, 0.1])
            sc1.markdown(f"　{TIER_BADGE.get(s.source_tier, s.source_tier)} [{s.title}]({s.url})")
            if sc2.button("移除", key=f"rmsrc_{s.id}"):
                db.delete(s); db.commit(); st.rerun()
        with st.form(f"addsrc_{e.id}"):
            st.caption("➕ 補充來源到此事件")
            a1, a2, a3 = st.columns([3, 3, 1.2])
            at = a1.text_input("標題", key=f"at_{e.id}", label_visibility="collapsed", placeholder="來源標題")
            au = a2.text_input("網址", key=f"au_{e.id}", label_visibility="collapsed", placeholder="https://...")
            atier = a3.selectbox("層級", ["official", "news", "self_published", "unknown", "social"],
                                 key=f"atier_{e.id}", label_visibility="collapsed")
            if st.form_submit_button("加入此來源"):
                if not at.strip() or not au.strip():
                    st.error("標題與網址皆必填")
                else:
                    db.add(models.EventSource(event_id=e.id, title=at.strip()[:500],
                                              url=au.strip()[:1000], source_tier=atier))
                    db.commit(); st.success("已補充來源"); st.rerun()
        st.markdown("---")
    st.divider()
    with st.form("new_empty_event"):
        st.markdown("**➕ 獨立新增空事件**（先建，再把來源指派進去）")
        c1, c2, c3, c4 = st.columns([3, 1.3, 1.3, 1])
        en = c1.text_input("事件名稱", key="ne_name")
        ed = c2.text_input("起始日期", key="ne_date", placeholder="YYYY-MM")
        ek = c3.selectbox("類型", ["incident", "compilation"], key="ne_kind")
        ep = c4.selectbox("連結廠商", [f"{pid}·{pmap[pid]}" for pid in sorted(pmap)] , key="ne_prod")
        if st.form_submit_button("建立空事件"):
            if not en.strip():
                st.error("請填名稱")
            else:
                ev = models.Event(name=en.strip(), start_date=ed.strip() or None, kind=ek, created_at=NOW())
                db.add(ev); db.flush()
                pid = int(ep.split("·")[0])
                db.add(models.EventManufacturer(event_id=ev.id, producer_id=pid))
                db.commit()
                st.success(f"已建立空事件 [{ev.id}] {en}"); st.rerun()


# ---------------------------------------------------------------------------
# B. 二次合併建議（需選定單一廠商）
# ---------------------------------------------------------------------------
if sel_pid:
    with st.expander(f"🔄 二次合併建議（{pmap.get(sel_pid)}）— 收攏被切塊拆散的同事件", expanded=False):
        if st.button("產生合併建議", key="genmerge"):
            st.session_state["merge_sug"] = suggest_merges(sel_pid)
        sug = st.session_state.get("merge_sug")
        if sug:
            if sug.get("error"):
                st.error(sug["error"])
            elif not sug.get("merges"):
                st.info("LLM 認為各群已獨立，無需合併。")
            else:
                st.caption("⚠️ 以下為 LLM 建議，請逐一確認再套用（注意不同年份的事件不應合併）。")
                for i, m in enumerate(sug["merges"]):
                    with st.form(f"merge_{i}"):
                        st.markdown(f"**合併 {len(m['cluster_keys'])} 群 → 「{m['merged_name']}」**（{m['total_sources']}筆）")
                        st.caption(f"理由：{m['reason']}")
                        new_name = st.text_input("合併後名稱（可改）", value=m["merged_name"], key=f"mn_{i}")
                        if st.form_submit_button("✅ 套用此合併"):
                            apply_merge(sel_pid, m["cluster_keys"], new_name.strip() or m["merged_name"])
                            st.session_state.pop("merge_sug", None)
                            st.success("已合併"); st.rerun()


# ---------------------------------------------------------------------------
# C. 候選審核：每筆可指派到任一事件 / 新事件 / 駁回
# ---------------------------------------------------------------------------
st.header("待審核候選")
q = db.query(models.EventCandidate).filter(models.EventCandidate.status == "pending")
if sel_pid:
    q = q.filter(models.EventCandidate.producer_id == sel_pid)
cands = q.all()
if not cands:
    st.success("此篩選下無待審核候選。")
    db.close(); st.stop()

ev_opts = event_options()

# 分組顯示：(producer, kind, cluster_id)
groups = defaultdict(list)
for c in cands:
    groups[(c.producer_id, c.llm_kind, c.llm_cluster_id or "-")].append(c)


def order(item):
    (pid, kind, cid), _ = item
    return (pid, {"incident": 0, "compilation": 1, "not_an_event": 2, None: 3}.get(kind, 3), str(cid))


for (pid, kind, cid), cs in sorted(groups.items(), key=order):
    pname = pmap.get(pid, "?")
    sug_name = cs[0].llm_suggested_name or ""
    with st.container(border=True):
        head = f"{KIND_LABEL.get(kind)}　·　{pname}（{len(cs)}篇）"
        if sug_name:
            head += f"　→　{sug_name}"
        st.subheader(head)
        if cs[0].llm_cluster_reason:
            with st.expander("🤖 LLM 理由", expanded=False):
                st.caption(cs[0].llm_cluster_reason)

        # 每筆一個指派下拉
        default = ("🗑️ 駁回" if kind == "not_an_event"
                   else (f"➕ 新事件：{sug_name}" if sug_name else "⏸️ 待定"))
        assign_opts = ["⏸️ 待定", "🗑️ 駁回"]
        if kind in ("incident", "compilation") and sug_name:
            assign_opts.insert(0, f"➕ 新事件：{sug_name}")
        assign_opts += list(ev_opts.keys())

        with st.form(f"card_{pid}_{kind}_{cid}"):
            for c in cs:
                col1, col2 = st.columns([0.62, 0.38])
                with col1:
                    st.markdown(f"{TIER_BADGE.get(c.source_tier, c.source_tier)} **[{c.id}]** [{c.title}]({c.url})")
                    if c.is_ambiguous:
                        st.markdown(f"<span style='color:#c23b3b'>⚠️ 歧義：同時提及 {'、'.join(c.ambiguous_with or [])}"
                                    f"，請確認主體是否為 {pname}</span>", unsafe_allow_html=True)
                with col2:
                    idx = assign_opts.index(default) if default in assign_opts else 0
                    st.selectbox("指派到", assign_opts, index=idx, key=f"as_{c.id}", label_visibility="collapsed")
            if st.form_submit_button("✅ 套用此卡指派"):
                # 收集每筆的目標
                new_event_buckets = defaultdict(list)   # name -> [cand]
                to_existing = defaultdict(list)          # eid -> [cand]
                to_reject = []
                for c in cs:
                    choice = st.session_state.get(f"as_{c.id}", "⏸️ 待定")
                    if choice == "⏸️ 待定":
                        continue
                    if choice == "🗑️ 駁回":
                        to_reject.append(c)
                    elif choice.startswith("➕ 新事件："):
                        new_event_buckets[choice.replace("➕ 新事件：", "")].append(c)
                    elif choice in ev_opts:
                        to_existing[ev_opts[choice]].append(c)

                # 建立新事件
                for name, bucket in new_event_buckets.items():
                    ev = models.Event(name=name, kind=(kind if kind in ("incident", "compilation") else "incident"),
                                      created_at=NOW())
                    db.add(ev); db.flush()
                    db.add(models.EventManufacturer(event_id=ev.id, producer_id=pid))
                    for c in bucket:
                        db.add(models.EventSource(event_id=ev.id, title=c.title, url=c.url,
                                                  source_tier=c.source_tier, published_date=c.published_date))
                        c.status = "approved"; c.event_id = ev.id; c.reviewed_at = NOW()
                # 掛既有事件（含跨廠商連結）
                for eid, bucket in to_existing.items():
                    if not db.query(models.EventManufacturer).filter(
                            models.EventManufacturer.event_id == eid,
                            models.EventManufacturer.producer_id == pid).first():
                        db.add(models.EventManufacturer(event_id=eid, producer_id=pid))
                    for c in bucket:
                        db.add(models.EventSource(event_id=eid, title=c.title, url=c.url,
                                                  source_tier=c.source_tier, published_date=c.published_date))
                        c.status = "approved"; c.event_id = eid; c.reviewed_at = NOW()
                for c in to_reject:
                    c.status = "rejected"; c.reviewed_at = NOW()
                db.commit()
                st.success(f"已處理：新事件{len(new_event_buckets)}、掛既有{sum(len(v) for v in to_existing.values())}筆、駁回{len(to_reject)}筆")
                st.rerun()

db.close()
