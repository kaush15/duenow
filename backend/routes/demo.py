import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.database import get_db
from backend.models import AppSetting
from backend.demo_seed import seed_demo_data, fast_forward_time
from backend.autopilot import get_simulated_today, get_app_setting, set_app_setting
from backend.paypal_client import paypal_client
from backend.config import settings

router = APIRouter(prefix="/api/demo", tags=["demo"])

class SettingsUpdateSchema(BaseModel):
    auto_approve_friendly: str = None
    preferred_language: str = None
    default_currency: str = None
    paypal_client_id: str = None
    paypal_client_secret: str = None

@router.post("/seed")
def seed_data(reset: bool = True, db: Session = Depends(get_db)):
    """Seeds realistic demo clients and invoices for live demonstration."""
    seed_demo_data(db, reset_existing=reset)
    return {"status": "success", "message": "Demo data successfully seeded."}

@router.post("/fast-forward")
def fast_forward(days: int = 7, db: Session = Depends(get_db)):
    """Fast-forwards simulated time by X days."""
    new_offset = fast_forward_time(db, days)
    sim_today = get_simulated_today(db)
    return {
        "status": "success",
        "offset_days": new_offset,
        "simulated_date": sim_today.isoformat()
    }

@router.post("/reset-time")
def reset_time(db: Session = Depends(get_db)):
    """Resets simulated time back to real current date."""
    set_app_setting(db, "simulated_days_offset", "0")
    return {
        "status": "success",
        "offset_days": 0,
        "simulated_date": datetime.date.today().isoformat()
    }

@router.get("/status")
def get_demo_status(db: Session = Depends(get_db)):
    """Returns current environment status, simulated date, and active settings."""
    sim_today = get_simulated_today(db)
    offset_setting = db.query(AppSetting).filter(AppSetting.key == "simulated_days_offset").first()
    offset = int(offset_setting.value) if offset_setting and offset_setting.value.isdigit() else 0

    return {
        "simulated_date": sim_today.isoformat(),
        "offset_days": offset,
        "paypal_configured": paypal_client.is_configured,
        "paypal_client_id": settings.PAYPAL_CLIENT_ID[:8] + "..." if settings.PAYPAL_CLIENT_ID else "",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "auto_approve_friendly": get_app_setting(db, "auto_approve_friendly", "false").lower() == "true",
        "preferred_language": get_app_setting(db, "preferred_language", "English"),
        "default_currency": get_app_setting(db, "default_currency", "USD")
    }

@router.post("/settings")
def update_settings(req: SettingsUpdateSchema, db: Session = Depends(get_db)):
    """Updates application settings."""
    if req.auto_approve_friendly is not None:
        set_app_setting(db, "auto_approve_friendly", req.auto_approve_friendly)
    if req.preferred_language is not None:
        set_app_setting(db, "preferred_language", req.preferred_language)
    if req.default_currency is not None:
        set_app_setting(db, "default_currency", req.default_currency)
    
    return {"status": "success", "message": "Settings updated."}
