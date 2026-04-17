import google.generativeai as genai
import os
from dotenv import load_dotenv
from database import SessionLocal

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
import models
import json
from sqlalchemy import or_
import re

class FoodAnalyzer:
    def __init__(self, db_session):
        self.db = db_session
        self.name_map = {
            "味精": "L-麩酸鈉",
            "味素": "L-麩酸鈉",
            "己二烯酸鉀": "己二烯酸鉀",
            "小蘇打": "碳酸氫鈉"
        }
        self.base_ingredients = ["麵粉", "水", "砂糖", "蔗糖", "食鹽", "棕櫚油", "豬油", "大豆油", "生乳", "雞蛋"]
        
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-3-flash-preview')
        else:
            self.model = None

    def find_best_match(self, item_name):
        if any(base in item_name for base in self.base_ingredients):
            return None
        search_name = self.name_map.get(item_name, item_name)
        exact = self.db.query(models.Additive).filter(models.Additive.name == search_name).first()
        if exact: return exact
        alias_match = self.db.query(models.Additive).filter(models.Additive.aliases.contains(item_name)).first()
        if alias_match: return alias_match
        return None

    def calculate_transparency(self, product, producer_info, safety_alerts):
        """[Module D] 計算資訊透明度得分"""
        score = 0
        checks = {
            "has_nutrition": bool(product.calories and product.sodium),
            "has_ingredients": bool(product.ingredients_list),
            "has_producer": bool(producer_info.get("name")),
            "has_safety_data": bool(safety_alerts) or producer_info.get("risk_level") != "Unknown"
        }
        score = sum(25 for val in checks.values() if val)
        return score

    def get_ai_diagnosis(self, product_fact, user_context, ingredients_data, safety_alerts):
        if not self.model: return {"grade": "N/A", "summary": "AI 模組未啟動"}
        
        # 核心優化：明確區分「資料庫已知資訊」與「AI 分析任務」
        prompt = f"""
        你是 FoodAware Pro 臨床營養專家系統。請執行「Module B & C」綜合風險評估。
        
        【權威資料庫背景 (Module B)】
        以下成分已由系統資料庫檢索到權威定義、用途與通用風險。
        【嚴格限制】: 嚴禁在 summary 或 warnings 中重述這些成分的定義或工業用途（例如不要說「苯甲酸鈉是防腐劑」）。
        成分資料詳情: {json.dumps(ingredients_data, ensure_ascii=False)}
        
        【產品與背景】
        名稱: {product_fact['name']} | 品牌: {product_fact['brand']} | 營養: {product_fact.get('nutrition')}
        廠商風險與警訊: {json.dumps(safety_alerts, ensure_ascii=False)}
        
        【使用者個人化條件】
        {json.dumps(user_context, ensure_ascii=False)}
        
        【任務要求】
        1. 評定 grade (A-F) 與 score (0-100)。
        2. summary (核心診斷): 請以極度嚴謹的毒理學觀點撰寫商品總介。明確指出短期食用無明顯危害，但長期過量攝取可能累積的慢性健康風險（例如：吃了沒事，但吃久了會有事）。並針對「該產品整體」對「該使用者條件」的動態影響進行深度分析。嚴禁單純回覆「可正常食用」。
        3. warnings (個人化警告): 若資料庫中的 `risks` 或 `medical_caution` 與使用者的 `health` 或 `allergies` 產生衝突，請列出具體警告。
        4. evidence_chain: 必須包含 "claim" (理由), "source" (依據來源), "url" (若有)。
        
        請以繁體中文回答。回傳格式必須為純 JSON。
        """
        try:
            response = self.model.generate_content(prompt)
            text = response.text
            json_match = re.search(r'(\{.*\})', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            return json.loads(text.strip())
        except Exception as e: 
            return {"grade": "C", "score": 50, "summary": "診斷引擎暫時降級運作中。", "evidence_chain": []}

    def save_ai_result_to_db(self, barcode, result):
        """[Module A] 將 AI 視覺解析出的資料儲存至資料庫"""
        # 檢查產品是否已存在
        product = self.db.query(models.Product).filter(models.Product.barcode == barcode).first()
        
        if not product:
            # 建立新產品
            product = models.Product(barcode=barcode)
            self.db.add(product)
        
        # 更新產品欄位
        product.name = result.get("name", product.name)
        product.brand = result.get("brand", product.brand)
        product.calories = result.get("calories", product.calories)
        product.protein = result.get("protein", product.protein)
        product.fat = result.get("fat", product.fat)
        product.carbohydrates = result.get("carbohydrates") or result.get("sugar", 0) # 簡化對應
        product.sugar = result.get("sugar", product.sugar)
        product.sodium = result.get("sodium", product.sodium)
        product.ingredients_list = result.get("ingredients_list", product.ingredients_list)
        product.processing_level = result.get("processing_level", product.processing_level)
        
        try:
            self.db.commit()
            # [Smart Feature] 自動對解析出的成分執行擴充偵測
            self.smart_additive_expansion(result.get("ingredients_list", []))
            return True
        except Exception as e:
            self.db.rollback()
            print(f"❌ 資料庫儲存失敗: {e}")
            return False

    def smart_additive_expansion(self, ingredients_list):
        """[Smart Feature] 偵測並自動擴充未知添加物到知識庫"""
        if not self.model or not ingredients_list: return
        
        unknown_items = []
        for item in ingredients_list:
            if not self.find_best_match(item):
                unknown_items.append(item)
        
        if not unknown_items: return
        
        print(f"🔍 發現 {len(unknown_items)} 個未知成分，啟動智能擴充: {unknown_items}")
        
        prompt = f"""
        以下是食品中偵測到的成分列表，請判斷哪些屬於「食品添加物」或「化學成分」。
        成分列表: {unknown_items}
        
        若是添加物，請提供以下資訊的 JSON 陣列：
        - name: 規格化名稱 (如: 己二烯酸鉀)
        - aliases: [別名1, 別名2]
        - category: 類別 (如: 防腐劑, 甜味劑)
        - description: 簡短定義
        - food_tech_purpose: 食品工業用途
        - risks: ["風險1", "風險2"]
        - iarc_class: IARC 致癌分級 (若無則 null)
        - is_allergen: boolean (是否為常見過敏原)
        
        若該成分為天然原始食材（如: 水, 雞蛋, 小麥, 糖），請忽略。
        只回傳 JSON 陣列。
        """
        
        try:
            response = self.model.generate_content(prompt)
            data = json.loads(re.search(r'(\[.*\])', response.text, re.DOTALL).group(1))
            
            for entry in data:
                # 再次確認是否已存在（避免併發衝突）
                existing = self.db.query(models.Additive).filter(models.Additive.name == entry['name']).first()
                if not existing:
                    new_ad = models.Additive(
                        name=entry['name'],
                        aliases=entry.get('aliases', []),
                        category=entry.get('category'),
                        description=entry.get('description'),
                        food_tech_purpose=entry.get('food_tech_purpose'),
                        risks=entry.get('risks', []),
                        iarc_class=entry.get('iarc_class'),
                        is_allergen=entry.get('is_allergen', False)
                    )
                    self.db.add(new_ad)
            self.db.commit()
            print(f"✅ 成功自動擴充 {len(data)} 筆添加物知識。")
        except Exception as e:
            print(f"⚠️ 智能擴充失敗: {e}")
            self.db.rollback()

    def generate_full_report(self, barcode, user_context=None):
        user_context = user_context or {"allergies": [], "health": [], "diet": []}
        product = self.db.query(models.Product).filter(models.Product.barcode == barcode).first()
        
        if not product:
            return {"error": "PRODUCT_NOT_FOUND", "barcode": barcode}

        producer = self.db.query(models.Producer).filter(models.Producer.id == product.producer_id).first()
        safety_alerts = []
        if product.producer_id:
            alerts = self.db.query(models.SafetyAlert).filter(models.SafetyAlert.producer_id == product.producer_id).all()
            safety_alerts = [{"title": a.title, "date": str(a.alert_date), "content": a.content} for a in alerts]

        ingredients_detail = []
        for item in (product.ingredients_list or []):
            ad = self.find_best_match(item)
            if ad:
                ingredients_detail.append({
                    "name": item, 
                    "isAdditive": True, 
                    "officialName": ad.name,
                    "purpose": ad.food_tech_purpose,
                    "iarcRating": ad.iarc_class, 
                    "adiValue": ad.adi, 
                    "caution": ad.medical_caution,
                    "description": ad.description,
                    "risks": ad.risks
                })
            else:
                ingredients_detail.append({"name": item, "isAdditive": False})

        producer_info = {
            "name": producer.name if producer else product.brand,
            "risk_level": producer.risk_level if producer else "Unknown"
        }
        
        transparency = self.calculate_transparency(product, producer_info, safety_alerts)
        
        product_fact = {
            "name": product.name, "brand": product.brand,
            "nutrition": {"sodium": product.sodium, "sugar": product.sugar, "calories": product.calories},
            "producer_info": producer_info
        }
        
        ai_diag = self.get_ai_diagnosis(product_fact, user_context, ingredients_detail, safety_alerts)

        return {
            "source": "FoodAware Engine v3.5 (Module B+C+D)",
            "product_info": {
                "barcode": barcode, "name": product.name, "brand": product.brand,
                "processingLevel": "Ultra-Processed" if product.processing_level >= 3 else "Processed"
            },
            "ingredients_detail": ingredients_detail,
            "certification_marks": product.certifications or [],
            "nutrition_facts": {
                "calories": product.calories, "protein": product.protein, "fat": product.fat,
                "carbohydrates": product.carbohydrates, "sugar": product.sugar, "sodium": product.sodium,
                "servingSize": f"{product.serving_size or 100}g"
            },
            "final_health_diagnosis": {
                "grade": ai_diag.get("grade", "C"),
                "score": ai_diag.get("score", 50),
                "transparencyScore": transparency,
                "summary": ai_diag.get("summary", ""),
                "warnings": ai_diag.get("warnings", []),
                "evidence_chain": ai_diag.get("evidence_chain", [])
            }
        }
