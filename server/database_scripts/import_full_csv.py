import pandas as pd
import mysql.connector
import json
import numpy as np

def run_import():
    db_config = {"host": "localhost", "user": "root", "password": "password", "database": "product_db", "port": 3306}
    df = pd.read_csv("/home/johnlai/projects/FoodScanIoT/Cloud_Server/database/MySQL_data/additives.csv")
    df = df.replace({np.nan: None})
    
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    for _, row in df.iterrows():
        if not row['name']: continue
        
        sql = """
        INSERT INTO additives (id, name, aliases, category, description, food_tech_purpose, adi, jecfa_summary, iarc_class, medical_caution, transparency_level, is_allergen, allergen_details, risks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description)
        """
        cursor.execute(sql, (
            int(row['id']), row['name'], row['aliases'] or "[]", row['category'], row['description'],
            row['food_tech_purpose'], row['adi'], row['jecfa_summary'], row['iarc_class'],
            row['medical_caution'], float(row['transparency_level'] or 0),
            int(row['is_allergen'] or 0), row['allergen_details'], row['risks'] or "{}"
        ))

    conn.commit()
    print(f"Successfully imported {len(df)} items.")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    run_import()
