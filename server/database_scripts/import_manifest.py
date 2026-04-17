import json
import mysql.connector
import os

def run_import():
    db_config = {
        "host": "localhost",
        "user": "root",
        "password": "password",
        "database": "product_db",
        "port": 3306
    }
    
    # 嘗試連線
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # 讀取 JSON
    with open('additives_manifest.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"Read {len(data)} items from manifest.")

    for item in data:
        # 轉換資料結構
        additive_id = f"mft-{item['id']}"
        name_zh = item['name']
        synonyms = json.dumps(item.get('aliases', []), ensure_ascii=False)
        
        # 簡單的風險邏輯
        risk = 'low'
        if any(x in name_zh for x in ['磷', '苯甲酸', '亞硝酸', '己二烯酸']):
            risk = 'medium'
        
        sql = """
        INSERT INTO additives_knowledge (additive_id, name_zh, synonyms, risk_level)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE name_zh=VALUES(name_zh), synonyms=VALUES(synonyms)
        """
        cursor.execute(sql, (additive_id, name_zh, synonyms, risk))

    conn.commit()
    print("Import completed successfully.")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    run_import()
