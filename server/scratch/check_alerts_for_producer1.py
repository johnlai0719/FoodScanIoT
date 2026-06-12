import sys
import os
from dotenv import load_dotenv

# Add parent directory and load dotenv
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SELECT id, alert_date, title, source_type, content FROM safety_alerts WHERE producer_id = 1"))
    rows = res.fetchall()
    print(f"Total alerts for producer_id 1 (統一企業): {len(rows)}")
    for r in rows:
        print(f"ID: {r[0]} | Date: {r[1]} | Title: {r[2]} | Source: {r[3]}")
        print(f"Content: {r[4]}")
        print("-" * 40)
