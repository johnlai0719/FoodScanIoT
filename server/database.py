import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

db_url = os.getenv("DATABASE_URL")
if not db_url:
    db_user = os.getenv("DB_USER", "root")
    db_pass = os.getenv("DB_PASSWORD", "password")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "product_db")
    db_url = f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

if db_url and "charset=" not in db_url:
    if "?" in db_url:
        db_url += "&charset=utf8mb4"
    else:
        db_url += "?charset=utf8mb4"

SQLALCHEMY_DATABASE_URL = db_url

# For Azure, SSL is usually required. 
# We add connect_args to support SSL if needed.
connect_args = {}
if os.getenv("DB_SSL") == "true":
    # Azure's MySQL usually works with these arguments
    connect_args = {"ssl": {"ca": "DigiCertGlobalRootG2.crt.pem"}}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
