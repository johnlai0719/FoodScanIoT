from sqlalchemy import Column, String, Integer, Float, JSON, Boolean, ForeignKey, Text
from database import Base

class TFDABaseNutrition(Base):
    __tablename__ = "tfda_base_nutrition"
    food_id = Column(String(50), primary_key=True, index=True)
    name = Column(String(255))
    category = Column(String(100))
    energy_100g = Column(Float)
    protein_100g = Column(Float)
    fat_100g = Column(Float)
    saturated_fat_100g = Column(Float, nullable=True)
    carbohydrates_100g = Column(Float)
    sugar_100g = Column(Float)
    fiber_100g = Column(Float, nullable=True)
    sodium_100g = Column(Float)

class Producer(Base):
    __tablename__ = "producers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True)
    license_number = Column(String(100), nullable=True) # 工廠登記編號
    risk_level = Column(String(20), default="Low") # Low, Medium, High
    safety_history = Column(JSON, nullable=True) # 歷年稽查紀錄
    last_audit_date = Column(String(50), nullable=True)

class Product(Base):
    __tablename__ = "products"
    barcode = Column(String(50), primary_key=True, index=True)
    name = Column(String(255))
    brand = Column(String(100))
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=True) # 關聯生產商
    manufacturer = Column(String(255), nullable=True)
    country_of_origin = Column(String(100), nullable=True)
    
    # 營養標示 (Per 100g/ml)
    calories = Column(Float) # Energy (kJ/kcal)
    protein = Column(Float) # 正面因素
    fat = Column(Float)
    saturated_fat = Column(Float, nullable=True) # 負面因素
    carbohydrates = Column(Float)
    sugar = Column(Float) # 負面因素
    fiber = Column(Float, nullable=True) # 正面因素
    sodium = Column(Float) # 負面因素 (mg)
    
    # 標章與其他
    serving_size = Column(Float, nullable=True)
    servings_per_container = Column(Float, nullable=True)
    other_nutrition = Column(JSON, nullable=True)
    ingredients_list = Column(JSON, nullable=True)
    ingredients_raw = Column(Text)
    allergens = Column(JSON)
    is_estimated = Column(Boolean, default=True)
    ref_food_id = Column(String(50), ForeignKey("tfda_base_nutrition.food_id"), nullable=True)
    processing_level = Column(Integer, nullable=True)
    processing_description = Column(Text, nullable=True)
    certifications = Column(JSON, nullable=True)

class Additive(Base):
    __tablename__ = "additives"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True)
    aliases = Column(JSON, nullable=True)
    category = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    
    food_tech_purpose = Column(Text, nullable=True)
    adi = Column(String(255), nullable=True)
    jecfa_summary = Column(Text, nullable=True)
    iarc_class = Column(String(50), nullable=True)
    medical_caution = Column(Text, nullable=True)
    transparency_level = Column(Integer, nullable=True)
    
    is_allergen = Column(Boolean, default=False)
    allergen_details = Column(JSON, nullable=True)
    
    risks = Column(JSON, nullable=True)

class SafetyAlert(Base):
    __tablename__ = "safety_alerts"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500))
    content = Column(String(2000))
    alert_date = Column(String(50))
    keyword_used = Column(String(50))
    source_url = Column(String(500), nullable=True)
    producer_id = Column(Integer, ForeignKey("producers.id"), nullable=True) # 關聯特定廠商
