import requests

def test_submit_suggestion():
    # 測試不存在的 record_id
    res = requests.post("http://localhost:8000/api/additives/ADD-NONEXIST/suggest", json={
        "field_name": "description",
        "new_value": "測試說明修改",
        "suggested_by": "專家A",
        "reason": "補充文獻"
    })
    assert res.status_code == 404
    
    # 測試存在的 record_id (ADD-0001)
    res = requests.post("http://localhost:8000/api/additives/ADD-0001/suggest", json={
        "field_name": "description",
        "new_value": "這是新的消費者描述測試內容",
        "suggested_by": "專家A",
        "reason": "補充文獻"
    })
    assert res.status_code == 200
    assert res.json()["status"] == "pending"
    assert res.json()["field_name"] == "description"

def test_submit_comment():
    res = requests.post("http://localhost:8000/api/additives/ADD-0001/comment", json={
        "author": "讀者B",
        "content": "我覺得這個添加物很常見"
    })
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    assert res.json()["author"] == "讀者B"
