import sys
import os
import json

# Add server directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from database import engine
from sqlalchemy import text

query = """
SELECT sa.id, sa.alert_date, sa.source_type, sa.title, sa.content, sa.source_url, sa.severity, p.name as producer_name
FROM safety_alerts sa
JOIN producers p ON sa.producer_id = p.id
WHERE p.name LIKE :name
ORDER BY sa.alert_date DESC
"""

with engine.connect() as conn:
    res = conn.execute(text(query), {"name": "%統一%"})
    rows = res.fetchall()
    
    print(f"Total alerts found for '統一': {len(rows)}")
    for idx, row in enumerate(rows):
        print(f"\n[{idx + 1}] Date: {row[1]} | Source: {row[2]} | Severity: {row[6]}")
        print(f"Title: {row[3]}")
        print(f"Content: {row[4]}")
        print(f"URL: {row[5]}")
