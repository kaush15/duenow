import datetime
import json
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Invoice, Client, AuditLog
from backend.schemas import InvoiceCreateRequest, RemindRequest, RecordPaymentRequest
from backend.paypal_client import paypal_client, PayPalAPIError
from backend.autopilot import get_simulated_today, autopilot_agent
from backend.scoring import calculate_client_score, generate_score_explanation, compute_predicted_pay_date

logger = logging.getLogger("invoices_route")

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

def compute_effective_status(inv: Invoice, today: datetime.date) -> str:
    """Computes effective status considering simulated today."""
    if inv.status in ["PAID", "MARKED_AS_PAID", "CANCELLED", "REFUNDED", "DRAFT"]:
        return inv.status
    try:
        due_dt = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
        if today > due_dt:
            return "OVERDUE"
    except Exception:
        pass
    return inv.status

@router.get("")
def list_invoices(
    status: Optional[str] = None,
    search: Optional[str] = None,
    include_demo: bool = True,
    db: Session = Depends(get_db)
):
    """Lists all invoices with simulated status calculations."""
    today = get_simulated_today(db)
    query = db.query(Invoice).order_by(Invoice.created_at.desc())
    if not include_demo:
        query = query.filter(Invoice.is_demo == False)

    invoices = query.all()
    results = []

    for inv in invoices:
        effective_st = compute_effective_status(inv, today)
        if status and isinstance(status, str) and status.upper() != "ALL":
            if effective_st != status.upper() and inv.status != status.upper():
                continue

        if search and isinstance(search, str):
            s = search.lower()
            if s not in inv.invoice_number.lower() and s not in inv.client_name.lower() and s not in inv.client_email.lower():
                continue

        inv_dict = inv.to_dict()
        inv_dict["effective_status"] = effective_st
        
        # Calculate days until due or days overdue
        try:
            due_dt = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
            diff = (today - due_dt).days
            inv_dict["days_overdue"] = diff if diff > 0 else 0
            inv_dict["days_until_due"] = -diff if diff < 0 else 0
        except Exception:
            inv_dict["days_overdue"] = 0
            inv_dict["days_until_due"] = 0

        # Include client score & predicted pay date if available
        if inv.client:
            inv_dict["client_score"] = inv.client.reliability_score
            inv_dict["predicted_pay_date"] = compute_predicted_pay_date(inv.due_date, inv.client.predicted_pay_days)
        else:
            inv_dict["client_score"] = 70
            inv_dict["predicted_pay_date"] = inv.due_date

        results.append(inv_dict)

    return results

@router.get("/{invoice_id}")
def get_invoice_detail(invoice_id: int, db: Session = Depends(get_db)):
    """Retrieves full invoice details with timeline and PayPal links."""
    today = get_simulated_today(db)
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    res = inv.to_dict()
    res["effective_status"] = compute_effective_status(inv, today)
    if inv.client:
        res["client_score"] = inv.client.reliability_score
        res["client_score_explanation"] = inv.client.score_explanation
        res["predicted_pay_date"] = compute_predicted_pay_date(inv.due_date, inv.client.predicted_pay_days)
    else:
        res["client_score"] = 70
        res["client_score_explanation"] = "New client"
        res["predicted_pay_date"] = inv.due_date

    return res

