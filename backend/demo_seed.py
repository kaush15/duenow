import datetime
import json
from sqlalchemy.orm import Session
from backend.models import Client, Invoice, ApprovalQueueItem, AuditLog, AppSetting
from backend.scoring import calculate_client_score, generate_score_explanation

DEMO_CLIENTS = [
    {
        "name": "Acme Design Co",
        "email": "billing@acmedesign.example.com",
        "company": "Acme Corporation",
        "reliability_score": 96,
        "score_explanation": "Acme Design Co consistently settles invoices within 24 hours of receipt with a 100% on-time payment record.",
        "predicted_pay_days": 1
    },
    {
        "name": "Apex Cloud Systems",
        "email": "finance@apexcloud.example.com",
        "company": "Apex Cloud Inc",
        "reliability_score": 85,
        "score_explanation": "Apex Cloud maintains a dependable payment history, settling within 2-3 days of invoice maturity.",
        "predicted_pay_days": 3
    },
    {
        "name": "Nexa Creative Labs",
        "email": "accounts@nexalabs.example.com",
        "company": "Nexa Media Group",
        "reliability_score": 62,
        "score_explanation": "Nexa Creative exhibits moderate payment friction, averaging 7 days past due and requiring a reminder nudge.",
        "predicted_pay_days": 7
    },
    {
        "name": "Bolt Logistics Corp",
        "email": "ap@boltlogistics.example.com",
        "company": "Bolt Global Freight",
        "reliability_score": 34,
        "score_explanation": "High risk client with chronic payment delays averaging 18 days overdue, requiring multiple escalation notices.",
        "predicted_pay_days": 18
    },
    {
        "name": "Venture Horizon",
        "email": "founders@venturehorizon.example.com",
        "company": "Venture Horizon Studios",
        "reliability_score": 70,
        "score_explanation": "New client with limited transaction history; standard net-15 payment terms recommended.",
        "predicted_pay_days": 0
    }
]

