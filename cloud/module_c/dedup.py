"""
Module C — 食安事件去重與合併(舊版,格式化 safety_alerts 表輸出用)

從 main.py 抽出,邏輯逐字未改動。純函式,不碰 cursor/db/全域狀態。

現況說明:本檔與新版 v1 管線(ingest.py→gate.py→...→query.py)是兩套獨立的
Module C 實作。main.py 目前呼叫此檔處理 `safety_alerts` 表的查詢結果,
v1 管線的 query.py 尚未接入正式路徑,見 [[計畫對照表]] 4.3。
"""
import re

# 已知食安事件關鍵字群組：每組代表「同一個事件」的各種說法（含繁簡與別名）。
# 兩筆事件若命中同一組關鍵字且年份相容，視為同一事件。
# 注意：異物類各自獨立（網帽 / 鼠頭 / 壁虎 / 刀片 不可混為一談）。
INCIDENT_KEYWORD_GROUPS = {
    "餿水油": ["餿水油", "地溝油", "地沟油", "劣質油", "劣质油", "劣質豬油", "全統香豬油", "强冠", "強冠"],
    "飼料油": ["飼料油", "飼料用油", "飼料級"],
    "銅葉綠素": ["銅葉綠素", "铜叶绿素", "大統", "大统", "富味鄉", "富味乡"],
    "塑化劑": ["塑化劑", "塑化剂", "起雲劑", "起云剂", "鄰苯二甲酸", "邻苯二甲酸"],
    "順丁烯二酸": ["順丁烯二酸", "顺丁烯二酸", "毒澱粉", "毒淀粉"],
    "戴奧辛": ["戴奧辛", "戴奥辛"],
    "單氯丙二醇": ["單氯丙二醇", "单氯丙二醇"],
    "環氧乙烷": ["環氧乙烷", "环氧乙烷", "環氧乙烯"],
    "三聚氰胺": ["三聚氰胺", "美耐皿"],
    "蘇丹紅": ["蘇丹紅", "苏丹红"],
    "塑膠網帽異物": ["網帽", "网帽", "塑膠網", "塑膠條", "塑胶条"],
    "鼠頭異物": ["鼠頭", "鼠头"],
    "壁虎異物": ["壁虎"],
    "刀片異物": ["刀片"],
    "手卷異物": ["手卷"],
    "蘋果麵包中毒": ["蘋果麵包", "苹果面包"],
    "真飽涼麵": ["真飽涼麵", "真饱凉面", "真飽", "真饱"],
}

def _incident_signature(evt: dict) -> set:
    text = (evt.get('title', '') or '') + (evt.get('summary', '') or '')
    return {gid for gid, terms in INCIDENT_KEYWORD_GROUPS.items() if any(t in text for t in terms)}

def _event_year(evt: dict):
    m = re.match(r'(\d{4})', str(evt.get('date', '') or ''))
    return m.group(1) if m else None

def is_similar_event(evt1: dict, evt2: dict) -> bool:
    def clean(text):
        if not text:
            return ""
        return re.sub(r'[^\w]', '', text)

    def char_bigrams(text):
        return set(text[i:i+2] for i in range(len(text) - 1))

    title1 = clean(evt1.get('title', ''))
    title2 = clean(evt2.get('title', ''))

    # 1. Exact or substring match in titles
    if title1 and title2:
        if title1 in title2 or title2 in title1:
            return True

    # 2. 事件關鍵字群組：命中同一組事件關鍵字，且年份相容（相同或其一未知）→ 同一事件
    sig1 = _incident_signature(evt1)
    sig2 = _incident_signature(evt2)
    if sig1 & sig2:
        y1, y2 = _event_year(evt1), _event_year(evt2)
        if y1 is None or y2 is None or y1 == y2:
            return True

    # 3. Character bigram Jaccard on titles (works for Chinese text without tokenization)
    if len(title1) >= 2 and len(title2) >= 2:
        bg1 = char_bigrams(title1)
        bg2 = char_bigrams(title2)
        union = bg1 | bg2
        if union:
            jaccard = len(bg1 & bg2) / len(union)
            if jaccard > 0.10:
                return True

    return False

