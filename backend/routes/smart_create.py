import datetime
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models import Client, Invoice
from backend.schemas import SmartCreatePromptRequest, SmartCreateResult, InvoiceDraft, LineItem
from backend.llm_adapter import llm_adapter
from backend.autopilot import get_simulated_today

logger = logging.getLogger("smart_create")

router = APIRouter(prefix="/api/smart-create", tags=["smart-create"])

class LLMClarificationOrDraft(BaseModel):
    is_clarification_needed: bool = Field(
        ...,
        description="True if critical information is missing (e.g., completely missing price or total, or missing client info), requiring asking the user ONE short question."
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="If is_clarification_needed is true, ask exactly ONE short, friendly question to get the missing detail."
    )
    client_name: Optional[str] = Field(default=None, description="Extracted client name or company")
    client_email: Optional[str] = Field(default=None, description="Client email address if mentioned, or suggested placeholder like client@example.com")
    items: Optional[list[LineItem]] = Field(default=None, description="List of parsed items with quantity and unit_price")
    due_days: Optional[int] = Field(default=14, description="Due date offset in days from today (default 14)")
    note: Optional[str] = Field(default="", description="Personalized thank-you or project note")
    terms: Optional[str] = Field(default="Payment due on specified date.", description="Standard payment terms")

@router.post("/parse", response_model=SmartCreateResult)
async def parse_smart_create(req: SmartCreatePromptRequest, db: Session = Depends(get_db)):
    """
    Parses natural language job description into structured invoice fields.
    If ambiguous or missing critical info, asks ONE short clarifying question.
    """
    today = get_simulated_today(db)
    prompt = (
        f"Today's Date: {today.isoformat()}\n"
        f"Requested Currency: {req.currency or 'USD'}\n"
        f"Freelancer Job Description: \"{req.prompt}\"\n\n"
        f"Instructions:\n"
        f"1. Extract the client name, client email (or infer plausible placeholder if client name is known), line items (name, quantity, unit_price, description), due days offset, and a professional note.\n"
        f"2. If the user provided NO pricing/rate at all, set is_clarification_needed=true and write ONE short clarifying question asking for the rate/price.\n"
        f"3. Otherwise, set is_clarification_needed=false and provide complete line items and draft fields.\n"
        f"4. If multiple items or rush fees/revisions are specified, parse them into distinct line items."
    )
    system = "You are DueNow's Smart Invoice Extraction AI. Convert informal freelancer job descriptions into clean, professional PayPal invoice drafts."

    try:
        res = await llm_adapter.generate_structured(prompt, LLMClarificationOrDraft, system_instruction=system)
        
        if res.is_clarification_needed and res.clarification_question:
            return SmartCreateResult(
                is_clarification_needed=True,
                clarification_question=res.clarification_question,
                invoice_draft=None
            )

        # Build clean invoice draft
        client_name = res.client_name or "Client"
        client_email = res.client_email or f"billing@{client_name.lower().replace(' ', '')}.com"
        due_offset = res.due_days if res.due_days is not None and res.due_days >= 0 else 14
        due_date = (today + datetime.timedelta(days=due_offset)).isoformat()

        items = res.items or [LineItem(name="Professional Services", quantity=1.0, unit_price=100.0, description="Completed deliverables")]

        draft = InvoiceDraft(
            client_name=client_name,
            client_email=client_email,
            currency=req.currency or "USD",
            items=items,
            due_date=due_date,
            note=res.note or "Thank you for your business!",
            terms=res.terms or "Payment due on date specified. Thank you!"
        )

        return SmartCreateResult(
            is_clarification_needed=False,
            clarification_question=None,
            invoice_draft=draft
        )

    except Exception as e:
        logger.error(f"Error in smart create parse: {e}")
        # Rule-based fallback if LLM is unavailable
        return SmartCreateResult(
            is_clarification_needed=False,
            clarification_question=None,
            invoice_draft=InvoiceDraft(
                client_name="Acme Client",
                client_email="client@example.com",
                currency=req.currency or "USD",
                items=[LineItem(name=req.prompt[:50] or "Design & Consulting", quantity=1.0, unit_price=250.0, description=req.prompt)],
                due_date=(today + datetime.timedelta(days=14)).isoformat(),
                note="Thank you for your business!",
                terms="Payment due in 14 days."
            )
        )

@router.get("/client-warning")
async def check_client_warning(client_name: str, db: Session = Depends(get_db)):
    """Checks if client has a low reliability score to warn freelancer before invoicing."""
    client = db.query(Client).filter(Client.name.ilike(f"%{client_name}%")).first()
    if not client:
        return {"has_warning": False, "score": 70, "message": "New client - no previous late payment record."}
    
    if client.reliability_score < 60:
        return {
            "has_warning": True,
            "score": client.reliability_score,
            "message": f"Caution: {client.name} has a low reliability score ({client.reliability_score}/100) and an average payment delay of {client.predicted_pay_days} days. Consider requesting an upfront deposit or shorter payment terms."
        }
    
    return {
        "has_warning": False,
        "score": client.reliability_score,
        "message": f"{client.name} has a strong reliability score of {client.reliability_score}/100."
    }
