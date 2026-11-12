import pytest
from unittest.mock import AsyncMock, patch, PropertyMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # ensure all models are registered on Base.metadata
from backend.main import app
from backend.database import Base, get_db
from backend.models import Invoice, AuditLog
from backend.config import settings
from backend.paypal_client import PayPalClient

VALID_HEADERS = {
    "paypal-transmission-id": "trans-mock-12345",
    "paypal-transmission-time": "2026-11-12T10:00:00Z",
    "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-transmission-sig": "mock_valid_signature_token"
}

PAID_PAYLOAD = {
    "event_type": "INVOICING.INVOICE.PAID",
    "resource": {
        "id": "INV2-TEST-PAYPAL-123",
        "detail": {
            "invoice_number": "INV-2026-TEST"
        }
    }
}

@pytest.fixture
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def test_db(test_engine):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def sample_invoice(test_db):
    inv = Invoice(
        paypal_invoice_id="INV2-TEST-PAYPAL-123",
        invoice_number="INV-2026-TEST",
        client_name="Acme Corp",
        client_email="acme@example.com",
        currency="USD",
        amount=500.0,
        status="SENT",
        due_date="2026-11-20",
        issue_date="2026-11-01",
        is_demo=False
    )
    test_db.add(inv)
    test_db.commit()
    test_db.refresh(inv)
    return inv

@pytest.fixture
def client(test_engine):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()

def test_webhook_no_signature_headers_rejected(client, test_db, sample_invoice):
    """A request with no signature headers is rejected, invoice status unchanged, WEBHOOK_REJECTED audit entry created."""
    with patch.object(settings, "ALLOW_UNSIGNED_WEBHOOKS", False):
        res = client.post("/webhooks/paypal", json=PAID_PAYLOAD)
        assert res.status_code == 400
        assert "missing signature headers" in res.json().get("detail", "")

        # Verify invoice status did not change
        test_db.refresh(sample_invoice)
        assert sample_invoice.status == "SENT"
        assert sample_invoice.paid_at is None

        # Verify WEBHOOK_REJECTED audit entry
        log = test_db.query(AuditLog).filter(AuditLog.action == "WEBHOOK_REJECTED").first()
        assert log is not None
        assert log.details == "missing signature headers"
        assert log.target_id == "INV2-TEST-PAYPAL-123"

def test_webhook_headers_present_webhook_id_unset_rejected(client, test_db, sample_invoice):
    """A request with headers but PAYPAL_WEBHOOK_ID unset is rejected."""
    with patch.object(settings, "ALLOW_UNSIGNED_WEBHOOKS", False), \
         patch.object(settings, "PAYPAL_WEBHOOK_ID", ""), \
         patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True):
        res = client.post("/webhooks/paypal", json=PAID_PAYLOAD, headers=VALID_HEADERS)
        assert res.status_code == 400
        assert "webhook id not configured" in res.json().get("detail", "")

        test_db.refresh(sample_invoice)
        assert sample_invoice.status == "SENT"
        assert sample_invoice.paid_at is None

        log = test_db.query(AuditLog).filter(AuditLog.action == "WEBHOOK_REJECTED").first()
        assert log is not None
        assert log.details == "webhook id not configured"

def test_webhook_headers_present_verification_false_rejected(client, test_db, sample_invoice):
    """A request with headers where verify_webhook_signature returns False is rejected."""
    with patch.object(settings, "ALLOW_UNSIGNED_WEBHOOKS", False), \
         patch.object(settings, "PAYPAL_WEBHOOK_ID", "WH-VALID-ID-999"), \
         patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True), \
         patch("backend.routes.webhooks.paypal_client.verify_webhook_signature", new_callable=AsyncMock, return_value=False):
        res = client.post("/webhooks/paypal", json=PAID_PAYLOAD, headers=VALID_HEADERS)
        assert res.status_code == 400
        assert "verification failed" in res.json().get("detail", "")

        test_db.refresh(sample_invoice)
        assert sample_invoice.status == "SENT"
        assert sample_invoice.paid_at is None

        log = test_db.query(AuditLog).filter(AuditLog.action == "WEBHOOK_REJECTED").first()
        assert log is not None
        assert log.details == "verification failed"

def test_webhook_headers_present_verification_true_paid_accepted(client, test_db, sample_invoice):
    """A request with headers where verify_webhook_signature returns True and a PAID event updates matching invoice to PAID and sets paid_at."""
    with patch.object(settings, "ALLOW_UNSIGNED_WEBHOOKS", False), \
         patch.object(settings, "PAYPAL_WEBHOOK_ID", "WH-VALID-ID-999"), \
         patch.object(PayPalClient, "is_configured", new_callable=PropertyMock, return_value=True), \
         patch("backend.routes.webhooks.paypal_client.verify_webhook_signature", new_callable=AsyncMock, return_value=True):
        res = client.post("/webhooks/paypal", json=PAID_PAYLOAD, headers=VALID_HEADERS)
        assert res.status_code == 200
        assert res.json().get("status") == "success"

        test_db.refresh(sample_invoice)
        assert sample_invoice.status == "PAID"
        assert sample_invoice.paid_at is not None

        log = test_db.query(AuditLog).filter(AuditLog.action == "INVOICE_PAID_WEBHOOK").first()
        assert log is not None
        assert str(sample_invoice.id) in log.target_id

def test_webhook_allow_unsigned_webhooks_paid_accepted(client, test_db, sample_invoice):
    """With ALLOW_UNSIGNED_WEBHOOKS enabled, an unsigned PAID event is accepted."""
    with patch.object(settings, "ALLOW_UNSIGNED_WEBHOOKS", True):
        res = client.post("/webhooks/paypal", json=PAID_PAYLOAD)
        assert res.status_code == 200
        assert res.json().get("status") == "success"

        test_db.refresh(sample_invoice)
        assert sample_invoice.status == "PAID"
        assert sample_invoice.paid_at is not None

def test_webhook_malformed_json_body_rejected(client, test_db, sample_invoice):
    """A request with malformed or invalid JSON body is rejected with 400 and creates WEBHOOK_REJECTED audit entry."""
    res = client.post(
        "/webhooks/paypal",
        content=b"not-a-valid-json-payload",
        headers={"Content-Type": "application/json", **VALID_HEADERS}
    )
    assert res.status_code == 400
    assert "Invalid or missing JSON body" in res.json().get("detail", "")

    test_db.refresh(sample_invoice)
    assert sample_invoice.status == "SENT"

    log = test_db.query(AuditLog).filter(AuditLog.action == "WEBHOOK_REJECTED").first()
    assert log is not None
    assert log.details == "invalid or missing JSON body"
