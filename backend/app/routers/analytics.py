from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, UsageEvent

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
def usage_overview(db: Session = Depends(get_db)):
    """Return last 24-hour hourly token usage chart and account leaderboard.

    Reports requests recorded by Router Eye. Does not add OpenRouter aggregate
    totals to avoid double-counting.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=24)

    # Hourly buckets of token usage
    hour = func.date_trunc("hour", UsageEvent.occurred_at).label("hour")
    hourly_rows = db.execute(
        select(
            hour,
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("total_tokens"),
            func.count(UsageEvent.id).label("requests"),
        )
        .where(UsageEvent.occurred_at >= start)
        .group_by(hour)
        .order_by(hour)
    ).all()

    # Build chart series with time labels
    chart = []
    for row in hourly_rows:
        chart.append(
            {
                "time": row.hour,
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "requests": int(row.requests or 0),
            }
        )

    # Account leaderboard: total tokens and request count per account
    account_rows = db.execute(
        select(
            Account.id,
            Account.label,
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.count(UsageEvent.id), 0).label("requests"),
        )
        .join(UsageEvent, Account.id == UsageEvent.account_id)
        .group_by(Account.id)
        .order_by(func.coalesce(func.sum(UsageEvent.total_tokens), 0).desc())
    ).all()

    accounts = []
    for row in account_rows:
        accounts.append(
            {
                "account_id": row.id,
                "account_name": row.label or f"Account {row.id}",
                "total_tokens": int(row.total_tokens or 0),
                "requests": int(row.requests or 0),
            }
        )

    return {"chart": chart, "accounts": accounts}