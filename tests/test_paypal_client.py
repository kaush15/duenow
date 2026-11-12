import pytest
from unittest.mock import AsyncMock, patch
from backend.paypal_client import PayPalClient

@pytest.fixture
def client():
    c = PayPalClient()
    c.client_id = "test_client_id"
    c.client_secret = "test_client_secret"
    c._access_token = "mock_access_token_123"
    c._token_expires_at = 9999999999.0
    return c

@pytest.mark.asyncio
async def test_generate_next_invoice_number(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"invoice_number": "INV2-ABC-123"}
        inv_num = await client.generate_next_invoice_number()
        assert inv_num == "INV2-ABC-123"
        mock_req.assert_called_once_with("POST", "/v2/invoicing/generate-next-invoice-number")

@pytest.mark.asyncio
async def test_create_draft_invoice(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {
            "id": "INV2-MOCK-ID",
            "links": [{"rel": "payer-view", "href": "https://www.sandbox.paypal.com/invoice/p/#INV2-MOCK-ID"}]
        }
        res = await client.create_draft_invoice(
            invoice_number="INV2-001",
            client_name="Test Client",
            client_email="client@test.com",
            currency="USD",
            items=[{"name": "Design", "quantity": 1, "unit_price": 500.0}],
            due_date="2026-11-20",
            issue_date="2026-11-02"
        )
        assert res["id"] == "INV2-MOCK-ID"
        assert res["view_url"] == "https://www.sandbox.paypal.com/invoice/p/#INV2-MOCK-ID"
        assert mock_req.call_count == 1

@pytest.mark.asyncio
async def test_send_invoice(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"status": "success", "status_code": 204}
        res = await client.send_invoice("INV2-MOCK-ID", subject="Test Subject")
        assert res["status"] == "success"
        mock_req.assert_called_once()

@pytest.mark.asyncio
async def test_remind_invoice(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"status": "success", "status_code": 204}
        res = await client.remind_invoice("INV2-MOCK-ID", subject="Past Due Reminder", note="Please pay promptly.")
        assert res["status"] == "success"
        mock_req.assert_called_once()

@pytest.mark.asyncio
async def test_cancel_invoice(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"status": "success", "status_code": 204}
        res = await client.cancel_invoice("INV2-MOCK-ID", reason="Client cancelled contract")
        assert res["status"] == "success"
        mock_req.assert_called_once()

@pytest.mark.asyncio
async def test_record_payment(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"payment_id": "PAY-12345"}
        res = await client.record_payment("INV2-MOCK-ID", amount=500.0, currency="USD")
        assert res["payment_id"] == "PAY-12345"
        mock_req.assert_called_once()

@pytest.mark.asyncio
async def test_verify_webhook_signature(client):
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"verification_status": "SUCCESS"}
        is_valid = await client.verify_webhook_signature(
            transmission_id="trans_1",
            transmission_time="2026-11-02T12:00:00Z",
            cert_url="https://api.sandbox.paypal.com/cert",
            auth_algo="SHA256withRSA",
            transmission_sig="sig123",
            webhook_id="wh_123",
            webhook_event={"event_type": "INVOICING.INVOICE.PAID"}
        )
        assert is_valid is True