@router.post("")
async def create_invoice(req: InvoiceCreateRequest, db: Session = Depends(get_db)):
    """
    Creates invoice and sends it via PayPal Sandbox.
    Calls POST /v2/invoicing/generate-next-invoice-number,
    POST /v2/invoicing/invoices, and POST /v2/invoicing/invoices/{id}/send.
    """
    today = get_simulated_today(db)
    issue_date = today.isoformat()

    # Compute total amount
    total_amount = sum(it.quantity * it.unit_price for it in req.items)

    # Check if PayPal is configured before attempting real invoice operations
    if not paypal_client.is_configured:
        raise HTTPException(status_code=503, detail="PayPal credentials are not configured")

    # 1. Generate Next Invoice Number from PayPal Sandbox
    invoice_number = await paypal_client.generate_next_invoice_number()

    # 2. Create Draft on PayPal Sandbox
    items_dicts = [it.model_dump() for it in req.items]
    try:
        paypal_resp = await paypal_client.create_draft_invoice(
            invoice_number=invoice_number,
            client_name=req.client_name,
            client_email=req.client_email,
            currency=req.currency,
            items=items_dicts,
            due_date=req.due_date,
            issue_date=issue_date,
            note=req.note,
            terms=req.terms
        )
        paypal_invoice_id = paypal_resp.get("id")
        view_url = paypal_resp.get("view_url")
    except PayPalAPIError as e:
        logger.error(f"PayPal Sandbox create draft failed ({e.status_code}): {e.message}")
        status_code = 400 if (e.status_code and 400 <= e.status_code < 500) else 502
        raise HTTPException(status_code=status_code, detail=f"PayPal error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error calling PayPal: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to communicate with PayPal: {str(e)}")

    # 3. If send_now is True, call Send endpoint
    status = "DRAFT"
    if req.send_now and paypal_invoice_id:
        try:
            await paypal_client.send_invoice(paypal_invoice_id, subject=f"Invoice {invoice_number} from DueNow Merchant")
            status = "SENT"
            # Re-fetch to get the payer view URL, which is only reliable after the invoice is sent
            try:
                refreshed = await paypal_client.get_invoice(paypal_invoice_id)
                if refreshed.get("view_url"):
                    view_url = refreshed.get("view_url")
            except Exception as e:
                logger.warning(f"Failed to refresh view_url after sending invoice {paypal_invoice_id}: {e}")
        except PayPalAPIError as e:
            logger.error(f"PayPal send call failed ({e.status_code}): {e.message}")
            status_code = 400 if (e.status_code and 400 <= e.status_code < 500) else 502
            raise HTTPException(status_code=status_code, detail=f"PayPal error: {e.message}")
        except Exception as e:
            logger.error(f"PayPal send call error: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to communicate with PayPal: {str(e)}")

    # Find or create Client once PayPal calls succeed
    client = db.query(Client).filter(Client.email == req.client_email).first()
    if not client:
        client = Client(
            name=req.client_name,
            email=req.client_email,
            company=req.client_name,
            reliability_score=70,
            score_explanation="Newly added client. No previous payment delays recorded.",
            predicted_pay_days=0
        )
        db.add(client)
        db.commit()
        db.refresh(client)

    # Create Invoice in DB
    invoice = Invoice(
        paypal_invoice_id=paypal_invoice_id,
        invoice_number=invoice_number,
        client_id=client.id,
        client_name=req.client_name,
        client_email=req.client_email,
        currency=req.currency,
        amount=total_amount,
        status=status,
        due_date=req.due_date,
        issue_date=issue_date,
        view_url=view_url,
        items_json=json.dumps(items_dicts),
        note=req.note,
        terms=req.terms or "Payment due upon specified date. Thank you!",
        timeline_json=json.dumps([
            {"event": "INVOICE_CREATED", "timestamp": datetime.datetime.utcnow().isoformat(), "details": f"Draft created (#{invoice_number})"},
            {"event": "INVOICE_SENT" if req.send_now else "DRAFT_SAVED", "timestamp": datetime.datetime.utcnow().isoformat(), "details": f"Dispatched to {req.client_email} via PayPal Sandbox" if req.send_now else "Saved as draft"}
        ])
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # Log to Audit Log
    db.add(AuditLog(
        actor="user",
        action="INVOICE_CREATED",
        details=f"Created {'and sent ' if req.send_now else ''}invoice #{invoice_number} for {req.client_name} ({total_amount:.2f} {req.currency})",
        target_id=str(invoice.id)
    ))
    db.commit()

    return invoice.to_dict()

@router.post("/{invoice_id}/send")
async def send_existing_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """POST /v2/invoicing/invoices/{id}/send on PayPal sandbox."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if inv.is_demo:
        inv.status = "SENT"
        inv.add_timeline_event("INVOICE_SENT", f"[Demo] Sent locally to {inv.client_email}")
        db.add(AuditLog(
            actor="user",
            action="INVOICE_SENT",
            details=f"[Demo] Sent invoice {inv.invoice_number} to {inv.client_email}",
            target_id=str(inv.id)
        ))
        db.commit()
        return inv.to_dict()

    if not paypal_client.is_configured:
        raise HTTPException(status_code=503, detail="PayPal credentials are not configured")

    if not inv.paypal_invoice_id:
        raise HTTPException(status_code=400, detail="Invoice is missing PayPal invoice ID")

    try:
        await paypal_client.send_invoice(inv.paypal_invoice_id, subject=f"Invoice {inv.invoice_number}")
    except PayPalAPIError as e:
        logger.error(f"PayPal send invoice failed: {e.message}")
        status_code = 400 if (e.status_code and 400 <= e.status_code < 500) else 502
        raise HTTPException(status_code=status_code, detail=f"PayPal error: {e.message}")
    except Exception as e:
        logger.error(f"PayPal send call error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to communicate with PayPal: {str(e)}")

    # Re-fetch to refresh view_url in DB (payer link is only reliable once invoice is sent)
    try:
        refreshed = await paypal_client.get_invoice(inv.paypal_invoice_id)
        if refreshed.get("view_url"):
            inv.view_url = refreshed.get("view_url")
    except Exception as e:
        logger.warning(f"Failed to refresh view_url for invoice {inv.paypal_invoice_id} after send: {e}")

    inv.status = "SENT"
    inv.add_timeline_event("INVOICE_SENT", f"Sent to {inv.client_email} via PayPal Sandbox")
    db.add(AuditLog(
        actor="user",
        action="INVOICE_SENT",
        details=f"Sent invoice {inv.invoice_number} to {inv.client_email}",
        target_id=str(inv.id)
    ))
    db.commit()
    return inv.to_dict()

@router.post("/{invoice_id}/remind")
async def remind_invoice(invoice_id: int, req: RemindRequest, db: Session = Depends(get_db)):
    """POST /v2/invoicing/invoices/{id}/remind with AI-written subject and note."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    try:
        return await autopilot_agent.send_reminder_action(
            db,
            invoice_id=invoice_id,
            subject=req.subject,
            note=req.note,
            tone=req.tone or "friendly",
            language=req.language or "English",
            actor="user"
        )
    except PayPalAPIError as e:
        logger.error(f"PayPal reminder failed: {e.message}")
        status_code = 400 if (e.status_code and 400 <= e.status_code < 500) else 502
        raise HTTPException(status_code=status_code, detail=f"PayPal error: {e.message}")
    except Exception as e:
        msg = str(e)
        status_code = 503 if "not configured" in msg else 502
        raise HTTPException(status_code=status_code, detail=msg)

@router.post("/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: int, reason: Optional[str] = "Cancelled by merchant", db: Session = Depends(get_db)):
    """POST /v2/invoicing/invoices/{id}/cancel on PayPal sandbox."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if inv.is_demo:
        inv.status = "CANCELLED"
        inv.add_timeline_event("INVOICE_CANCELLED", f"[Demo] Cancelled with reason: {reason}")
        db.add(AuditLog(
            actor="user",
            action="INVOICE_CANCELLED",
            details=f"[Demo] Cancelled invoice {inv.invoice_number}. Reason: {reason}",
            target_id=str(inv.id)
        ))
        db.commit()
        return inv.to_dict()

    if not paypal_client.is_configured:
        raise HTTPException(status_code=503, detail="PayPal credentials are not configured")

    if inv.paypal_invoice_id:
        try:
            await paypal_client.cancel_invoice(inv.paypal_invoice_id, reason=reason)
        except PayPalAPIError as e:
            logger.error(f"PayPal cancel failed: {e.message}")
            status_code = 400 if (e.status_code and 400 <= e.status_code < 500) else 502
            raise HTTPException(status_code=status_code, detail=f"PayPal error: {e.message}")
        except Exception as e:
            logger.error(f"PayPal cancel call error: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to communicate with PayPal: {str(e)}")

    inv.status = "CANCELLED"
    inv.add_timeline_event("INVOICE_CANCELLED", f"Cancelled with reason: {reason}")
    db.add(AuditLog(
        actor="user",
        action="INVOICE_CANCELLED",
        details=f"Cancelled invoice {inv.invoice_number}. Reason: {reason}",
        target_id=str(inv.id)
    ))
    db.commit()
    return inv.to_dict()

@router.post("/{invoice_id}/record-payment")
async def record_payment(invoice_id: int, req: RecordPaymentRequest, db: Session = Depends(get_db)):
    """POST /v2/invoicing/invoices/{id}/payments fallback for demos."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    amt = req.amount if req.amount is not None else inv.amount
    payment_date = req.date or get_simulated_today(db).isoformat()

    if not inv.is_demo and inv.paypal_invoice_id and paypal_client.is_configured:
        try:
            await paypal_client.record_payment(
                inv.paypal_invoice_id,
                amount=amt,
                currency=inv.currency,
                payment_date=payment_date,
                method=req.method or "OTHER",
                note=req.note or "Recorded payment"
            )
        except Exception as e:
            logger.warning(f"PayPal record payment call: {e}")

    inv.status = "PAID"
    inv.paid_at = datetime.datetime.utcnow()
    inv.add_timeline_event("PAYMENT_RECEIVED", f"Payment of {amt:.2f} {inv.currency} recorded via PayPal Sandbox")
    
    # Recalculate client reliability score
    if inv.client:
        client_invoices = [i.to_dict() for i in inv.client.invoices]
        new_score, lag_days, stats = calculate_client_score(client_invoices)
        inv.client.reliability_score = new_score
        inv.client.predicted_pay_days = lag_days
        try:
            inv.client.score_explanation = await generate_score_explanation(inv.client.name, new_score, stats)
        except Exception:
            pass

    db.add(AuditLog(
        actor="user",
        action="PAYMENT_RECORDED",
        details=f"Recorded payment of {amt:.2f} {inv.currency} for invoice {inv.invoice_number}",
        target_id=str(inv.id)
    ))
    db.commit()
    return inv.to_dict()

def compute_chart_data(invoices: List[Invoice], today: datetime.date, include_demo: bool) -> List[Dict[str, Any]]:
    """Generates 6-month collected vs expected chart data."""
    if include_demo:
        return [
            {"period": "Jul 2026", "collected": 2400, "projected": 2400},
            {"period": "Aug 2026", "collected": 4250, "projected": 4250},
            {"period": "Sep 2026", "collected": 3200, "projected": 3200},
            {"period": "Oct 2026", "collected": 5850, "projected": 5850},
            {"period": "Nov 2026", "collected": 1600, "projected": 7550},
            {"period": "Dec 2026", "collected": 0, "projected": 4800}
        ]

    # Dynamically compute chart data for real invoices only
    periods = []
    for offset in range(-4, 2):
        m = today.month + offset
        y = today.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        period_label = datetime.date(y, m, 1).strftime("%b %Y")

        col = 0.0
        exp = 0.0
        for inv in invoices:
            if inv.is_demo:
                continue
            if inv.status in ["PAID", "MARKED_AS_PAID"] and inv.paid_at:
                p_dt = inv.paid_at.date() if isinstance(inv.paid_at, datetime.datetime) else datetime.datetime.fromisoformat(str(inv.paid_at)[:10]).date()
                if p_dt.year == y and p_dt.month == m:
                    col += inv.amount
            elif inv.status in ["SENT", "SCHEDULED"]:
                try:
                    d_dt = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
                    if d_dt.year == y and d_dt.month == m:
                        exp += inv.amount
                except Exception:
                    pass

        is_past = (y < today.year) or (y == today.year and m < today.month)
        proj = col if is_past else (col + exp)
        periods.append({"period": period_label, "collected": round(col, 2), "projected": round(proj, 2)})

    return periods

@router.get("/dashboard/stats")
async def get_dashboard_stats(include_demo: bool = True, db: Session = Depends(get_db)):
    """KPI cards, cashflow projection chart, and AI Cashflow insight card."""
    today = get_simulated_today(db)
    query = db.query(Invoice)
    if not include_demo:
        query = query.filter(Invoice.is_demo == False)
    invoices = query.all()

    total_demo_count = db.query(Invoice).filter(Invoice.is_demo == True).count()
    has_demo_data = total_demo_count > 0
    demo_included = bool(include_demo and has_demo_data)

    total_outstanding = 0.0
    total_collected = 0.0
    total_overdue = 0.0
    days_to_pay_list = []
    overdue_invoices = []

    for inv in invoices:
        effective_st = compute_effective_status(inv, today)
        if inv.status in ["PAID", "MARKED_AS_PAID"]:
            total_collected += inv.amount
            if inv.issue_date and inv.paid_at:
                try:
                    iss = datetime.datetime.strptime(inv.issue_date[:10], "%Y-%m-%d").date()
                    paid_d = inv.paid_at.date() if isinstance(inv.paid_at, datetime.datetime) else datetime.datetime.fromisoformat(str(inv.paid_at)[:10]).date()
                    days_to_pay_list.append(max(0, (paid_d - iss).days))
                except Exception:
                    pass
        elif inv.status in ["SENT", "SCHEDULED"]:
            total_outstanding += inv.amount
            try:
                due_d = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
                if today > due_d:
                    diff = (today - due_d).days
                    total_overdue += inv.amount
                    inv_dict = inv.to_dict()
                    inv_dict["effective_status"] = "OVERDUE"
                    inv_dict["days_overdue"] = diff
                    inv_dict["days_until_due"] = 0
                    overdue_invoices.append(inv_dict)
            except Exception:
                pass

    avg_days_to_pay = round(sum(days_to_pay_list) / len(days_to_pay_list), 1) if days_to_pay_list else 12.0

    # Sort overdue invoices: most overdue first
    overdue_invoices.sort(key=lambda x: x.get("days_overdue", 0), reverse=True)

    # Chart monthly aggregations
    chart_data = compute_chart_data(invoices, today, include_demo=include_demo)

    # Generate AI cashflow insight
    insight = await autopilot_agent.generate_cashflow_insight(db, include_demo=include_demo)

    return {
        "total_outstanding": total_outstanding,
        "total_collected": total_collected,
        "total_overdue": total_overdue,
        "avg_days_to_pay": avg_days_to_pay,
        "simulated_date": today.isoformat(),
        "chart_data": chart_data,
        "cashflow_insight": insight,
        "overdue_invoices": overdue_invoices,
        "has_demo_data": has_demo_data,
        "demo_included": demo_included
    }
