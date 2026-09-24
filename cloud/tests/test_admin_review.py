import requests

def test_admin_review_flow():
    # 1. login to get token
    login_res = requests.post("http://localhost:8000/api/admin/login", json={
        "username": "admin",
        "password": "admin123"
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. create a pending suggestion
    suggest_res = requests.post("http://localhost:8000/api/additives/ADD-0001/suggest", json={
        "field_name": "description",
        "new_value": "new description for testing admin approval",
        "suggested_by": "test user",
        "reason": "unit test"
    })
    assert suggest_res.status_code == 200
    sug_id = suggest_res.json()["id"]
    
    # 3. test list pending suggestions
    list_res = requests.get("http://localhost:8000/api/admin/suggestions?status=pending", headers=headers)
    assert list_res.status_code == 200
    pending_ids = [s["id"] for s in list_res.json()]
    assert sug_id in pending_ids
    
    # 4. test approve suggestion
    approve_res = requests.put(f"http://localhost:8000/api/admin/suggestions/{sug_id}/approve", headers=headers)
    assert approve_res.status_code == 200
    
    # 5. verify additives table updated
    get_res = requests.get("http://localhost:8000/api/additives/ADD-0001")
    assert get_res.status_code == 200
    assert get_res.json()["description"] == "new description for testing admin approval"

    # 6. create another pending suggestion for rejection test
    suggest_res2 = requests.post("http://localhost:8000/api/additives/ADD-0001/suggest", json={
        "field_name": "description",
        "new_value": "another new description that should be rejected",
        "suggested_by": "test user",
        "reason": "unit test rejection"
    })
    assert suggest_res2.status_code == 200
    sug_id2 = suggest_res2.json()["id"]

    # 7. test reject suggestion
    reject_res = requests.put(f"http://localhost:8000/api/admin/suggestions/{sug_id2}/reject", headers=headers)
    assert reject_res.status_code == 200

    # 8. verify additives table is unchanged
    get_res2 = requests.get("http://localhost:8000/api/additives/ADD-0001")
    assert get_res2.status_code == 200
    assert get_res2.json()["description"] == "new description for testing admin approval"
