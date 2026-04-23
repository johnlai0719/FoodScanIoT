from fastapi import APIRouter, HTTPException
import os
import mysql.connector
import json as _json
from typing import List, Optional

router = APIRouter(prefix="/admin/api", tags=["admin"])

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "password"),
    "database": os.getenv("DB_NAME", "product_db"),
    "charset": "utf8mb4"
}

def get_db_conn():
    return mysql.connector.connect(**DB_CONFIG)

@router.get("/list")
async def list_products(limit: int = 100):
    db = get_db_conn()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT barcode, name, brand, manufacturer, created_at FROM products ORDER BY created_at DESC LIMIT %s", (limit,))
    products = cursor.fetchall()
    cursor.close()
    db.close()
    return {"status": "success", "data": products}

import glob

@router.delete("/delete/{barcode}")
async def delete_product(barcode: str):
    db = get_db_conn()
    cursor = db.cursor()
    
    # 1. 刪除資料庫紀錄
    cursor.execute("DELETE FROM products WHERE barcode = %s", (barcode,))
    db.commit()
    deleted_rows = cursor.rowcount
    
    # 2. 清理實體圖片 (uploads/scan_*_{barcode}_*.jpg)
    uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
    pattern = os.path.join(uploads_dir, f"*_{barcode}_*.jpg")
    files = glob.glob(pattern)
    deleted_files = []
    for f in files:
        try:
            os.remove(f)
            deleted_files.append(os.path.basename(f))
        except Exception as e:
            print(f"Error removing file {f}: {e}")
            
    cursor.close()
    db.close()
    
    return {
        "status": "success", 
        "deleted_rows": deleted_rows, 
        "deleted_files": deleted_files
    }

@router.post("/cleanup_tests")
async def cleanup_tests():
    db = get_db_conn()
    cursor = db.cursor(dictionary=True)
    
    # 找出所有包含 TEST 或 IMG_ 的條碼
    cursor.execute("SELECT barcode FROM products WHERE barcode LIKE '%TEST%' OR barcode LIKE 'IMG_%'")
    test_items = cursor.fetchall()
    
    results = []
    for item in test_items:
        barcode = item['barcode']
        # 呼叫刪除邏輯
        res = await delete_product(barcode)
        results.append({"barcode": barcode, "status": "deleted"})
        
    db.close()
    return {"status": "success", "cleaned_count": len(results)}

from pydantic import BaseModel

class UpdateProductRequest(BaseModel):
    name: str

@router.patch("/update/{barcode}")
async def update_product_name(barcode: str, req: UpdateProductRequest):
    db = get_db_conn()
    cursor = db.cursor()
    cursor.execute("UPDATE products SET name = %s WHERE barcode = %s", (req.name, barcode))
    db.commit()
    updated = cursor.rowcount
    cursor.close()
    db.close()
    return {"status": "success", "updated": updated}
