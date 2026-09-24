import os
import sys
import psycopg2
import bcrypt
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# 與 database.py 採同一套連線設定來源(DATABASE_URL 或拆開的 DB_* 變數),
# 避免兩處設定各自為政、其中一處失去同步。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import SQLALCHEMY_DATABASE_URL


def init_tables(db_url: str = None):
    conn = psycopg2.connect(db_url or SQLALCHEMY_DATABASE_URL.replace("postgresql+psycopg2", "postgresql"))
    cur = conn.cursor()
    
    # 建立 admin_users 表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(100) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(50) DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # 建立 additive_suggestions 表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS additive_suggestions (
        id SERIAL PRIMARY KEY,
        record_id VARCHAR(50) NOT NULL,
        field_name VARCHAR(100) NOT NULL,
        old_value TEXT,
        new_value TEXT NOT NULL,
        suggested_by VARCHAR(100),
        reason TEXT,
        status VARCHAR(50) DEFAULT 'pending',
        reviewed_by INT REFERENCES admin_users(id),
        reviewed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # 建立 additive_comments 表
    cur.execute("""
    CREATE TABLE IF NOT EXISTS additive_comments (
        id SERIAL PRIMARY KEY,
        record_id VARCHAR(50) NOT NULL,
        author VARCHAR(100) NOT NULL,
        content TEXT NOT NULL,
        status VARCHAR(50) DEFAULT 'approved',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # 插入預設管理員
    username = "admin"
    password = "admin123"
    pwd_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    cur.execute("""
    INSERT INTO admin_users (username, password_hash)
    VALUES (%s, %s)
    ON CONFLICT (username) DO NOTHING;
    """, (username, pwd_hash))

    conn.commit()
    conn.close()
    print(f"✅ 管理審核表已就緒。本機預設帳號(僅供開發使用,正式環境請自行更換密碼):"
          f" username={username} / password={password}")

if __name__ == "__main__":
    init_tables()
