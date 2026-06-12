import requests

def test_admin_login():
    # Test wrong password, expected 401
    res = requests.post("http://localhost:8000/api/admin/login", json={
        "username": "admin",
        "password": "wrongpassword"
    })
    assert res.status_code == 401
    
    # Test correct password, expected 200 and access_token in json
    res = requests.post("http://localhost:8000/api/admin/login", json={
        "username": "admin",
        "password": "admin123"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()
