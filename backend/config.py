import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("duenow.config")

# Load .env file
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

class Settings:
    # PayPal Sandbox
    PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_WEBHOOK_ID: str = os.getenv("PAYPAL_WEBHOOK_ID", "")
    PAYPAL_MODE: str = os.getenv("PAYPAL_MODE", "sandbox")
    PAYPAL_BUSINESS_EMAIL: str = os.getenv("PAYPAL_BUSINESS_EMAIL", "")
    PAYPAL_BASE_URL: str = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"
    ALLOW_UNSIGNED_WEBHOOKS: bool = os.getenv("ALLOW_UNSIGNED_WEBHOOKS", "false").lower() == "true"

    # LLM
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").lower()
    LLM_API_KEY: str = (os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip().strip('"').strip("'")
    LLM_MODEL: str = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash"

    # App
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")
    PORT: int = int(os.getenv("PORT", "8000"))
    DATABASE_URL: str = f"sqlite:///{Path(__file__).resolve().parent.parent / 'duenow.db'}"

    # Supported Currencies in PayPal Sandbox
    SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY"]

    def __init__(self):
        if not self.PAYPAL_CLIENT_ID or not self.PAYPAL_CLIENT_SECRET:
            logger.warning("PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is missing. PayPal integration will run in unconfigured mode.")
        if not self.PAYPAL_WEBHOOK_ID:
            logger.warning("PAYPAL_WEBHOOK_ID is not set. Incoming PayPal webhooks will be rejected unless ALLOW_UNSIGNED_WEBHOOKS is enabled.")
        if not self.LLM_API_KEY:
            logger.warning("LLM_API_KEY is missing. AI features will fallback to template responses.")

settings = Settings()
