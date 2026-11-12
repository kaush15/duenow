from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from backend.config import settings

engine = create_engine(
    settings.DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def run_migrations():
    """Ensures schema changes are applied safely to existing SQLite database."""
    Base.metadata.create_all(bind=engine)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(invoices)"))
            cols = [row[1] for row in result.fetchall()]
            if cols and "is_demo" not in cols:
                conn.execute(text("ALTER TABLE invoices ADD COLUMN is_demo BOOLEAN DEFAULT 0"))
                conn.commit()
            if cols:
                # Mark existing demo invoices as demo
                conn.execute(text("UPDATE invoices SET is_demo = 1 WHERE paypal_invoice_id LIKE 'INV2-DEMO-%'"))
                conn.commit()
    except Exception:
        pass
