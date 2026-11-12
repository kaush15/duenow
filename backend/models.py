import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from backend.database import Base

class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    company = Column(String(255), nullable=True)
    reliability_score = Column(Integer, default=100)
    score_explanation = Column(Text, nullable=True)
    predicted_pay_days = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="client", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "company": self.company or "",
            "reliability_score": self.reliability_score,
            "score_explanation": self.score_explanation or "Initial score. Client has no previous overdue history.",
            "predicted_pay_days": self.predicted_pay_days,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    paypal_invoice_id = Column(String(100), unique=True, nullable=True, index=True)
    invoice_number = Column(String(50), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    client_name = Column(String(255), nullable=False)
    client_email = Column(String(255), nullable=False)
    currency = Column(String(10), default="USD")
    amount = Column(Float, nullable=False, default=0.0)
    status = Column(String(50), default="DRAFT", index=True) # DRAFT, SENT, SCHEDULED, PAID, MARKED_AS_PAID, CANCELLED, REFUNDED
    due_date = Column(String(20), nullable=False) # YYYY-MM-DD
    issue_date = Column(String(20), nullable=False) # YYYY-MM-DD
    paid_at = Column(DateTime, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    view_url = Column(String(500), nullable=True)
    items_json = Column(Text, default="[]")
    note = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)
    timeline_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client", back_populates="invoices")
    queue_items = relationship("ApprovalQueueItem", back_populates="invoice", cascade="all, delete-orphan")

    def get_items(self):
        try:
            return json.loads(self.items_json) if self.items_json else []
        except Exception:
            return []

    def set_items(self, items):
        self.items_json = json.dumps(items)

    def get_timeline(self):
        try:
            return json.loads(self.timeline_json) if self.timeline_json else []
        except Exception:
            return []

    def add_timeline_event(self, event: str, details: str = ""):
        events = self.get_timeline()
        events.append({
            "event": event,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details
        })
        self.timeline_json = json.dumps(events)

    def to_dict(self):
        return {
            "id": self.id,
            "paypal_invoice_id": self.paypal_invoice_id,
            "invoice_number": self.invoice_number,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "client_email": self.client_email,
            "currency": self.currency,
            "amount": self.amount,
            "status": self.status,
            "due_date": self.due_date,
            "issue_date": self.issue_date,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "is_demo": bool(self.is_demo),
            "view_url": self.view_url,
            "items": self.get_items(),
            "note": self.note or "",
            "terms": self.terms or "",
            "timeline": self.get_timeline(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ApprovalQueueItem(Base):
    __tablename__ = "approval_queue"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    tone = Column(String(50), default="friendly") # friendly, firm, final
    language = Column(String(50), default="English")
    subject = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=False)
    status = Column(String(50), default="pending") # pending, approved, skipped, sent
    created_at = Column(DateTime, default=datetime.utcnow)
    actioned_at = Column(DateTime, nullable=True)

    invoice = relationship("Invoice", back_populates="queue_items")

    def to_dict(self):
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "invoice": self.invoice.to_dict() if self.invoice else None,
            "tone": self.tone,
            "language": self.language,
            "subject": self.subject,
            "message": self.message,
            "reasoning": self.reasoning,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "actioned_at": self.actioned_at.isoformat() if self.actioned_at else None,
        }

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor = Column(String(50), default="autopilot") # autopilot, user, webhook, system
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=False)
    target_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "actor": self.actor,
            "action": self.action,
            "details": self.details,
            "target_id": self.target_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
