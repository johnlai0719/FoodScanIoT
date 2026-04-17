import os
import json
import requests
import google.generativeai as genai
from database import SessionLocal, engine
import models
from sqlalchemy.orm import Session
from datetime import datetime

# 初始化 Gemini
api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-3-flash-preview') # 切換至傳統穩定型號

def update_producer_safety_events(producer_id: int, p_name: str):
    """檢索單一廠商的食安事件，並在更新後清除 Fog 端快取"""
    db = SessionLocal()
    print(f"🔍 [背景排程] 正在搜尋廠商: {p_name}")
    
    prompt = f"""
    你是一個專業的食安監測 AI。請檢索有關「{p_name}」在台灣近兩年發生的重大食安新聞、產品回收、或衛生局抽驗不合格事件。
    請以繁體中文回傳 JSON 格式列表，內容包含：
    - title: 事件標題
    - content: 簡短內容摘要 (100字內)
    - date: 事件日期 (YYYY-MM)
    - source_url: 新聞來源連結 (如有)
    
    如果沒有發現任何事件，請回傳空列表 []。
    JSON ONLY, NO MARKDOWN.
    """

    try:
        response = model.generate_content(prompt)
        text = response.text.replace('```json', '').replace('```', '').strip()
        events = json.loads(text)

        if events:
            print(f"✅ 發現 {p_name} 有 {len(events)} 筆新事件，正在同步...")
            new_inserts = 0
            for ev in events:
                # 避免重複匯入 (簡單以標題判斷)
                exists = db.query(models.SafetyAlert).filter(models.SafetyAlert.title == ev['title']).first()
                if not exists:
                    new_alert = models.SafetyAlert(
                        title=ev['title'],
                        content=ev['content'],
                        alert_date=ev['date'],
                        source_url=ev.get('source_url', ""),
                        producer_id=producer_id,
                        keyword_used=p_name
                    )
                    db.add(new_alert)
                    new_inserts += 1
            db.commit()
            
            # 若有真正新增資料，發送快取清除請求給 Fog
            if new_inserts > 0:
                print(f"🔔 [通知] 發現 {p_name} 新事件，準備清除受影響產品的 Fog 快取...")
                try:
                    # 找出受影響的所有產品
                    affected_products = db.query(models.Product).filter(models.Product.producer_id == producer_id).all()
                    cleared_count = 0
                    
                    if affected_products:
                        for prod in affected_products:
                            try:
                                resp = requests.delete(f"http://localhost:3001/cache/{prod.barcode}", timeout=3)
                                if resp.status_code == 200:
                                    cleared_count += 1
                            except Exception as e_req:
                                print(f"⚠️ 清除條碼 {prod.barcode} 快取失敗: {e_req}")
                    
                    print(f"🧹 已成功針對 {p_name} 旗下的 {cleared_count} 項產品清除 Fog 舊快取！")
                except Exception as ex:
                    print(f"⚠️ 清除 Fog 快取過程發生異常: {ex}")
        else:
            print(f"ℹ️ {p_name} 近期無重大食安事件報告。")

    except Exception as e:
        print(f"❌ 檢索 {p_name} 失敗: {e}")
    finally:
        db.close()


def update_safety_events():
    """批次更新所有廠商"""
    db = SessionLocal()
    producers = db.query(models.Producer).all()
    print(f"🚀 啟動 AI 監控模式：正在為 {len(producers)} 家廠商檢索食安事件...")
    db.close()

    for producer in producers:
        update_producer_safety_events(producer.id, producer.name)

    print("✨ 食安事件自動更新完成！")

if __name__ == "__main__":
    update_safety_events()