def seed_demo_data(db: Session, reset_existing: bool = True):
    """Populates the database with realistic clients and invoices for demonstration."""
    if reset_existing:
        # Delete only is_demo rows so real invoices and real clients are never touched
        demo_invoices = db.query(Invoice).filter(Invoice.is_demo == True).all()
        if demo_invoices:
            demo_ids = [inv.id for inv in demo_invoices]
            db.query(ApprovalQueueItem).filter(ApprovalQueueItem.invoice_id.in_(demo_ids)).delete(synchronize_session=False)
            db.query(Invoice).filter(Invoice.is_demo == True).delete(synchronize_session=False)
        demo_emails = [c["email"] for c in DEMO_CLIENTS]
        for cl in db.query(Client).filter(Client.email.in_(demo_emails)).all():
            if not cl.invoices:
                db.delete(cl)
        db.commit()

    # Reset simulated offset
    offset_setting = db.query(AppSetting).filter(AppSetting.key == "simulated_days_offset").first()
    if not offset_setting:
        db.add(AppSetting(key="simulated_days_offset", value="0"))
    else:
        offset_setting.value = "0"

    auto_approve_setting = db.query(AppSetting).filter(AppSetting.key == "auto_approve_friendly").first()
    if not auto_approve_setting:
        db.add(AppSetting(key="auto_approve_friendly", value="false"))

    db.commit()

    today = datetime.date.today()
    created_clients = []

    for c_data in DEMO_CLIENTS:
        client = Client(
            name=c_data["name"],
            email=c_data["email"],
            company=c_data["company"],
            reliability_score=c_data["reliability_score"],
            score_explanation=c_data["score_explanation"],
            predicted_pay_days=c_data["predicted_pay_days"]
        )
        db.add(client)
        created_clients.append(client)
    db.commit()

    # Client map by name
    cmap = {c.name: c for c in created_clients}

    # Realistic sample invoices
    sample_invoices = [
        # Historical Paid Invoices (showing good cashflow history)
        {
            "client": cmap["Acme Design Co"],
            "number": "INV-2026-081",
            "amount": 2400.0,
            "status": "PAID",
            "issue_date": (today - datetime.timedelta(days=75)).isoformat(),
            "due_date": (today - datetime.timedelta(days=60)).isoformat(),
            "paid_at": (today - datetime.timedelta(days=61)).isoformat() + "T10:00:00",
            "items": [{"name": "Brand Identity Design & Guidelines", "quantity": 1, "unit_price": 2400.0, "description": "Complete vector package & guidelines"}],
            "note": "Thank you for partnering with us on your brand refresh!",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=75)).isoformat() + "T09:00:00", "details": "Invoice generated"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=75)).isoformat() + "T09:05:00", "details": "Sent via PayPal to billing@acmedesign.example.com"},
                {"event": "PAYMENT_RECEIVED", "timestamp": (today - datetime.timedelta(days=61)).isoformat() + "T10:00:00", "details": "Paid in full via PayPal Sandbox"}
            ]
        },
        {
            "client": cmap["Acme Design Co"],
            "number": "INV-2026-094",
            "amount": 1850.0,
            "status": "PAID",
            "issue_date": (today - datetime.timedelta(days=40)).isoformat(),
            "due_date": (today - datetime.timedelta(days=25)).isoformat(),
            "paid_at": (today - datetime.timedelta(days=26)).isoformat() + "T14:30:00",
            "items": [{"name": "Mobile UI/UX Design Sprint", "quantity": 1, "unit_price": 1850.0, "description": "Figma prototypes and component system"}],
            "note": "Sprint 2 deliverables completed.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=40)).isoformat() + "T11:00:00", "details": "Invoice generated"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=40)).isoformat() + "T11:10:00", "details": "Sent via PayPal"},
                {"event": "PAYMENT_RECEIVED", "timestamp": (today - datetime.timedelta(days=26)).isoformat() + "T14:30:00", "details": "Paid in full"}
            ]
        },
        {
            "client": cmap["Apex Cloud Systems"],
            "number": "INV-2026-088",
            "amount": 3200.0,
            "status": "PAID",
            "issue_date": (today - datetime.timedelta(days=55)).isoformat(),
            "due_date": (today - datetime.timedelta(days=40)).isoformat(),
            "paid_at": (today - datetime.timedelta(days=38)).isoformat() + "T16:00:00",
            "items": [{"name": "Cloud Infrastructure Optimization", "quantity": 32, "unit_price": 100.0, "description": "Terraform refactor & cost tuning"}],
            "note": "AWS cost reduction sprint completed.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=55)).isoformat() + "T08:00:00", "details": "Draft created"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=55)).isoformat() + "T08:15:00", "details": "Sent via PayPal"},
                {"event": "PAYMENT_RECEIVED", "timestamp": (today - datetime.timedelta(days=38)).isoformat() + "T16:00:00", "details": "Settled on PayPal"}
            ]
        },
        {
            "client": cmap["Nexa Creative Labs"],
            "number": "INV-2026-079",
            "amount": 1600.0,
            "status": "PAID",
            "issue_date": (today - datetime.timedelta(days=90)).isoformat(),
            "due_date": (today - datetime.timedelta(days=75)).isoformat(),
            "paid_at": (today - datetime.timedelta(days=68)).isoformat() + "T12:00:00",
            "items": [{"name": "Motion Graphics & Promo Video", "quantity": 1, "unit_price": 1600.0, "description": "30s 4K promo video render"}],
            "note": "Final master export delivered.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=90)).isoformat() + "T10:00:00", "details": "Draft created"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=90)).isoformat() + "T10:15:00", "details": "Sent via PayPal"},
                {"event": "REMINDER_SENT", "timestamp": (today - datetime.timedelta(days=72)).isoformat() + "T09:00:00", "details": "Gentle reminder sent"},
                {"event": "PAYMENT_RECEIVED", "timestamp": (today - datetime.timedelta(days=68)).isoformat() + "T12:00:00", "details": "Paid after reminder"}
            ]
        },

        # Active Open Invoices
        {
            "client": cmap["Apex Cloud Systems"],
            "number": "INV-2026-102",
            "amount": 1500.0,
            "status": "SENT",
            "issue_date": (today - datetime.timedelta(days=10)).isoformat(),
            "due_date": (today + datetime.timedelta(days=4)).isoformat(), # Due in 4 days
            "paid_at": None,
            "items": [{"name": "API Security Audit & Penetration Test", "quantity": 1, "unit_price": 1500.0, "description": "Comprehensive security evaluation report"}],
            "note": "Net 14 payment terms.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=10)).isoformat() + "T09:00:00", "details": "Invoice created"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=10)).isoformat() + "T09:30:00", "details": "Dispatched via PayPal Sandbox"}
            ]
        },
        {
            "client": cmap["Nexa Creative Labs"],
            "number": "INV-2026-103",
            "amount": 2250.0,
            "status": "SENT",
            "issue_date": (today - datetime.timedelta(days=20)).isoformat(),
            "due_date": (today - datetime.timedelta(days=5)).isoformat(), # Overdue by 5 days
            "paid_at": None,
            "items": [
                {"name": "Frontend Web Application Redesign", "quantity": 1, "unit_price": 1800.0, "description": "React + Tailwind responsive implementation"},
                {"name": "Asset Optimization", "quantity": 1, "unit_price": 450.0, "description": "SVG icons & webp image conversions"}
            ],
            "note": "Deliverables merged to staging branch.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=20)).isoformat() + "T14:00:00", "details": "Created"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=20)).isoformat() + "T14:10:00", "details": "Sent via PayPal Sandbox"}
            ]
        },
        {
            "client": cmap["Bolt Logistics Corp"],
            "number": "INV-2026-104",
            "amount": 3800.0,
            "status": "SENT",
            "issue_date": (today - datetime.timedelta(days=30)).isoformat(),
            "due_date": (today - datetime.timedelta(days=15)).isoformat(), # Severely overdue by 15 days
            "paid_at": None,
            "items": [
                {"name": "Supply Chain Dispatch Portal", "quantity": 1, "unit_price": 3200.0, "description": "Real-time fleet tracking dashboard"},
                {"name": "Emergency Integration Support", "quantity": 4, "unit_price": 150.0, "description": "Weekend API support hours"}
            ],
            "note": "Payment strictly required upon milestone completion.",
            "timeline": [
                {"event": "INVOICE_CREATED", "timestamp": (today - datetime.timedelta(days=30)).isoformat() + "T10:00:00", "details": "Created"},
                {"event": "INVOICE_SENT", "timestamp": (today - datetime.timedelta(days=30)).isoformat() + "T10:05:00", "details": "Sent via PayPal Sandbox"},
                {"event": "REMINDER_SENT", "timestamp": (today - datetime.timedelta(days=8)).isoformat() + "T09:00:00", "details": "Firm reminder sent previously"}
            ]
        }
    ]

    for inv_data in sample_invoices:
        client = inv_data["client"]
        paypal_id = f"INV2-DEMO-{inv_data['number']}"
        inv = Invoice(
            paypal_invoice_id=paypal_id,
            invoice_number=inv_data["number"],
            client_id=client.id,
            client_name=client.name,
            client_email=client.email,
            currency="USD",
            amount=inv_data["amount"],
            status=inv_data["status"],
            due_date=inv_data["due_date"],
            issue_date=inv_data["issue_date"],
            paid_at=datetime.datetime.fromisoformat(inv_data["paid_at"]) if inv_data["paid_at"] else None,
            is_demo=True,
            view_url=f"https://www.sandbox.paypal.com/invoice/p/#{paypal_id}",
            items_json=json.dumps(inv_data["items"]),
            note=inv_data["note"],
            terms="Payment due upon specified date. Thank you!",
            timeline_json=json.dumps(inv_data["timeline"])
        )
        db.add(inv)
    db.commit()

    # Pre-populate 2 initial ApprovalQueueItems for immediate demo satisfaction
    overdue_nexa = db.query(Invoice).filter(Invoice.invoice_number == "INV-2026-103").first()
    overdue_bolt = db.query(Invoice).filter(Invoice.invoice_number == "INV-2026-104").first()

    if overdue_nexa:
        queue_nexa = ApprovalQueueItem(
            invoice_id=overdue_nexa.id,
            tone="firm",
            language="English",
            subject="Payment Past Due: Invoice INV-2026-103 ($2,250.00 USD)",
            message="Hi Nexa Creative Labs Team,\n\nWe noticed invoice INV-2026-103 ($2,250.00 USD) was due 5 days ago on " + overdue_nexa.due_date + ". Please let us know if there is anything needed from our side to expedite this payment.\n\nBest regards,\nDueNow Freelance Billing",
            reasoning="Overdue 5 days. Client reliability score is 62/100 (typically settles after 1 reminder). Recommended tone: firm.",
            status="pending"
        )
        db.add(queue_nexa)

    if overdue_bolt:
        queue_bolt = ApprovalQueueItem(
            invoice_id=overdue_bolt.id,
            tone="final",
            language="English",
            subject="URGENT: Final Notice for Outstanding Invoice INV-2026-104 ($3,800.00 USD)",
            message="Dear Bolt Logistics Corp Accounting,\n\nThis is a final notice regarding overdue invoice INV-2026-104 ($3,800.00 USD), which is now 15 days past due. Immediate remittance is required to keep your account in good standing.\n\nPlease complete payment today using the PayPal link provided.",
            reasoning="Severely overdue by 15 days. Client has a low reliability score (34/100) and previous reminder was unanswered. Recommended tone: final.",
            status="pending"
        )
        db.add(queue_bolt)

    # Initial Audit Log entries
    db.add(AuditLog(
        actor="system",
        action="DEMO_DATA_SEEDED",
        details="Loaded 5 demo clients, 7 invoices (4 paid, 3 active open), and 2 Autopilot queue proposals.",
        target_id="all"
    ))
    db.commit()

def fast_forward_time(db: Session, days: int) -> int:
    """Advances simulated time by given days."""
    setting = db.query(AppSetting).filter(AppSetting.key == "simulated_days_offset").first()
    current = int(setting.value) if setting and setting.value.isdigit() else 0
    new_offset = max(0, current + days)
    if not setting:
        setting = AppSetting(key="simulated_days_offset", value=str(new_offset))
        db.add(setting)
    else:
        setting.value = str(new_offset)
    
    db.add(AuditLog(
        actor="user",
        action="TIME_FAST_FORWARDED",
        details=f"Fast-forwarded simulated time by {days} days. Total simulated offset: +{new_offset} days.",
        target_id=str(new_offset)
    ))
    db.commit()
    return new_offset