def _title_score(title: str, source_type: str) -> int:
    """為事件標題評分，挑選最適合當「代表標題」的來源。
    優先序：官方 > 新聞 > 維基 > 其他；並懲罰 PDF、個人貼文等雜訊標題。"""
    if not title or not title.strip():
        return -100
    title = title.strip()
    score = {"official": 30, "news": 20, "wiki": 10, "consumer_complaint": 5}.get(source_type or "", 0)
    # 懲罰雜訊標題
    if title.startswith("[PDF]") or title.startswith("[pdf]"):
        score -= 20
    # 個人貼文如「張哲生 - 1960年...」「某某 | Facebook」
    if re.match(r'^[一-鿿]{2,4}\s*[-—|]', title):
        score -= 15
    if "facebook" in title.lower() or "instagram" in title.lower() or title.lower() == "instagram":
        score -= 15
    # 適中長度加分（太短資訊不足、太長多為內文截斷）
    if 10 <= len(title) <= 45:
        score += 5
    return score

def merge_events(events: list) -> list:
    merged_list = []
    for evt in events:
        found = False
        for m_evt in merged_list:
            if is_similar_event(evt, m_evt):
                # Merge evt into m_evt
                d1 = m_evt.get('date', '')
                d2 = evt.get('date', '')
                if d2 and (not d1 or d2 > d1):
                    m_evt['date'] = d2

                if evt.get('summary') and evt.get('summary') not in m_evt.get('summary', ''):
                    if len(evt.get('summary')) > len(m_evt.get('summary', '')):
                        m_evt['summary'] = evt.get('summary')

                cand_score = _title_score(evt.get('title', ''), evt.get('_raw_source_type') or '')
                if evt.get('title') and cand_score > m_evt.get('_title_score', -100):
                    m_evt['title'] = evt.get('title')
                    m_evt['_title_score'] = cand_score

                if evt.get('severity') is not None:
                    if m_evt.get('severity') is None or evt.get('severity') > m_evt.get('severity'):
                        m_evt['severity'] = evt.get('severity')

                url = evt.get('source_url', '')
                if url:
                    link_title = "來源連結"
                    url_lower = url.lower()
                    st = evt.get('_raw_source_type') or ''
                    if "threads.com" in url_lower:
                        link_title = "Threads 連結"
                    elif "wikipedia.org" in url_lower:
                        link_title = "維基百科"
                    elif st == "official":
                        link_title = "官方公告"
                    elif st == "news":
                        link_title = "新聞連結"
                    elif st == "social":
                        link_title = "社群討論"

                    if not any(lnk['url'] == url for lnk in m_evt['links']):
                        m_evt['links'].append({"title": link_title, "url": url})

                    if 'sources' not in m_evt or not m_evt['sources']:
                        m_evt['sources'] = {"official": None, "news": None, "social": None}
                    if st in ["official", "news", "social"]:
                        m_evt['sources'][st] = url

                found = True
                break

        if not found:
            links = []
            url = evt.get('source_url', '')
            st = evt.get('_raw_source_type') or ''
            if url:
                link_title = "來源連結"
                url_lower = url.lower()
                if "threads.com" in url_lower:
                    link_title = "Threads 連結"
                elif "wikipedia.org" in url_lower:
                    link_title = "維基百科"
                elif st == "official":
                    link_title = "官方公告"
                elif st == "news":
                    link_title = "新聞連結"
                elif st == "social":
                    link_title = "社群討論"
                links.append({"title": link_title, "url": url})

            new_evt = {
                "date": evt.get('date') or '',
                "type": evt.get('type') or '未知來源',
                "title": evt.get('title') or '',
                "summary": evt.get('summary') or '',
                "source_url": url,
                "severity": evt.get('severity'),
                "links": links,
                "sources": {
                    "official": url if st == 'official' else None,
                    "news": url if st == 'news' else None,
                    "social": url if st == 'social' else None
                },
                "_raw_source_type": st,
                "_title_score": _title_score(evt.get('title', ''), st)
            }
            merged_list.append(new_evt)

    for evt in merged_list:
        evt.pop('_raw_source_type', None)
        evt.pop('_title_score', None)

    return merged_list
