import sys
sys.path.append("/home/johnlai/projects/server")
from database import SessionLocal
import models

db = SessionLocal()
try:
    producer = db.query(models.Producer).filter(models.Producer.id == 1).first()
    if producer:
        producer.last_audit_date = None
        db.commit()
        print("Reset last_audit_date of 統一企業 (ID 1) to None.")
finally:
    db.close()
