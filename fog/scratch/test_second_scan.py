import requests
import json

url = "http://localhost:3003/query"
payload = {
    "barcode": "4713696415398",
    "user_conditions": {
        "group": "adult",
        "allergens": []
    }
}

print("Simulating second scan of 4713696415398...")
try:
    response = requests.post(url, json=payload, timeout=5)
    print(f"Status Code: {response.status_code}")
    print(f"Cache Header: {response.headers.get('X-Cache')}")
    data = response.json()
    
    # Check food_safety_events
    events = data.get("food_safety_events", [])
    print(f"\nNumber of food safety events returned: {len(events)}")
    for idx, ev in enumerate(events[:3]):
        print(f"[{idx}] Date: {ev.get('date')} | Type: {ev.get('type')} | Title: {ev.get('title')}")
        print(f"    Summary: {ev.get('summary')}\n")
        
except Exception as e:
    print(f"Error: {e}")
