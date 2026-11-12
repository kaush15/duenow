from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, EmailStr

class LineItem(BaseModel):
    name: str = Field(..., description="Name or title of the service or item")
    quantity: float = Field(default=1.0, description="Quantity or hours")
    unit_price: float = Field(..., description="Unit price or hourly rate")
    description: Optional[str] = Field(default="", description="Detailed description of the item")

class InvoiceDraft(BaseModel):
    client_name: str = Field(..., description="Name of the client or company")
    client_email: str = Field(..., description="Email address of the client")
    currency: str = Field(default="USD", description="ISO currency code, e.g. USD, EUR, GBP")
    items: List[LineItem] = Field(default_factory=list, description="List of line items")
    due_date: str = Field(..., description="Due date in YYYY-MM-DD format")
    note: Optional[str] = Field(default="", description="Friendly note to the client")
    terms: Optional[str] = Field(default="Payment due by due date. Thank you for your business!", description="Terms and conditions")

class SmartCreatePromptRequest(BaseModel):
    prompt: str = Field(..., description="Natural language description of the job/invoice")
    currency: Optional[str] = Field(default="USD", description="Preferred currency code")

class SmartCreateResult(BaseModel):
    is_clarification_needed: bool = Field(default=False, description="True if prompt lacks critical information")
    clarification_question: Optional[str] = Field(default=None, description="One short question to clarify ambiguous details")
    invoice_draft: Optional[InvoiceDraft] = Field(default=None, description="Structured invoice draft if prompt is sufficient")

class InvoiceCreateRequest(BaseModel):
    client_name: str
    client_email: str
    currency: str = "USD"
    items: List[LineItem]
    due_date: str
    note: Optional[str] = ""
    terms: Optional[str] = ""
    send_now: bool = True

class RemindRequest(BaseModel):
    subject: str
    note: str
    tone: Optional[str] = "friendly"
    language: Optional[str] = "English"

class RecordPaymentRequest(BaseModel):
    amount: Optional[float] = None
    date: Optional[str] = None
    method: Optional[str] = "OTHER"
    note: Optional[str] = "Demo manual payment recording"

class ApprovalActionRequest(BaseModel):
    action: str = Field(..., description="'approve', 'skip', or 'edit'")
    subject: Optional[str] = None
    message: Optional[str] = None

class FastForwardRequest(BaseModel):
    days: int = Field(..., description="Number of days to advance simulated time")

class GenerateReminderDraftRequest(BaseModel):
    invoice_id: int
    tone: str = "friendly"
    language: str = "English"

class CashflowInsightResponse(BaseModel):
    summary: str
    projected_30d: float
    overdue_amount: float
    confidence_level: str
