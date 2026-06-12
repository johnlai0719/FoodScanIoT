import requests
import json

url = "http://localhost:3003/query"
payload = {
    "barcode": "4710126198463",
    "user_conditions": {
        "group": "adult",
        "allergens": [],
        "chronic_conditions": ["hypertension", "diabetes"]
    }
}

try:
    response = requests.post(url, json=payload, timeout=5)
    data = response.json()
    print("Nutrition Facts in response:")
    print(json.dumps(data.get("data", {}).get("nutrition_facts", {}), indent=2, ensure_ascii=False))
    print("\nWarnings in final_health_diagnosis:")
    print(data.get("final_health_diagnosis", {}).get("warnings", []))
except Exception as e:
    print(f"Error: {e}")
