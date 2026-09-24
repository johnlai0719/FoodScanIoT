import psycopg2
import os

def test_tables_exist():
    # 請讀取環境變數 DATABASE_URL 連線
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    
    # 檢查資料表是否成功建立
    tables = ['admin_users', 'additive_suggestions', 'additive_comments']
    for table in tables:
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);", (table,))
        assert cur.fetchone()[0] == True
    conn.close()
