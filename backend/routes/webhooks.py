import datetime
import logging
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Request, Header, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Invoice, AuditLog
from backend.config import settings
from backend.paypal_client import paypal_client
from backend.scoring import calculate_client_score, generate_score_explanation

logger = logging.getLogger("webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@router.post("/paypal")
async def handle_paypal_webhook(
    request: Request,
    db: Session = Depends(get_db),
    paypal_transmission_id: Optional[str] = Header(None, alias="paypal-transmission-id"),
    paypal_transmission_time: Optional[str] = Header(None, alias="paypal-transmission-time"),
    paypal_cert_url: Optional[str] = Header(None, alias="paypal-cert-url"),
    paypal_auth_algo: Optional[str] = Header(None, alias="paypal-auth-algo"),
    paypal_transmission_sig: Optional[str] = Header(None, alias="paypal-transmission-sig")
):
    """
    Handles incoming PayPal Invoicing Webhooks with signature verification.
    Supported events:
    - INVOICING.INVOICE.PAID
    - INVOICING.INVOICE.CANCELLED
    - INVOICING.INVOICE.REFUNDED
    """
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Body is not a JSON object")
    except Exception:
        db.add(AuditLog(
            actor="webhook",
            action="WEBHOOK_REJECTED",
            details="invalid or missing JSON body"
        ))
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or missing JSON body")

    event_type = body.get("event_type")
    resource = body.get("resource", {}) if isinstance(body.get("resource"), dict) else {}
    paypal_id = resource.get("id")
    # Fallback: parse ID from href or self link if "id" not present
    if not paypal_id:
        href = resource.get("href")
        if not href and isinstance(resource.get("links"), list):
            for lnk in resource["links"]:
                if isinstance(lnk, dict) and lnk.get("rel") == "self" and lnk.get("href"):
                    href = lnk["href"]
                    break
        if href:
            segments = [s for s in urlparse(href).path.split("/") if s]
            if segments:
                paypal_id = segments[-1]

    logger.info(f"Received PayPal Webhook: event_type={event_type}, paypal_id={paypal_id}")

    is_verified = False
    reject_reason = None

    if settings.ALLOW_UNSIGNED_WEBHOOKS:
        logger.warning(
            "*** SECURITY WARNING: ALLOW_UNSIGNED_WEBHOOKS is enabled! "
            "Accepting PayPal webhook event without signature verification. "
            "Never enable this setting in production! ***"
        )
        is_verified = True
    else:
        has_all_headers = bool(
            paypal_transmission_id
            and paypal_transmission_time
            and paypal_cert_url
            and paypal_auth_algo
            and paypal_transmission_sig
        )
        if not has_all_headers:
            reject_reason = "missing signature headers"
        elif not settings.PAYPAL_WEBHOOK_ID:
            reject_reason = "webhook id not configured"
        elif not paypal_client.is_configured:
            reject_reason = "paypal client not configured"
        else:
            try:
                is_valid = await paypal_client.verify_webhook_signature(
                    transmission_id=paypal_transmission_id,
                    transmission_time=paypal_transmission_time,
                    cert_url=paypal_cert_url,
                    auth_algo=paypal_auth_algo,
                    transmission_sig=paypal_transmission_sig,
                    webhook_id=settings.PAYPAL_WEBHOOK_ID,
                    webhook_event=body
                )
                if is_valid:
                    is_verified = True
                else:
                    reject_reason = "verification failed"
            except Exception as e:
                logger.warning(f"Webhook signature check error: {e}")
                reject_reason = "verification failed"

    if not is_verified:
        logger.warning(f"PayPal Webhook rejected: {reject_reason}")
        db.add(AuditLog(
            actor="webhook",
            action="WEBHOOK_REJECTED",
            details=reject_reason or "verification failed",
            target_id=str(paypal_id) if paypal_id else None
        ))
        db.commit()
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {reject_reason or 'verification failed'}")

    # Locate invoice in DB
    invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == paypal_id).first() if paypal_id else None
    if not invoice:
        # Fallback check by invoice number (detail may be nested dict or missing)
        detail_dict = resource.get("detail") if isinstance(resource.get("detail"), dict) else {}
        inv_num = detail_dict.get("invoice_number")
        if inv_num:
            invoice = db.query(Invoice).filter(Invoice.invoice_number == inv_num).first()

    if not invoice:
        logger.warning(f"No matching invoice found for webhook paypal_id: {paypal_id}")
        return {"status": "ignored", "reason": "invoice_not_found"}

    # Back-fill paypal_invoice_id if DB record was matched by invoice_number
    if not invoice.paypal_invoice_id and paypal_id:
        invoice.paypal_invoice_id = paypal_id

    # Back-fill view_url from webhook resource if DB record is missing it
    if not invoice.view_url:
        detail_dict = resource.get("detail") if isinstance(resource.get("detail"), dict) else {}
        metadata = detail_dict.get("metadata") if isinstance(detail_dict.get("metadata"), dict) else {}
        recipient_view_url = metadata.get("recipient_view_url")
        if not recipient_view_url and isinstance(resource.get("links"), list):
            for lnk in resource["links"]:
                if isinstance(lnk, dict) and lnk.get("rel") == "payer-view" and lnk.get("href"):
                    recipient_view_url = lnk["href"]
                    break
        if recipient_view_url:
            invoice.view_url = recipient_view_url

    # Process events
    if event_type == "INVOICING.INVOICE.PAID":
        invoice.status = "PAID"
        # Try to read payment_date from payments.transactions in the resource
        paid_dt = None
        payments = resource.get("payments") if isinstance(resource.get("payments"), dict) else {}
        transactions = payments.get("transactions") if isinstance(payments.get("transactions"), list) else []
        if transactions and isinstance(transactions[-1], dict) and transactions[-1].get("payment_date"):
            try:
                paid_dt = datetime.datetime.fromisoformat(transactions[-1]["payment_date"].replace("Z", "+00:00"))
            except Exception:
                pass
        invoice.paid_at = paid_dt or datetime.datetime.utcnow()
        invoice.add_timeline_event("PAYMENT_RECEIVED", "Payment verified via PayPal Webhook")
        
        # Recalculate client score
        if invoice.client:
            client_invoices = [i.to_dict() for i in invoice.client.invoices]
            new_score, lag_days, stats = calculate_client_score(client_invoices)
            invoice.client.reliability_score = new_score
            invoice.client.predicted_pay_days = lag_days
            try:
                invoice.client.score_explanation = await generate_score_explanation(invoice.client.name, new_score, stats)
            except Exception:
                pass

        db.add(AuditLog(
            actor="webhook",
            action="INVOICE_PAID_WEBHOOK",
            details=f"Invoice {invoice.invoice_number} paid in full via PayPal Webhook",
            target_id=str(invoice.id)
        ))

    elif event_type == "INVOICING.INVOICE.CANCELLED":
        invoice.status = "CANCELLED"
        invoice.add_timeline_event("INVOICE_CANCELLED", "Cancelled via PayPal Webhook")
        db.add(AuditLog(
            actor="webhook",
            action="INVOICE_CANCELLED_WEBHOOK",
            details=f"Invoice {invoice.invoice_number} cancelled via PayPal Webhook",
            target_id=str(invoice.id)
        ))

    elif event_type == "INVOICING.INVOICE.REFUNDED":
        invoice.status = "REFUNDED"
        invoice.add_timeline_event("INVOICE_REFUNDED", "Refunded via PayPal Webhook")
        db.add(AuditLog(
            actor="webhook",
            action="INVOICE_REFUNDED_WEBHOOK",
            details=f"Invoice {invoice.invoice_number} refunded via PayPal Webhook",
            target_id=str(invoice.id)
        ))

    db.commit()
    return {"status": "success", "event": event_type, "invoice_id": invoice.id}
