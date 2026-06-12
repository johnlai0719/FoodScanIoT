import os
import psycopg2
import bcrypt
from dotenv import load_dotenv

load_dotenv()

def init_tables():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
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

if __name__ == "__main__":
    init_tables()
