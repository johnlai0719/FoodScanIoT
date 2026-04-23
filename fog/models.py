from sqlalchemy import Column, String, JSON, DateTime
from database import Base
import datetime

class FogCache(Base):
    __tablename__ = "fog_cache"
    barcode = Column(String(50), primary_key=True, index=True)
    product_name = Column(String(255))
    report_data = Column(JSON)
    cached_at = Column(DateTime, default=datetime.datetime.utcnow)
