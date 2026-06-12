"""
test_vision.py — 測試 Gemma 4 31B 食品標籤視覺辨識

功能：讀取 input/ 資料夾中的圖片，送給 Gemma 4 31B 辨識：
  1. 標籤上的所有成分與食品添加物
  2. 每項的英文學名

使用方式：
  python test_vision.py              # 處理 input/ 所有圖片
  python test_vision.py --image xxx.jpg  # 指定單張圖片
  python test_vision.py --list-models    # 列出可用模型確認 ID

安裝依賴：
  pip install google-genai python-dotenv pillow
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import os

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    print("請先安裝：pip install google-genai")
    sys.exit(1)

# ── 設定 ──────────────────────────────────────────────
MODEL_ID = "gemini-2.5-flash"   # Gemma 4 31B 不支援圖片輸入，改用 Gemini 視覺模型
INPUT_DIR = Path(__file__).parent / "input"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

PROMPT = """
請分析這張食品標籤圖片，找出所有的「食品添加物」與「成分」。

請以 JSON 格式回傳，格式如下：
{
  "product_name": "產品名稱（若看得到）",
  "ingredients": [
    {
      "original": "標籤上的原始文字",
      "name_en": "英文學名（若為食品添加物請給正式化學名，若為一般食材給英文名即可，不確定則給 null）",
      "is_additive": true 或 false
    }
  ],
  "notes": "其他備註（如標籤模糊、部分辨識不清等）"
}

規則：
- original 欄位完整保留標籤原文，不要修改
- name_en 若為添加物請給正式 JECFA / CODEX 學名，例如 "Sodium Benzoate" 而非 "preservative"
- 若標籤有 E 號或 INS 號也請記錄在 name_en 旁（例如 "Sodium Benzoate (INS 211)"）
- 只回傳 JSON，不要其他說明文字
"""
# ──────────────────────────────────────────────────────


def get_client():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("錯誤：請在 .env 設定 GEMINI_API_KEY")
        sys.exit(1)
    return genai.Client(api_key=api_key)


def list_models(client):
    print("可用模型清單：")
    for m in client.models.list():
        if "gemma" in m.name.lower() or "gemini" in m.name.lower():
            print(f"  {m.name}")


def image_to_part(image_path: Path):
    suffix = image_path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".heic": "image/heic", ".heif": "image/heif"
    }
    mime = mime_map.get(suffix, "image/jpeg")
    data = image_path.read_bytes()
    return genai_types.Part.from_bytes(data=data, mime_type=mime)


def analyze_image(client, image_path: Path):
    print(f"\n{'─'*50}")
    print(f"圖片：{image_path.name}")
    print(f"模型：{MODEL_ID}")

    image_part = image_to_part(image_path)

    t0 = time.time()
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=[image_part, PROMPT],
    )
    elapsed = time.time() - t0

    raw = response.text.strip()

    # 嘗試解析 JSON
    json_text = raw
    if "```json" in raw:
        json_text = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        json_text = raw.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(json_text)
        print(f"耗時：{elapsed:.1f} 秒")
        print(f"產品：{result.get('product_name', '未辨識')}")
        print(f"成分數：{len(result.get('ingredients', []))}")
        print()
        for item in result.get("ingredients", []):
            tag = "[添加物]" if item.get("is_additive") else "[食材]  "
            en = item.get("name_en") or "—"
            print(f"  {tag} {item['original']}  →  {en}")
        if result.get("notes"):
            print(f"\n備註：{result['notes']}")
        print()
        print("完整 JSON：")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(f"耗時：{elapsed:.1f} 秒")
        print("⚠ 無法解析 JSON，原始回應：")
        print(raw)


def main():
    parser = argparse.ArgumentParser(description="Gemma 視覺辨識測試")
    parser.add_argument("--image", help="指定單張圖片路徑")
    parser.add_argument("--list-models", action="store_true", help="列出可用模型")
    args = parser.parse_args()

    client = get_client()

    if args.list_models:
        list_models(client)
        return

    if args.image:
        images = [Path(args.image)]
    else:
        images = [p for p in INPUT_DIR.iterdir() if p.suffix.lower() in SUPPORTED_EXTS]
        if not images:
            print(f"input/ 資料夾沒有圖片（支援：{', '.join(SUPPORTED_EXTS)}）")
            print(f"請把食品標籤圖片放入：{INPUT_DIR}")
            return
        images.sort()

    print(f"找到 {len(images)} 張圖片，使用模型：{MODEL_ID}")
    for img in images:
        analyze_image(client, img)


if __name__ == "__main__":
    main()
