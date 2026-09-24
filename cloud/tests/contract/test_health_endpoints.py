"""健康檢查端點的行為契約。

這些端點不只是「有回應就好」——部署腳本用它們判斷部署成敗，所以它們的語義必須穩定：

  status = "ok"        這一層現在能服務請求
  status = "degraded"  這一層自身有問題（例如讀不到資料庫）

**最重要的一條**：Cloud 不可用時，Fog 必須仍回報 ok。Fog 的存在理由之一就是
Cloud 斷線時以快取繼續服務；若把下游狀態算進自身健康度，會造成兩個後果：
  1. Cloud 離線時無法部署 Fog（部署腳本的健康檢查會失敗）
  2. 與「離線降級」的設計主張自相矛盾
下游狀態只作為資訊回報，放在 downstream 欄位。

註：Node 層（:3001）的 /health 是 TypeScript，不在此檔涵蓋範圍；其設計原則相同，
見 fog/server.ts 的註解。
"""
import os
import sqlite3
import sys

import pytest

fastapi = pytest.importorskip(
    "fastapi",
    reason="本機未安裝 FastAPI 時略過；CI 的 python job 亦不安裝，"
           "這些端點的行為改由部署後的冒煙測試把關",
)
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def fog_app(tmp_path, monkeypatch):
    """載入 Fog Python 層，並指向一個乾淨的暫存快取 DB。

    刻意不使用專案內的 fog_cache.db——測試不該碰到任何真實快取。
    """
    db = tmp_path / "fog_cache.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cache (barcode TEXT PRIMARY KEY, result_json TEXT, "
        "last_updated INTEGER, ttl INTEGER)"
    )
    conn.execute("INSERT INTO cache VALUES ('4710018123456','{}',0,3600)")
    conn.commit()
    conn.close()

    # 指向一個保證連不上的位址，模擬 Cloud 離線
    monkeypatch.setenv("CLOUD_API_URL", "http://127.0.0.1:59999/api/analyze")
    # 以明確路徑載入，避免與 server/main.py 名稱衝突（見 conftest.py 說明）
    from conftest import load_fog_main
    fog_main = load_fog_main()

    monkeypatch.setattr(fog_main, "DB_PATH", str(db))
    monkeypatch.setattr(fog_main, "CLOUD_URL", "http://127.0.0.1:59999/api/analyze")
    return fog_main


def test_health_reports_ok_when_cache_is_readable(fog_app):
    body = TestClient(fog_app.app).get("/health").json()
    assert body["status"] == "ok"
    assert body["layer"] == "fog-python"
    assert body["cache_db"] == "ok"
    assert body["cache_entries"] == 1


def test_commit_is_reported(fog_app):
    """部署後要能當場確認 Pi 上跑的是哪一版——「部署成功」與「部署了正確版本」
    是兩件事，只看部署腳本沒報錯無法區分。"""
    body = TestClient(fog_app.app).get("/health").json()
    assert "commit" in body and body["commit"]


def test_cloud_outage_does_not_degrade_fog(fog_app):
    """本檔最重要的一條，理由見檔頭。"""
    body = TestClient(fog_app.app).get("/health?deep=1").json()

    assert body["downstream"]["cloud"].startswith("unreachable"), (
        "測試前提不成立：預期 Cloud 應連不上"
    )
    assert body["status"] == "ok", (
        "Cloud 不可用讓 Fog 的 status 變成非 ok。這會使 Cloud 離線時無法部署 Fog，"
        "並與離線降級的設計主張矛盾。下游狀態應只放在 downstream 欄位。"
    )


def test_shallow_check_makes_no_downstream_call(fog_app):
    """部署腳本打的是不帶參數的 /health，必須夠快、不可因下游逾時而卡住。"""
    body = TestClient(fog_app.app).get("/health").json()
    assert "downstream" not in body


def test_cache_db_failure_degrades_status(fog_app, monkeypatch):
    """反向確認：自身資源壞掉時 status 必須降級，否則健康檢查形同虛設。"""
    monkeypatch.setattr(fog_app, "DB_PATH", "/nonexistent/path/fog_cache.db")
    body = TestClient(fog_app.app).get("/health").json()
    assert body["status"] == "degraded"
    assert body["cache_db"].startswith("error")
