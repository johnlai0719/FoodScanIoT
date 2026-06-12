import sys
import os
from dotenv import load_dotenv

# Add parent directory and load dotenv
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Check producers
    res_p = conn.execute(text("SELECT id, name, risk_level FROM producers"))
    producers = res_p.fetchall()
    print("=== Producers ===")
    for p in producers:
        print(f"ID: {p[0]} | Name: {p[1]} | Risk Level: {p[2]}")
        
    # Check safety alerts count
    res_s = conn.execute(text("SELECT COUNT(*) FROM safety_alerts"))
    count = res_s.fetchone()[0]
    print(f"\n=== Total Safety Alerts: {count} ===")
    
    if count > 0:
        res_latest = conn.execute(text("SELECT id, alert_date, title, producer_id FROM safety_alerts ORDER BY alert_date DESC LIMIT 5"))
        print("\n=== Latest 5 Safety Alerts ===")
        for a in res_latest.fetchall():
            print(f"ID: {a[0]} | Date: {a[1]} | Title: {a[2]} | Producer ID: {a[3]}")
