import pytest
from backend.scoring import calculate_client_score, compute_predicted_pay_date

def test_score_empty_history():
    score, lag, stats = calculate_client_score([])
    assert score == 70
    assert lag == 0
    assert stats["total_invoices"] == 0

def test_score_prompt_payer():
    # Paid promptly on or before due date with 0 reminders
    history = [
        {"status": "PAID", "due_date": "2026-08-01", "paid_at": "2026-07-31T12:00:00", "reminder_count": 0},
        {"status": "PAID", "due_date": "2026-09-01", "paid_at": "2026-09-01T15:00:00", "reminder_count": 0},
        {"status": "PAID", "due_date": "2026-10-01", "paid_at": "2026-09-29T09:00:00", "reminder_count": 0}
    ]
    score, lag, stats = calculate_client_score(history)
    assert score >= 90
    assert lag == 0
    assert stats["on_time_rate"] == 100
    assert stats["paid_count"] == 3

def test_score_chronic_late_payer():
    # Repeatedly 20+ days late, needed 3 reminders
    history = [
        {"status": "PAID", "due_date": "2026-06-01", "paid_at": "2026-06-25T12:00:00", "reminder_count": 3},
        {"status": "PAID", "due_date": "2026-07-01", "paid_at": "2026-07-28T12:00:00", "reminder_count": 3},
        {"status": "SENT", "due_date": "2026-08-01", "paid_at": None, "reminder_count": 2}
    ]
    score, lag, stats = calculate_client_score(history)
    assert score < 50
    assert lag > 15
    assert stats["paid_count"] == 2

def test_predicted_pay_date_calculation():
    due_date = "2026-11-10"
    predicted = compute_predicted_pay_date(due_date, predicted_lag_days=5)
    assert predicted == "2026-11-15"

    # 0 lag days should match due date
    assert compute_predicted_pay_date("2026-12-01", 0) == "2026-12-01"
