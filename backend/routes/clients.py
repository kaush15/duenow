import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import Client, Invoice
from backend.scoring import calculate_client_score, generate_score_explanation
from backend.autopilot import get_simulated_today

router = APIRouter(prefix="/api/clients", tags=["clients"])

@router.get("")
def list_clients(db: Session = Depends(get_db)):
    """Lists all clients with calculated scores, risk levels, and invoice stats."""
    today = get_simulated_today(db)
    clients = db.query(Client).all()
    results = []

    for c in clients:
        invoices = c.invoices
        open_invs = [inv for inv in invoices if inv.status in ["SENT", "SCHEDULED"]]
        paid_invs = [inv for inv in invoices if inv.status in ["PAID", "MARKED_AS_PAID"]]
        
        total_billed = sum(inv.amount for inv in invoices)
        total_paid = sum(inv.amount for inv in paid_invs)
        total_outstanding = sum(inv.amount for inv in open_invs)

        score = c.reliability_score
        if score >= 80:
            risk_tier = "low_risk"
            risk_label = "Low Risk (Reliable)"
        elif score >= 55:
            risk_tier = "medium_risk"
            risk_label = "Moderate Risk"
        else:
            risk_tier = "high_risk"
            risk_label = "High Risk (Late Payer)"

        results.append({
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "company": c.company or c.name,
            "reliability_score": score,
            "score_explanation": c.score_explanation or "Initial client profile.",
            "predicted_pay_days": c.predicted_pay_days,
            "risk_tier": risk_tier,
            "risk_label": risk_label,
            "total_invoices": len(invoices),
            "open_invoices_count": len(open_invs),
            "total_billed": total_billed,
            "total_paid": total_paid,
            "total_outstanding": total_outstanding,
            "created_at": c.created_at.isoformat() if c.created_at else None
        })

    return results

@router.get("/{client_id}")
def get_client(client_id: int, db: Session = Depends(get_db)):
    """Returns single client details with associated invoices."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    res = client.to_dict()
    res["invoices"] = [inv.to_dict() for inv in client.invoices]
    return res

@router.post("/{client_id}/recalculate-score")
async def recalculate_score(client_id: int, db: Session = Depends(get_db)):
    """Recalculates reliability score from scratch using latest invoice history."""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    invoices = [inv.to_dict() for inv in client.invoices]
    new_score, lag_days, stats = calculate_client_score(invoices)
    
    explanation = await generate_score_explanation(client.name, new_score, stats)
    client.reliability_score = new_score
    client.predicted_pay_days = lag_days
    client.score_explanation = explanation
    db.commit()

    return {
        "client_id": client.id,
        "reliability_score": new_score,
        "predicted_pay_days": lag_days,
        "score_explanation": explanation,
        "stats": stats
    }
