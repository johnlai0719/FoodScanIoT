from pydantic import BaseModel
from typing import Optional, Any, List, Dict

class ProductBase(BaseModel):
    barcode: str
    name: str
    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    country_of_origin: Optional[str] = None
    calories: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbohydrates: Optional[float] = None
    sugar: Optional[float] = None
    sodium: Optional[float] = None
    
    # 新增的 JSON 支援
    other_nutrition: Optional[Dict[str, Any]] = None
    ingredients_list: Optional[List[str]] = None
    ingredients_raw: Optional[str] = None
    
    allergens: Optional[Any] = None
    is_estimated: bool = True
    ref_food_id: Optional[str] = None

class ProductCreate(ProductBase):
    pass

class ProductSchema(ProductBase):
    class Config:
        from_attributes = True
