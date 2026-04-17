import google.generativeai as genai
import os
import json
import base64
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

class VisionService:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("⚠️ 警告: 未偵測到 API_KEY")
            raise ValueError("伺服器未能讀取到 GEMINI_API_KEY 環境變數，請檢查啟動腳本與系統變數配置。")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-3-flash-preview')

    def analyze_food_package(self, image_paths):
        """讀取多張圖片並整合解析食品資訊"""
        image_parts = []
        
        for path in image_paths:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    img_data = f.read()
                image_parts.append({"mime_type": "image/jpeg", "data": img_data})
        
        if not image_parts:
            return None

        prompt = """
        分析這些食品包裝照片（可能包含多個視角，如正面、成分表、營養標示等），並整合回傳 JSON 格式的產品資訊。
        如果不同照片中有重複資訊，請以最清晰的為準。
        
        必須包含：
        - name: 產品名稱
        - brand: 品牌
        - manufacturer: 製造商或委託代工廠商名稱
        - calories, protein, fat, sugar, sodium: 每100g的營養標示 (數字)
        - ingredients_list: [成分1, 成分2, ...] 
        - processing_level: 加工分級 (1-4)
        
        請以繁體中文回答。JSON ONLY.
        """

        try:
            content = image_parts + [prompt]
            response = self.model.generate_content(content)
            text = response.text.replace('```json', '').replace('```', '').strip()
            import re
            json_match = re.search(r'(\{.*\})', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            return json.loads(text)
        except Exception as e:
            error_msg = f"❌ 視覺多圖分析失敗: {e}\n"
            print(error_msg, flush=True)
            import traceback
            tb = traceback.format_exc()
            with open("/tmp/vision_error.log", "a") as f:
                f.write(error_msg)
                f.write(tb)
                f.write("-" * 40 + "\n")
            return None
