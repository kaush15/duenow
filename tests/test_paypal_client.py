import pytest
from unittest.mock import AsyncMock, patch, call
from backend.paypal_client import PayPalClient, PayPalAPIError

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
    """POST returns real PayPal shape (link object with href, no 'id'), followed by GET."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            # 1st call: POST /v2/invoicing/invoices — real PayPal response: link object, no "id"
            {
                "rel": "self",
                "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-MOCK-ID",
                "method": "GET"
            },
            # 2nd call: GET /v2/invoicing/invoices/INV2-MOCK-ID
            {
                "id": "INV2-MOCK-ID",
                "status": "DRAFT",
                "detail": {
                    "invoice_number": "INV2-001",
                    "metadata": {
                        "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-MOCK-ID",
                        "invoicer_view_url": "https://www.sandbox.paypal.com/invoice/payerView/details/INV2-MOCK-ID"
                    }
                }
            }
        ]
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
        assert res["invoicer_view_url"] == "https://www.sandbox.paypal.com/invoice/payerView/details/INV2-MOCK-ID"
        assert mock_req.call_count == 2
        assert mock_req.call_args_list[0][0][0] == "POST"
        assert mock_req.call_args_list[0][0][1] == "/v2/invoicing/invoices"
        assert mock_req.call_args_list[1] == call("GET", "/v2/invoicing/invoices/INV2-MOCK-ID")

@pytest.mark.asyncio
async def test_create_draft_invoice_parse_id_from_links_list(client):
    """POST returns links array with rel 'self' — ID is parsed from href."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {
                "links": [
                    {"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-LINKS-ID", "method": "GET"}
                ]
            },
            {
                "id": "INV2-LINKS-ID",
                "detail": {
                    "metadata": {
                        "recipient_view_url": "https://view.paypal.com/inv/INV2-LINKS-ID"
                    }
                }
            }
        ]
        res = await client.create_draft_invoice(
            invoice_number="INV2-002",
            client_name="Test Client",
            client_email="client@test.com",
            currency="USD",
            items=[{"name": "Consulting", "quantity": 1, "unit_price": 300.0}],
            due_date="2026-11-20",
            issue_date="2026-11-02"
        )
        assert res["id"] == "INV2-LINKS-ID"
        assert res["view_url"] == "https://view.paypal.com/inv/INV2-LINKS-ID"
        assert mock_req.call_count == 2
        assert mock_req.call_args_list[1] == call("GET", "/v2/invoicing/invoices/INV2-LINKS-ID")

@pytest.mark.asyncio
async def test_create_draft_invoice_id_present_directly(client):
    """POST response directly containing 'id' still triggers GET follow-up."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {"id": "INV2-DIRECT-ID"},
            {
                "id": "INV2-DIRECT-ID",
                "links": [{"rel": "payer-view", "href": "https://view.paypal.com/payer"}]
            }
        ]
        res = await client.create_draft_invoice(
            invoice_number="INV2-003",
            client_name="Test Client",
            client_email="client@test.com",
            currency="USD",
            items=[{"name": "Support", "quantity": 1, "unit_price": 100.0}],
            due_date="2026-11-20",
            issue_date="2026-11-02"
        )
        assert res["id"] == "INV2-DIRECT-ID"
        assert res["view_url"] == "https://view.paypal.com/payer"
        assert mock_req.call_count == 2
        assert mock_req.call_args_list[1] == call("GET", "/v2/invoicing/invoices/INV2-DIRECT-ID")

@pytest.mark.asyncio
async def test_create_draft_invoice_missing_id_raises_paypal_api_error(client):
    """POST response with no id, href, or self link raises PayPalAPIError."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"rel": "unrelated", "links": []}
        with pytest.raises(PayPalAPIError) as exc_info:
            await client.create_draft_invoice(
                invoice_number="INV2-004",
                client_name="Test Client",
                client_email="client@test.com",
                currency="USD",
                items=[{"name": "Item", "quantity": 1, "unit_price": 50.0}],
                due_date="2026-11-20",
                issue_date="2026-11-02"
            )
        assert "Failed to extract PayPal invoice ID" in exc_info.value.message
        assert mock_req.call_count == 1

@pytest.mark.asyncio
async def test_get_invoice_no_made_up_fallback_url(client):
    """GET response without recipient_view_url or payer-view link returns None for view_url (no fake URL)."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {
            "id": "INV2-NO-URL",
            "status": "DRAFT",
            "detail": {"metadata": {}}
        }
        res = await client.get_invoice("INV2-NO-URL")
        assert res["id"] == "INV2-NO-URL"
        assert res["view_url"] is None
        mock_req.assert_called_once_with("GET", "/v2/invoicing/invoices/INV2-NO-URL")

@pytest.mark.asyncio
async def test_get_invoice_fallback_to_payer_view_link(client):
    """GET response without metadata recipient_view_url falls back to payer-view in links."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {
            "id": "INV2-LINK-ONLY",
            "links": [{"rel": "payer-view", "href": "https://view.paypal.com/payer-fallback"}]
        }
        res = await client.get_invoice("INV2-LINK-ONLY")
        assert res["id"] == "INV2-LINK-ONLY"
        assert res["view_url"] == "https://view.paypal.com/payer-fallback"

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
