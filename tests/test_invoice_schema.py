import pytest
from pydantic import ValidationError
from backend.schemas import LineItem, InvoiceDraft, SmartCreateResult, InvoiceCreateRequest

def test_line_item_valid():
    item = LineItem(name="Logo Design", quantity=3.0, unit_price=200.0, description="3 revisions")
    assert item.name == "Logo Design"
    assert item.quantity == 3.0
    assert item.unit_price == 200.0
    assert item.quantity * item.unit_price == 600.0

def test_line_item_missing_required():
    with pytest.raises(ValidationError):
        LineItem(quantity=1.0) # missing name and unit_price

def test_invoice_draft_structure():
    items = [
        LineItem(name="Logo design", quantity=3, unit_price=200.0),
        LineItem(name="Rush fee", quantity=1, unit_price=150.0)
    ]
    draft = InvoiceDraft(
        client_name="Acme Co",
        client_email="sam@acme.com",
        currency="USD",
        items=items,
        due_date="2026-11-12",
        note="Rush logo delivery"
    )
    assert draft.client_name == "Acme Co"
    assert len(draft.items) == 2
    total = sum(i.quantity * i.unit_price for i in draft.items)
    assert total == 750.0

def test_smart_create_clarification_result():
    res = SmartCreateResult(
        is_clarification_needed=True,
        clarification_question="What is your hourly rate or fixed price for this project?",
        invoice_draft=None
    )
    assert res.is_clarification_needed is True
    assert "hourly rate" in res.clarification_question
    assert res.invoice_draft is None

def test_smart_create_complete_draft_result():
    draft = InvoiceDraft(
        client_name="Stripe Partner",
        client_email="billing@stripe.com",
        items=[LineItem(name="Backend Integration", quantity=10, unit_price=120.0)],
        due_date="2026-11-20"
    )
    res = SmartCreateResult(
        is_clarification_needed=False,
        clarification_question=None,
        invoice_draft=draft
    )
    assert res.is_clarification_needed is False
    assert res.invoice_draft.client_email == "billing@stripe.com"
