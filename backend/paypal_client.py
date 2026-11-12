import time
import base64
import logging
from typing import Dict, Any, Optional, List
import httpx
from backend.config import settings

logger = logging.getLogger("paypal_client")

class PayPalAPIError(Exception):
    def __init__(self, message: str, status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details

class PayPalClient:
    def __init__(self):
        self.base_url = settings.PAYPAL_BASE_URL.rstrip("/")
        self.client_id = settings.PAYPAL_CLIENT_ID
        self.client_secret = settings.PAYPAL_CLIENT_SECRET
        self.business_email = settings.PAYPAL_BUSINESS_EMAIL
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.client_id != self.client_secret)

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """Retrieves and caches OAuth2 client-credentials token."""
        if not force_refresh and self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise PayPalAPIError("PayPal Client ID or Secret is missing in .env", status_code=401)

        auth_str = f"{self.client_id}:{self.client_secret}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()

        url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {"grant_type": "client_credentials"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, data=data)
                if resp.status_code != 200:
                    err_body = resp.text
                    logger.error(f"PayPal auth failed ({resp.status_code}): {err_body}")
                    raise PayPalAPIError(
                        f"PayPal Sandbox authentication failed ({resp.status_code}). Please verify PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env",
                        status_code=resp.status_code,
                        details=err_body
                    )
                
                payload = resp.json()
                self._access_token = payload.get("access_token")
                expires_in = payload.get("expires_in", 3600)
                # Cache until 60 seconds before actual expiration
                self._token_expires_at = time.time() + expires_in - 60
                return self._access_token
        except httpx.RequestError as e:
            logger.error(f"Network error during PayPal token request: {e}")
            raise PayPalAPIError(f"Could not connect to PayPal Sandbox: {str(e)}", status_code=503)

    async def _request(self, method: str, path: str, json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Authenticated HTTP request wrapper."""
        token = await self.get_access_token()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.request(method, url, headers=headers, json=json_data, params=params)
                
                # Check for 204 No Content
                if resp.status_code == 204:
                    return {"status": "success", "status_code": 204}
                
                if resp.status_code >= 400:
                    err_msg = resp.text
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("message") or err_json.get("error_description") or resp.text
                    except Exception:
                        err_json = None

                    logger.error(f"PayPal API error [{method} {path}] {resp.status_code}: {err_msg}")
                    raise PayPalAPIError(
                        f"PayPal API error ({resp.status_code}): {err_msg}",
                        status_code=resp.status_code,
                        details=err_json or resp.text
                    )

                return resp.json()
        except httpx.RequestError as e:
            logger.error(f"PayPal network error [{method} {path}]: {e}")
            raise PayPalAPIError(f"PayPal network error: {str(e)}", status_code=503)

    async def generate_next_invoice_number(self) -> str:
        """POST /v2/invoicing/generate-next-invoice-number"""
        try:
            res = await self._request("POST", "/v2/invoicing/generate-next-invoice-number")
            return res.get("invoice_number", f"INV-{int(time.time())}")
        except Exception as e:
            logger.warning(f"Failed to generate next invoice number from PayPal: {e}. Generating local fallback.")
            return f"DN-{int(time.time()) % 100000:05d}"

    async def create_draft_invoice(
        self,
        invoice_number: str,
        client_name: str,
        client_email: str,
        currency: str,
        items: List[Dict[str, Any]],
        due_date: str,
        issue_date: str,
        note: Optional[str] = None,
        terms: Optional[str] = None
    ) -> Dict[str, Any]:
        """POST /v2/invoicing/invoices"""
        formatted_items = []
        for it in items:
            formatted_items.append({
                "name": it.get("name", "Service Item"),
                "description": it.get("description", ""),
                "quantity": str(it.get("quantity", 1)),
                "unit_amount": {
                    "currency_code": currency,
                    "value": f"{float(it.get('unit_price', 0.0)):.2f}"
                },
                "unit_of_measure": "QUANTITY"
            })

        payload = {
            "detail": {
                "invoice_number": invoice_number,
                "invoice_date": issue_date,
                "currency_code": currency,
                "note": note or "Thank you for your business!",
                "term": terms or "Due upon date specified.",
                "payment_term": {
                    "term_type": "DUE_ON_DATE_SPECIFIED",
                    "due_date": due_date
                }
            },
            "invoicer": {
                "name": {
                    "given_name": "DueNow",
                    "surname": "Merchant"
                },
                "email_address": self.business_email
            },
            "primary_recipients": [
                {
                    "billing_info": {
                        "name": {
                            "given_name": client_name
                        },
                        "email_address": client_email
                    }
                }
            ],
            "items": formatted_items
        }

        res = await self._request("POST", "/v2/invoicing/invoices", json_data=payload)
        
        # Extract invoice ID and links
        invoice_id = res.get("id")
        links = res.get("links", [])
        view_url = None
        for l in links:
            if l.get("rel") in ["payer-view", "self"]:
                if l.get("rel") == "payer-view":
                    view_url = l.get("href")
        
        if not view_url and invoice_id:
            view_url = f"https://www.sandbox.paypal.com/invoice/p/#{invoice_id}"

        res["view_url"] = view_url
        return res

    async def send_invoice(self, paypal_invoice_id: str, subject: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        """POST /v2/invoicing/invoices/{id}/send"""
        payload: Dict[str, Any] = {"send_to_invoicer": True}
        if subject:
            payload["subject"] = subject
        if note:
            payload["note"] = note
            
        res = await self._request("POST", f"/v2/invoicing/invoices/{paypal_invoice_id}/send", json_data=payload)
        return res

    async def remind_invoice(self, paypal_invoice_id: str, subject: str, note: str) -> Dict[str, Any]:
        """POST /v2/invoicing/invoices/{id}/remind with AI-written subject and note"""
        payload = {
            "subject": subject,
            "note": note,
            "send_to_invoicer": True,
            "send_to_recipient": True
        }
        res = await self._request("POST", f"/v2/invoicing/invoices/{paypal_invoice_id}/remind", json_data=payload)
        return res

    async def get_invoice(self, paypal_invoice_id: str) -> Dict[str, Any]:
        """GET /v2/invoicing/invoices/{id}"""
        res = await self._request("GET", f"/v2/invoicing/invoices/{paypal_invoice_id}")
        
        # Check payer view link
        links = res.get("links", [])
        view_url = None
        for l in links:
            if l.get("rel") == "payer-view":
                view_url = l.get("href")
        if not view_url:
            view_url = f"https://www.sandbox.paypal.com/invoice/p/#{paypal_invoice_id}"
        res["view_url"] = view_url
        return res

    async def list_invoices(self, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """GET /v2/invoicing/invoices"""
        params = {
            "page": page,
            "page_size": page_size,
            "total_required": "true"
        }
        return await self._request("GET", "/v2/invoicing/invoices", params=params)

    async def cancel_invoice(self, paypal_invoice_id: str, reason: str = "Cancelled by user") -> Dict[str, Any]:
        """POST /v2/invoicing/invoices/{id}/cancel"""
        payload = {
            "subject": "Invoice Cancelled",
            "note": reason,
            "send_to_invoicer": True,
            "send_to_recipient": True
        }
        return await self._request("POST", f"/v2/invoicing/invoices/{paypal_invoice_id}/cancel", json_data=payload)

    async def record_payment(
        self,
        paypal_invoice_id: str,
        amount: float,
        currency: str = "USD",
        payment_date: Optional[str] = None,
        method: str = "OTHER",
        note: str = "Demo payment recorded manually"
    ) -> Dict[str, Any]:
        """POST /v2/invoicing/invoices/{id}/payments fallback for demos"""
        if not payment_date:
            payment_date = time.strftime("%Y-%m-%d")

        payload = {
            "method": method,
            "payment_date": payment_date,
            "amount": {
                "currency_code": currency,
                "value": f"{amount:.2f}"
            },
            "note": note
        }
        return await self._request("POST", f"/v2/invoicing/invoices/{paypal_invoice_id}/payments", json_data=payload)

    async def verify_webhook_signature(
        self,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_id: str,
        webhook_event: Dict[str, Any]
    ) -> bool:
        """POST /v1/notifications/verify-webhook-signature"""
        payload = {
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_id": webhook_id,
            "webhook_event": webhook_event
        }
        try:
            res = await self._request("POST", "/v1/notifications/verify-webhook-signature", json_data=payload)
            status = res.get("verification_status")
            return status == "SUCCESS"
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            return False

paypal_client = PayPalClient()
