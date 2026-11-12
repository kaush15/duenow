import datetime
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ApprovalQueueItem, Invoice, AuditLog, AppSetting
from backend.schemas import ApprovalActionRequest, GenerateReminderDraftRequest
from backend.autopilot import autopilot_agent, get_app_setting, set_app_setting

logger = logging.getLogger("autopilot_route")

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])

@router.get("/queue")
def get_approval_queue(db: Session = Depends(get_db)):
    """Returns all pending proposals in the Autopilot approval queue."""
    items = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "pending").order_by(ApprovalQueueItem.created_at.desc()).all()
    results = []
    for item in items:
        it_dict = item.to_dict()
        results.append(it_dict)
    return results

@router.post("/run")
async def run_autopilot(db: Session = Depends(get_db)):
    """Triggers the Autopilot agent evaluation cycle immediately."""
    result = await autopilot_agent.run_autopilot_cycle(db)
    return result

@router.post("/action/{queue_id}")
async def handle_approval_action(queue_id: int, req: ApprovalActionRequest, db: Session = Depends(get_db)):
    """
    User action on a queued proposal:
    - 'approve': dispatches reminder via PayPal Sandbox
    - 'edit': updates text and dispatches
    - 'skip': archives the proposal without sending
    """
    item = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.id == queue_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    invoice = item.invoice
    if not invoice:
        raise HTTPException(status_code=404, detail="Associated invoice not found")

    action = req.action.lower()
    subject = req.subject or item.subject
    message = req.message or item.message

    if action in ["approve", "edit"]:
        # Execute reminder tool
        await autopilot_agent.send_reminder_action(
            db=db,
            invoice_id=invoice.id,
            subject=subject,
            note=message,
            tone=item.tone,
            language=item.language,
            actor="user"
        )
        item.status = "approved"
        item.subject = subject
        item.message = message
        item.actioned_at = datetime.datetime.utcnow()
        db.commit()

        return {
            "status": "success",
            "action": "approved",
            "message": f"Reminder for {invoice.invoice_number} successfully dispatched via PayPal Sandbox."
        }

    elif action == "skip":
        item.status = "skipped"
        item.actioned_at = datetime.datetime.utcnow()
        db.add(AuditLog(
            actor="user",
            action="QUEUE_PROPOSAL_SKIPPED",
            details=f"User skipped reminder proposal for invoice {invoice.invoice_number} ({invoice.client_name}).",
            target_id=str(invoice.id)
        ))
        db.commit()
        return {"status": "success", "action": "skipped"}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

@router.post("/draft")
async def generate_reminder_draft(req: GenerateReminderDraftRequest, db: Session = Depends(get_db)):
    """Generates an on-demand reminder draft for any invoice given desired tone and language."""
    invoice = db.query(Invoice).filter(Invoice.id == req.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    today = datetime.date.today()
    try:
        due_dt = datetime.datetime.strptime(invoice.due_date[:10], "%Y-%m-%d").date()
        days_overdue = (today - due_dt).days
    except Exception:
        days_overdue = 0

    client = invoice.client
    draft = await autopilot_agent.draft_reminder_message(
        invoice=invoice,
        client=client,
        days_overdue=days_overdue,
        tone=req.tone,
        language=req.language
    )
    return draft.model_dump()

@router.get("/logs")
def get_audit_logs(limit: int = 50, db: Session = Depends(get_db)):
    """Chronological Audit Log of all actions taken by Autopilot and User."""
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [log.to_dict() for log in logs]
