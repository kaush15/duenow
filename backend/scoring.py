import datetime
from typing import List, Dict, Any, Tuple
import logging
from backend.llm_adapter import llm_adapter
from pydantic import BaseModel, Field

logger = logging.getLogger("scoring")

class ScoreExplanationSchema(BaseModel):
    explanation: str = Field(..., description="One concise, objective sentence explaining the client's reliability score and payment trend.")
    risk_level: str = Field(..., description="'low', 'medium', or 'high'")

def calculate_client_score(past_invoices: List[Dict[str, Any]]) -> Tuple[int, int, Dict[str, Any]]:
    """
    Computes a 0-100 reliability score from payment history:
    - On-time / paid ratio (0-40)
    - Punctuality / average days late (0-40)
    - Reminder responsiveness (0-20)
    Returns: (score, predicted_lag_days, stats_dict)
    """
    if not past_invoices:
        return 70, 0, {
            "total_invoices": 0,
            "paid_count": 0,
            "avg_days_late": 0,
            "avg_reminders": 0,
            "on_time_rate": 100
        }

    total = len(past_invoices)
    paid_invoices = [inv for inv in past_invoices if inv.get("status") in ["PAID", "MARKED_AS_PAID"]]
    paid_count = len(paid_invoices)

    # 1. Paid Ratio (0-40 pts)
    paid_ratio = paid_count / total if total > 0 else 1.0
    ratio_score = paid_ratio * 40.0

    # 2. Punctuality (0-40 pts)
    days_late_list = []
    reminders_list = []

    for inv in past_invoices:
        due_str = inv.get("due_date")
        paid_str = inv.get("paid_at")
        reminders = inv.get("reminder_count", 0)
        reminders_list.append(reminders)

        if due_str and paid_str:
            try:
                due_dt = datetime.datetime.strptime(due_str[:10], "%Y-%m-%d").date()
                paid_dt = datetime.datetime.fromisoformat(paid_str.replace("Z", "+00:00")).date()
                days_late = (paid_dt - due_dt).days
                days_late_list.append(max(0, days_late))
            except Exception:
                pass
        elif inv.get("status") in ["SENT", "SCHEDULED"] and due_str:
            # Currently open invoice
            try:
                due_dt = datetime.datetime.strptime(due_str[:10], "%Y-%m-%d").date()
                today = datetime.date.today()
                if today > due_dt:
                    days_late_list.append((today - due_dt).days)
            except Exception:
                pass

    avg_days_late = sum(days_late_list) / len(days_late_list) if days_late_list else 0.0

    if avg_days_late <= 0:
        punctuality_score = 40.0
    elif avg_days_late <= 3:
        punctuality_score = 32.0
    elif avg_days_late <= 7:
        punctuality_score = 22.0
    elif avg_days_late <= 14:
        punctuality_score = 12.0
    else:
        punctuality_score = max(0.0, 10.0 - (avg_days_late - 14) * 0.5)

    # 3. Reminder responsiveness (0-20 pts)
    avg_reminders = sum(reminders_list) / len(reminders_list) if reminders_list else 0.0
    if avg_reminders <= 0.2:
        reminder_score = 20.0
    elif avg_reminders <= 1.0:
        reminder_score = 15.0
    elif avg_reminders <= 2.0:
        reminder_score = 8.0
    else:
        reminder_score = 2.0

    final_score = int(round(ratio_score + punctuality_score + reminder_score))
    final_score = max(5, min(100, final_score))

    stats = {
        "total_invoices": total,
        "paid_count": paid_count,
        "avg_days_late": round(avg_days_late, 1),
        "avg_reminders": round(avg_reminders, 1),
        "on_time_rate": int(round((len([d for d in days_late_list if d == 0]) / len(days_late_list) * 100) if days_late_list else 100))
    }

    predicted_lag_days = int(round(avg_days_late))
    return final_score, predicted_lag_days, stats

async def generate_score_explanation(
    client_name: str,
    score: int,
    stats: Dict[str, Any]
) -> str:
    """Uses LLM to craft a single, clear, professional sentence explaining the reliability score."""
    prompt = (
        f"Client: {client_name}\n"
        f"Reliability Score: {score}/100\n"
        f"Paid Invoices: {stats.get('paid_count')}/{stats.get('total_invoices')}\n"
        f"Average Days Late: {stats.get('avg_days_late')} days\n"
        f"On-Time Rate: {stats.get('on_time_rate')}%\n"
        f"Average Reminders Needed: {stats.get('avg_reminders')}\n\n"
        f"Write exactly ONE professional sentence explaining this score and what the freelancer should expect."
    )
    system = "You are a credit and collections intelligence analyst for freelancers. Be concise, objective, and specific."

    try:
        result = await llm_adapter.generate_structured(
            prompt,
            ScoreExplanationSchema,
            system_instruction=system
        )
        return result.explanation
    except Exception as e:
        logger.warning(f"LLM explanation generation failed: {e}. Falling back to rule-based explanation.")
        if score >= 85:
            return f"{client_name} is highly reliable, maintaining a {stats.get('on_time_rate')}% on-time payment track record."
        elif score >= 65:
            return f"{client_name} reliably pays invoices, occasionally trailing by {stats.get('avg_days_late')} days past due date."
        else:
            return f"{client_name} exhibits chronic payment delays averaging {stats.get('avg_days_late')} days late and requires active reminder follow-ups."

def compute_predicted_pay_date(due_date_str: str, predicted_lag_days: int) -> str:
    """Calculates predicted pay date given due date and client lag."""
    try:
        due_dt = datetime.datetime.strptime(due_date_str[:10], "%Y-%m-%d").date()
        pred_dt = due_dt + datetime.timedelta(days=max(0, predicted_lag_days))
        return pred_dt.isoformat()
    except Exception:
        return due_date_str
