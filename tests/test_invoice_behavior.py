import datetime
import pytest
from unittest.mock import AsyncMock, patch, PropertyMock
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Invoice, Client
from backend.schemas import InvoiceCreateRequest, LineItem, RemindRequest
from backend.paypal_client import PayPalClient, PayPalAPIError
from backend.main import get_system_config
from backend.routes.invoices import (
    create_invoice,
    send_existing_invoice,
    cancel_invoice,
    remind_invoice,
    get_dashboard_stats,
)
from backend.routes.demo import clear_demo_data

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.mark.asyncio
async def test_create_invoice_unconfigured_paypal(test_db):
    """When PayPal is not configured, creating an invoice must return 503 error."""
    with patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=False):
        req = InvoiceCreateRequest(
            client_name="Test Client",
            client_email="test@client.com",
            currency="USD",
            items=[LineItem(name="Consulting", quantity=1.0, unit_price=100.0)],
            due_date="2026-11-20"
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_invoice(req, db=test_db)
        assert exc_info.value.status_code == 503
        assert "PayPal credentials are not configured" in exc_info.value.detail
        # Verify no invoice was saved in DB
        assert test_db.query(Invoice).count() == 0

@pytest.mark.asyncio
async def test_create_invoice_paypal_failure_raises_error(test_db):
    """When PayPal create draft fails, it raises an HTTP error and does NOT create a fake invoice."""
    with patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True), \
         patch("backend.routes.invoices.paypal_client.generate_next_invoice_number", new_callable=AsyncMock) as mock_gen, \
         patch("backend.routes.invoices.paypal_client.create_draft_invoice", new_callable=AsyncMock) as mock_create:
        
        mock_gen.return_value = "INV2-TEST-001"
        mock_create.side_effect = PayPalAPIError("Invalid currency or payload", status_code=400)

        req = InvoiceCreateRequest(
            client_name="Acme Corp",
            client_email="acme@example.com",
            currency="USD",
            items=[LineItem(name="Development", quantity=2.0, unit_price=150.0)],
            due_date="2026-11-25"
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_invoice(req, db=test_db)

        assert exc_info.value.status_code == 400
        assert "PayPal error: Invalid currency or payload" in exc_info.value.detail
        # Confirm no fake or fallback invoice was committed to the DB
        assert test_db.query(Invoice).count() == 0

@pytest.mark.asyncio
async def test_demo_invoice_actions_never_call_paypal(test_db):
    """Demo invoices (is_demo=True) update local state only and do not call PayPal."""
    # Seed a demo invoice
    demo_inv = Invoice(
        paypal_invoice_id="INV2-DEMO-001",
        invoice_number="INV-2026-DEMO",
        client_name="Demo Client",
        client_email="demo@client.com",
        currency="USD",
        amount=500.0,
        status="DRAFT",
        due_date="2026-11-20",
        issue_date="2026-11-01",
        is_demo=True
    )
    test_db.add(demo_inv)
    test_db.commit()
    test_db.refresh(demo_inv)

    with patch("backend.routes.invoices.paypal_client.send_invoice", new_callable=AsyncMock) as mock_send, \
         patch("backend.autopilot.paypal_client.remind_invoice", new_callable=AsyncMock) as mock_remind, \
         patch("backend.routes.invoices.paypal_client.cancel_invoice", new_callable=AsyncMock) as mock_cancel:

        # 1. Send demo invoice
        send_res = await send_existing_invoice(demo_inv.id, db=test_db)
        assert send_res["status"] == "SENT"
        assert send_res["is_demo"] is True
        mock_send.assert_not_called()

        # 2. Remind demo invoice
        remind_req = RemindRequest(subject="Reminder", note="Please pay", tone="friendly")
        remind_res = await remind_invoice(demo_inv.id, req=remind_req, db=test_db)
        assert remind_res["status"] == "success"
        mock_remind.assert_not_called()

        # 3. Cancel demo invoice
        cancel_res = await cancel_invoice(demo_inv.id, reason="Demo cancel", db=test_db)
        assert cancel_res["status"] == "CANCELLED"
        mock_cancel.assert_not_called()

@pytest.mark.asyncio
async def test_real_invoice_send_calls_paypal(test_db):
    """Non-demo invoices call PayPal when sending, and re-fetch to refresh view_url."""
    real_inv = Invoice(
        paypal_invoice_id="INV2-REAL-PAYPAL-ID",
        invoice_number="INV-2026-REAL",
        client_name="Real Client",
        client_email="real@client.com",
        currency="USD",
        amount=1000.0,
        status="DRAFT",
        due_date="2026-11-20",
        issue_date="2026-11-01",
        is_demo=False
    )
    test_db.add(real_inv)
    test_db.commit()
    test_db.refresh(real_inv)

    with patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True), \
         patch("backend.routes.invoices.paypal_client.send_invoice", new_callable=AsyncMock) as mock_send, \
         patch("backend.routes.invoices.paypal_client.get_invoice", new_callable=AsyncMock) as mock_get:
        mock_send.return_value = {"status": "success"}
        mock_get.return_value = {"id": "INV2-REAL-PAYPAL-ID", "view_url": "https://view.paypal.com/refreshed"}

        res = await send_existing_invoice(real_inv.id, db=test_db)
        assert res["status"] == "SENT"
        assert res["view_url"] == "https://view.paypal.com/refreshed"
        mock_send.assert_called_once_with("INV2-REAL-PAYPAL-ID", subject="Invoice INV-2026-REAL")
        mock_get.assert_called_once_with("INV2-REAL-PAYPAL-ID")

        # Verify view_url was persisted to DB
        test_db.refresh(real_inv)
        assert real_inv.view_url == "https://view.paypal.com/refreshed"

