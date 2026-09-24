import os
import time
from database import SessionLocal, engine
import models
from database_scripts.init_review_tables import init_tables as init_review_tables

SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_data", "reference_seed.sql")


def _load_reference_seed(db):
    """
    匯入參考資料種子檔(additives / producers 等安全公開資料;
    不含 admin_users 或任何即時營運資料)。

    種子檔由 pg_dump --data-only --inserts 產生，含 psql 專用的
    \\restrict/\\unrestrict 中繼指令(非合法 SQL)，執行前先過濾掉。
    """
    if not os.path.exists(SEED_FILE):
        print(f"⚠️  找不到種子檔: {SEED_FILE}，略過參考資料匯入。")
        return

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        sql = "\n".join(
            line for line in f if not line.lstrip().startswith("\\")
        )

    conn = db.connection().connection
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        conn.commit()
        print(f"✅ 已匯入參考資料種子檔: {SEED_FILE}")
    except Exception as e:
        conn.rollback()
        print(f"❌ 種子檔匯入失敗: {e}")
    finally:
        cursor.close()


def init_db():
    # 增加重試機制，等待 PostgreSQL 啟動
    max_retries = 5
    for i in range(max_retries):
        try:
            print(f"📡 嘗試連線至資料庫 (第 {i+1} 次)...")
            models.Base.metadata.create_all(bind=engine)
            break
        except Exception as e:
            if i == max_retries - 1:
                print(f"❌ 無法連線至資料庫: {e}")
                return
            print(f"⏳ 資料庫尚未就緒，5 秒後重試...")
            time.sleep(5)

    db = SessionLocal()

    try:
        # 檢查 Additives 是否為空，空的話代表是全新資料庫，匯入參考資料種子檔
        count = db.query(models.Additive).count()
        if count == 0:
            print("🚀 資料庫中無添加物資料，正在從種子檔匯入參考資料...")
            _load_reference_seed(db)
        else:
            print(f"📊 資料庫已有 {count} 筆添加物資料，跳過自動匯入。")

        # admin_users / additive_suggestions / additive_comments 三張表不在
        # SQLAlchemy models 內(歷史因素獨立管理),故另外呼叫此腳本建立，
        # 並植入僅供本機開發使用的預設管理員帳號。
        print("🚀 正在建立管理審核相關資料表...")
        init_review_tables()

    except Exception as e:
        print(f"❌ 檢查失敗: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
