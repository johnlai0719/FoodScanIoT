"""Local readiness probes; never invoke paid recognition APIs."""
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

kind = sys.argv[1]
ports = {"cloud": 8000, "reader": 8181, "dashboard": 8787}
path = "/api/state" if kind == "dashboard" else "/health"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(f"http://127.0.0.1:{ports[kind]}{path}", timeout=3) as response:
        state = json.load(response)
except urllib.error.HTTPError as exc:
    if kind != "cloud" or exc.code != 503:
        raise
    state = json.load(exc)
    assert state.get("demo") is True
    import psycopg2
    with psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
                          dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                          password=os.environ["DB_PASSWORD"], connect_timeout=3) as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone() == (1,)
    state = {"database": "ok", "vision_backend": os.environ["VISION_BACKEND"]}
if kind == "cloud":
    # The deliberate 503 demo switch must not break container readiness.
    assert state.get("database") == "ok"
    assert state.get("vision_backend") == "google_vision_rules_gemini"
elif kind == "reader":
    assert state.get("routing_policy") == "vision_text_envelope_v1"
    assert state.get("ocr_quality_policy") == "vision_min_text_v1"
    assert state.get("gemini_configured")
    credentials = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    assert credentials.is_file()
    assert json.loads(credentials.read_text())["private_key"]
else:
    assert "server_time" in state
