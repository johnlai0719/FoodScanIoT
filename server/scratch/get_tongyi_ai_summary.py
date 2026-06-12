import requests
import sys
import os
import json

url = "http://localhost:3002/api/analyze"
payload = {
    "barcode": "4710088411164", # 統一肉燥麵
    "label_images": [],
    "user_conditions": {
        "group": "adult",
        "allergens": []
    }
}

try:
    print("Sending analyze request for 統一肉燥麵 to uvicorn server...")
    resp = requests.post(url, json=payload, timeout=60)
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("\n=== FULL RESPONSE ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"Failed: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
