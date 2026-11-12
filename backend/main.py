import asyncio
import datetime
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import engine, Base, SessionLocal, run_migrations
from backend.models import Invoice, Client
from backend.paypal_client import paypal_client
from backend.demo_seed import seed_demo_data
from backend.scoring import calculate_client_score

from backend.routes.smart_create import router as smart_create_router
from backend.routes.invoices import router as invoices_router
from backend.routes.clients import router as clients_router
from backend.routes.autopilot import router as autopilot_router
from backend.routes.webhooks import router as webhooks_router
from backend.routes.demo import router as demo_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("duenow")

# Create database tables and apply safe migrations
run_migrations()

async def paypal_polling_background_task():
    """Polls PayPal Sandbox every 30s to synchronize open invoice statuses."""
    logger.info("Starting PayPal 30s background sync poller...")
    while True:
        try:
            await asyncio.sleep(30)
            if paypal_client.is_configured:
                db: Session = SessionLocal()
                try:
                    open_invoices = db.query(Invoice).filter(
                        Invoice.status.in_(["SENT", "SCHEDULED"]),
                        Invoice.paypal_invoice_id.isnot(None)
                    ).all()

                    for inv in open_invoices:
                        try:
                            pp_data = await paypal_client.get_invoice(inv.paypal_invoice_id)
                            pp_status = pp_data.get("status")

                            # Refresh view_url from real response if available
                            pp_view_url = pp_data.get("view_url")
                            has_changes = False
                            if pp_view_url and inv.view_url != pp_view_url:
                                inv.view_url = pp_view_url
                                has_changes = True

                            if pp_status and pp_status != inv.status:
                                logger.info(f"Poller sync: invoice {inv.invoice_number} status updated {inv.status} -> {pp_status}")
                                inv.status = pp_status
                                has_changes = True

                                if pp_status in ["PAID", "MARKED_AS_PAID"] and not inv.paid_at:
                                    # Try to read payment_date from payments.transactions
                                    paid_dt = None
                                    payments = pp_data.get("payments") if isinstance(pp_data.get("payments"), dict) else {}
                                    transactions = payments.get("transactions") if isinstance(payments.get("transactions"), list) else []
                                    if transactions and isinstance(transactions[-1], dict) and transactions[-1].get("payment_date"):
                                        try:
                                            paid_dt = datetime.datetime.fromisoformat(transactions[-1]["payment_date"].replace("Z", "+00:00"))
                                        except Exception:
                                            pass
                                    inv.paid_at = paid_dt or datetime.datetime.utcnow()
                                    inv.add_timeline_event("PAYMENT_RECEIVED", "Status verified via PayPal 30s Poller")

                                    if inv.client:
                                        client_invoices = [i.to_dict() for i in inv.client.invoices]
                                        new_score, lag_days, _ = calculate_client_score(client_invoices)
                                        inv.client.reliability_score = new_score
                                        inv.client.predicted_pay_days = lag_days
                                elif pp_status == "CANCELLED":
                                    inv.add_timeline_event("INVOICE_CANCELLED", "Status verified via PayPal 30s Poller")

                            if has_changes:
                                db.commit()
                        except Exception as e:
                            # Ignore transient errors during poll
                            pass
                finally:
                    db.close()
        except asyncio.CancelledError:
            logger.info("PayPal polling task cancelled.")
            break
        except Exception as e:
            logger.warning(f"Error in PayPal poller cycle: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure database is initialized with demo data if empty
    db: Session = SessionLocal()
    try:
        if db.query(Client).count() == 0:
            logger.info("Initializing database with demo seed data...")
            seed_demo_data(db)
    finally:
        db.close()

    # Start background polling task
    poll_task = asyncio.create_task(paypal_polling_background_task())

    yield

    # Shutdown
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title="DueNow API",
    description="AI-powered Invoicing & Collections Engine for Freelancers",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(smart_create_router)
app.include_router(invoices_router)
app.include_router(clients_router)
app.include_router(autopilot_router)
app.include_router(webhooks_router)
app.include_router(demo_router)

# Health check endpoint for deployment monitoring
@app.get("/api/health", tags=["system"])
async def health_check():
    return {"status": "ok"}

# Mount Frontend Static Assets
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/")
    async def serve_root():
        return FileResponse(frontend_dist / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # Allow API and Webhook requests to pass through
        if full_path.startswith("api/") or full_path.startswith("webhooks/"):
            return HTMLResponse(status_code=404, content="API route not found")
        
        file_path = frontend_dist / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(frontend_dist / "index.html")
else:
    @app.get("/")
    def index_placeholder():
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h1>DueNow API is Running</h1>"
            "<p>Frontend is currently building. Explore API docs at <a href='/docs'>/docs</a></p>"
            "</body></html>"
        )
