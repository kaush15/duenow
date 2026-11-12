import datetime
import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.models import Invoice, Client, ApprovalQueueItem, AuditLog, AppSetting
from backend.paypal_client import paypal_client
from backend.llm_adapter import llm_adapter

logger = logging.getLogger("autopilot")

class ReminderDraftSchema(BaseModel):
    subject: str = Field(..., description="Subject line of the reminder email")
    message: str = Field(..., description="Polite yet effective message body for the reminder")
    reasoning: str = Field(..., description="Clear 1-sentence reasoning explaining why this tone and timing were chosen")

class CashflowSummarySchema(BaseModel):
    summary: str = Field(..., description="A 2-3 sentence financial projection summary for the next 30 days")
    projected_30d: float = Field(..., description="Estimated collected amount in next 30 days")
    confidence_level: str = Field(..., description="'High', 'Medium', or 'Moderate'")

def get_simulated_today(db: Session) -> datetime.date:
    """Returns today's date adjusted by simulated_days_offset."""
    setting = db.query(AppSetting).filter(AppSetting.key == "simulated_days_offset").first()
    offset = int(setting.value) if setting and setting.value.isdigit() else 0
    return datetime.date.today() + datetime.timedelta(days=offset)

def get_app_setting(db: Session, key: str, default: str = "") -> str:
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    return setting.value if setting else default

def set_app_setting(db: Session, key: str, value: str):
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not setting:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()