@pytest.mark.asyncio
async def test_create_and_send_refreshes_view_url(test_db):
    """create_invoice with send_now=True calls create, send, and get_invoice to refresh view_url."""
    with patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True), \
         patch("backend.routes.invoices.paypal_client.generate_next_invoice_number", new_callable=AsyncMock) as mock_gen, \
         patch("backend.routes.invoices.paypal_client.create_draft_invoice", new_callable=AsyncMock) as mock_create, \
         patch("backend.routes.invoices.paypal_client.send_invoice", new_callable=AsyncMock) as mock_send, \
         patch("backend.routes.invoices.paypal_client.get_invoice", new_callable=AsyncMock) as mock_get:

        mock_gen.return_value = "INV2-SENDNOW-001"
        mock_create.return_value = {
            "id": "INV2-PAYPAL-SENDNOW",
            "view_url": None
        }
        mock_send.return_value = {"status": "success"}
        mock_get.return_value = {
            "id": "INV2-PAYPAL-SENDNOW",
            "view_url": "https://view.paypal.com/after-send-link"
        }

        req = InvoiceCreateRequest(
            client_name="SendNow Client",
            client_email="sendnow@example.com",
            currency="USD",
            items=[LineItem(name="Consulting", quantity=1.0, unit_price=200.0)],
            due_date="2026-11-20",
            send_now=True
        )

        res = await create_invoice(req, db=test_db)
        assert res["status"] == "SENT"
        assert res["view_url"] == "https://view.paypal.com/after-send-link"
        mock_create.assert_called_once()
        mock_send.assert_called_once()
        mock_get.assert_called_once_with("INV2-PAYPAL-SENDNOW")

        # Verify persisted to DB
        inv_in_db = test_db.query(Invoice).filter(Invoice.paypal_invoice_id == "INV2-PAYPAL-SENDNOW").first()
        assert inv_in_db is not None
        assert inv_in_db.view_url == "https://view.paypal.com/after-send-link"


@pytest.mark.asyncio
async def test_api_config_returns_only_safe_fields():
    """GET /api/config returns only business_email, paypal_mode, paypal_configured and never secrets."""
    config = await get_system_config()
    assert set(config.keys()) == {"business_email", "paypal_mode", "paypal_configured"}
    assert "client_secret" not in config
    assert "client_id" not in config
    assert config["paypal_mode"] == "sandbox"


@pytest.mark.asyncio
async def test_dashboard_stats_returns_overdue_invoices_and_respects_demo_toggle(test_db):
    """Needs attention list receives overdue invoices matching KPI total, and respects include_demo."""
    real_inv = Invoice(
        paypal_invoice_id="INV2-REAL-OVERDUE",
        invoice_number="INV-2026-REAL-OD",
        client_name="Real Client",
        client_email="real@client.com",
        currency="USD",
        amount=1500.0,
        status="SENT",
        due_date="2026-11-01",
        issue_date="2026-10-15",
        is_demo=False
    )
    demo_inv = Invoice(
        paypal_invoice_id="INV2-DEMO-OVERDUE",
        invoice_number="INV-2026-DEMO-OD",
        client_name="Demo Client",
        client_email="demo@client.com",
        currency="USD",
        amount=6050.0,
        status="SENT",
        due_date="2026-11-05",
        issue_date="2026-10-20",
        is_demo=True
    )
    test_db.add_all([real_inv, demo_inv])
    test_db.commit()

    with patch("backend.routes.invoices.get_simulated_today", return_value=datetime.date(2026, 11, 12)), \
         patch("backend.autopilot.autopilot_agent.generate_cashflow_insight", new_callable=AsyncMock) as mock_insight:
        mock_insight.return_value = {"summary": "Healthy cashflow", "projected_30d": 7550.0, "confidence_level": "High"}

        # 1. With include_demo=True (default)
        stats_with_demo = await get_dashboard_stats(include_demo=True, db=test_db)
        assert stats_with_demo["total_overdue"] == 7550.0
        assert len(stats_with_demo["overdue_invoices"]) == 2
        assert stats_with_demo["has_demo_data"] is True
        assert stats_with_demo["demo_included"] is True
        overdue_numbers = [inv["invoice_number"] for inv in stats_with_demo["overdue_invoices"]]
        assert "INV-2026-REAL-OD" in overdue_numbers
        assert "INV-2026-DEMO-OD" in overdue_numbers

        # 2. With include_demo=False
        stats_no_demo = await get_dashboard_stats(include_demo=False, db=test_db)
        assert stats_no_demo["total_overdue"] == 1500.0
        assert len(stats_no_demo["overdue_invoices"]) == 1
        assert stats_no_demo["overdue_invoices"][0]["invoice_number"] == "INV-2026-REAL-OD"
        assert stats_no_demo["has_demo_data"] is True
        assert stats_no_demo["demo_included"] is False


@pytest.mark.asyncio
async def test_clear_demo_data_leaves_real_invoices_untouched(test_db):
    """Clear demo data deletes only is_demo=True rows and preserves real invoices."""
    real_inv = Invoice(
        paypal_invoice_id="INV2-REAL-KEEP",
        invoice_number="INV-2026-REAL-KEEP",
        client_name="Real Client",
        client_email="real@client.com",
        currency="USD",
        amount=2500.0,
        status="SENT",
        due_date="2026-11-20",
        issue_date="2026-11-01",
        is_demo=False
    )
    demo_inv = Invoice(
        paypal_invoice_id="INV2-DEMO-DELETE",
        invoice_number="INV-2026-DEMO-DEL",
        client_name="Demo Client",
        client_email="demo@client.com",
        currency="USD",
        amount=500.0,
        status="SENT",
        due_date="2026-11-10",
        issue_date="2026-11-01",
        is_demo=True
    )
    test_db.add_all([real_inv, demo_inv])
    test_db.commit()

    res = clear_demo_data(db=test_db)
    assert res["status"] == "success"
    assert res["deleted_count"] == 1

    remaining_invoices = test_db.query(Invoice).all()
    assert len(remaining_invoices) == 1
    assert remaining_invoices[0].invoice_number == "INV-2026-REAL-KEEP"
    assert remaining_invoices[0].is_demo is False