class AutopilotAgent:
    """Autonomous Collections Agent with structured tool actions."""

    @staticmethod
    def get_invoice_details(db: Session, invoice_id: int) -> Optional[Invoice]:
        return db.query(Invoice).filter(Invoice.id == invoice_id).first()

    @staticmethod
    def get_client_history(db: Session, client_id: int) -> Dict[str, Any]:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return {}
        invoices = db.query(Invoice).filter(Invoice.client_id == client_id).all()
        return {
            "client": client.to_dict(),
            "invoices": [inv.to_dict() for inv in invoices]
        }

    @staticmethod
    async def draft_reminder_message(
        invoice: Invoice,
        client: Optional[Client],
        days_overdue: int,
        tone: str = "friendly",
        language: str = "English"
    ) -> ReminderDraftSchema:
        """LLM tool: drafts custom reminder and provides transparent reasoning."""
        client_name = client.name if client else invoice.client_name
        score = client.reliability_score if client else 70
        invoice_num = invoice.invoice_number
        amount = f"{invoice.amount:.2f} {invoice.currency}"
        due_date = invoice.due_date

        timing_status = (
            f"Overdue by {days_overdue} days" if days_overdue > 0
            else f"Due today" if days_overdue == 0
            else f"Due in {abs(days_overdue)} days (upcoming)"
        )

        prompt = (
            f"Draft a payment reminder for client '{client_name}'.\n"
            f"Invoice Number: {invoice_num}\n"
            f"Amount Due: {amount}\n"
            f"Due Date: {due_date}\n"
            f"Current Timing: {timing_status}\n"
            f"Client Reliability Score: {score}/100\n"
            f"Desired Tone: {tone.upper()} (Options: friendly, firm, final)\n"
            f"Language: {language}\n\n"
            f"Guidelines:\n"
            f"- If friendly: warm, helpful, gentle nudge, assuming they just overlooked it.\n"
            f"- If firm: direct, clear, emphasizing the missed due date and urgency of settling account.\n"
            f"- If final: serious, urgent, stating immediate resolution required.\n"
            f"- Provide a 1-sentence reasoning explaining why this tone and timing were selected.\n"
            f"- Keep the message under 100 words."
        )
        system = "You are DueNow Autopilot, an intelligent collections agent for freelancers and small businesses. Return strictly structured JSON."

        try:
            return await llm_adapter.generate_structured(prompt, ReminderDraftSchema, system_instruction=system)
        except Exception as e:
            logger.warning(f"LLM reminder draft failed: {e}. Using deterministic fallback.")
            # Fallback message
            if tone == "friendly":
                subj = f"Friendly reminder: Invoice {invoice_num} for {amount}"
                msg = f"Hi {client_name}, just a gentle reminder that invoice {invoice_num} ({amount}) was due on {due_date}. Please let us know if you need any assistance with payment."
                reas = f"{timing_status}, client reliability score is {score}/100, tone: friendly."
            elif tone == "firm":
                subj = f"Payment past due: Invoice {invoice_num} ({amount})"
                msg = f"Dear {client_name}, invoice {invoice_num} for {amount} is now {days_overdue} days overdue. Please arrange payment as soon as possible to keep your account current."
                reas = f"Overdue {days_overdue} days, follow-up required, tone: firm."
            else:
                subj = f"Final notice: Overdue Invoice {invoice_num}"
                msg = f"Dear {client_name}, this is an urgent notice regarding overdue invoice {invoice_num} for {amount}. Please remit payment immediately."
                reas = f"Significantly overdue ({days_overdue} days), escalation needed, tone: final."
            return ReminderDraftSchema(subject=subj, message=msg, reasoning=reas)

    @staticmethod
    async def send_reminder_action(
        db: Session,
        invoice_id: int,
        subject: str,
        note: str,
        tone: str = "friendly",
        language: str = "English",
        actor: str = "user"
    ) -> Dict[str, Any]:
        """Tool: sends reminder via PayPal sandbox API and updates audit logs."""
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise Exception("Invoice not found")

        # Call PayPal remind endpoint if PayPal invoice ID exists and invoice is not a demo invoice
        paypal_result = None
        if not getattr(invoice, "is_demo", False) and invoice.paypal_invoice_id:
            if not paypal_client.is_configured:
                raise Exception("PayPal credentials are not configured")
            paypal_result = await paypal_client.remind_invoice(invoice.paypal_invoice_id, subject, note)

        # Update invoice timeline
        prefix = "[Demo] " if getattr(invoice, "is_demo", False) else ""
        invoice.add_timeline_event(
            "REMINDER_SENT",
            f"{prefix}Reminder sent ({tone.upper()} tone, {language}). Subject: {subject}"
        )

        # Create audit log
        log = AuditLog(
            actor=actor,
            action="SEND_REMINDER",
            details=f"{prefix}Sent {tone} reminder for invoice {invoice.invoice_number} ({invoice.client_name}). Subject: {subject}",
            target_id=str(invoice.id)
        )
        db.add(log)
        db.commit()

        return {
            "status": "success",
            "invoice_id": invoice.id,
            "subject": subject,
            "paypal_response": paypal_result
        }

    @staticmethod
    async def run_autopilot_cycle(db: Session) -> Dict[str, Any]:
        """
        Executes one full Autopilot evaluation cycle:
        1. Checks open invoices against simulated today.
        2. Discovers overdue / soon-due invoices needing follow-up.
        3. Drafts reminders with LLM.
        4. Auto-approves if rule matches, otherwise queues for user approval.
        """
        today = get_simulated_today(db)
        auto_approve_friendly = get_app_setting(db, "auto_approve_friendly", "false").lower() == "true"
        preferred_lang = get_app_setting(db, "preferred_language", "English")

        # Open invoices
        open_invoices = db.query(Invoice).filter(
            Invoice.status.in_(["SENT", "SCHEDULED"])
        ).all()

        actions_taken = []
        new_proposals = 0
        auto_approved = 0

        for inv in open_invoices:
            try:
                due_dt = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
                days_overdue = (today - due_dt).days
            except Exception:
                continue

            # Check if invoice is due in <= 2 days or already overdue
            if days_overdue < -2:
                continue # not yet ready for reminder

            # Check if there is already an active pending item in queue for this invoice
            existing_pending = db.query(ApprovalQueueItem).filter(
                ApprovalQueueItem.invoice_id == inv.id,
                ApprovalQueueItem.status == "pending"
            ).first()

            if existing_pending:
                continue # Already in queue waiting for user

            # Check if reminder was sent recently (within last 3 simulated days)
            recent_sent = False
            for ev in inv.get_timeline():
                if ev.get("event") == "REMINDER_SENT":
                    ts_str = ev.get("timestamp")
                    if ts_str:
                        try:
                            ev_dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
                            if (today - ev_dt).days < 3:
                                recent_sent = True
                                break
                        except Exception:
                            pass
            if recent_sent:
                continue

            # Determine appropriate tone
            if days_overdue <= 4:
                tone = "friendly"
            elif days_overdue <= 14:
                tone = "firm"
            else:
                tone = "final"

            client = inv.client
            draft = await AutopilotAgent.draft_reminder_message(
                inv, client, days_overdue, tone=tone, language=preferred_lang
            )

            # Check auto-approve condition
            if auto_approve_friendly and tone == "friendly":
                # Auto-approve & send directly
                await AutopilotAgent.send_reminder_action(
                    db, inv.id, draft.subject, draft.message, tone=tone, language=preferred_lang, actor="autopilot"
                )
                auto_approved += 1
                actions_taken.append({
                    "invoice_number": inv.invoice_number,
                    "action": "AUTO_APPROVED_AND_SENT",
                    "tone": tone
                })
            else:
                # Add to Approval Queue
                queue_item = ApprovalQueueItem(
                    invoice_id=inv.id,
                    tone=tone,
                    language=preferred_lang,
                    subject=draft.subject,
                    message=draft.message,
                    reasoning=draft.reasoning,
                    status="pending"
                )
                db.add(queue_item)
                db.commit()

                log = AuditLog(
                    actor="autopilot",
                    action="QUEUED_PROPOSAL",
                    details=f"Proposed {tone} reminder for invoice {inv.invoice_number} ({inv.client_name}). Reasoning: {draft.reasoning}",
                    target_id=str(inv.id)
                )
                db.add(log)
                db.commit()

                new_proposals += 1
                actions_taken.append({
                    "invoice_number": inv.invoice_number,
                    "action": "QUEUED_FOR_APPROVAL",
                    "tone": tone,
                    "queue_id": queue_item.id
                })

        return {
            "evaluated_invoices": len(open_invoices),
            "new_proposals": new_proposals,
            "auto_approved": auto_approved,
            "actions": actions_taken,
            "simulated_date": today.isoformat()
        }

    @staticmethod
    async def generate_cashflow_insight(db: Session, include_demo: bool = True) -> Dict[str, Any]:
        """Generates AI 2-3 sentence financial projection summary for the next 30 days."""
        today = get_simulated_today(db)
        query = db.query(Invoice).filter(Invoice.status.in_(["SENT", "SCHEDULED"]))
        if not include_demo:
            query = query.filter(Invoice.is_demo == False)
        open_invoices = query.all()
        
        total_open = sum(inv.amount for inv in open_invoices)
        overdue_invoices = []
        upcoming_invoices = []

        for inv in open_invoices:
            try:
                due_dt = datetime.datetime.strptime(inv.due_date[:10], "%Y-%m-%d").date()
                if today > due_dt:
                    overdue_invoices.append(inv)
                else:
                    upcoming_invoices.append(inv)
            except Exception:
                upcoming_invoices.append(inv)

        overdue_amt = sum(inv.amount for inv in overdue_invoices)
        upcoming_amt = sum(inv.amount for inv in upcoming_invoices)

        if not open_invoices:
            return {
                "summary": "No open or overdue receivables. All invoices are currently settled.",
                "projected_30d": 0.0,
                "overdue_amount": 0.0,
                "confidence_level": "High"
            }

        prompt = (
            f"Simulated Today's Date: {today.isoformat()}\n"
            f"Total Open Invoices: {len(open_invoices)} totaling ${total_open:,.2f}\n"
            f"Overdue Amount: ${overdue_amt:,.2f} across {len(overdue_invoices)} invoices\n"
            f"Upcoming Amount (next 30 days): ${upcoming_amt:,.2f}\n\n"
            f"Generate a 2-3 sentence financial insight card for a freelancer. "
            f"Provide projected collections for next 30 days and confidence level (High, Medium, Moderate)."
        )
        system = "You are DueNow's Financial Intelligence Assistant. Return concise, actionable JSON."

        try:
            res = await llm_adapter.generate_structured(prompt, CashflowSummarySchema, system_instruction=system)
            return {
                "summary": res.summary,
                "projected_30d": res.projected_30d,
                "overdue_amount": overdue_amt,
                "confidence_level": res.confidence_level
            }
        except Exception as e:
            logger.warning(f"Cashflow LLM insight failed: {e}. Fallback to standard projection.")
            est = upcoming_amt + (overdue_amt * 0.75)
            return {
                "summary": f"Based on current invoices and client payment reliability, you can expect approximately ${est:,.2f} to be collected over the next 30 days. Priority follow-ups on ${overdue_amt:,.2f} overdue balances will maximize your cash flow.",
                "projected_30d": est,
                "overdue_amount": overdue_amt,
                "confidence_level": "High"
            }

autopilot_agent = AutopilotAgent()
