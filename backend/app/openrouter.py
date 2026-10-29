from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import UsageEvent

TIMEOUT = httpx.Timeout(25.0, connect=8.0)

class OpenRouterError(Exception):
    pass

async def _request(key: str, method: str, path: str, **kwargs):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            response = await client.request(method, f"{settings.openrouter_base_url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OpenRouterError(f"OpenRouter network error: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        # Avoid returning response bodies because they may contain sensitive information.
        if response.status_code == 401: detail = "Key rejected (401). Use a valid OpenRouter management key."
        elif response.status_code == 403: detail = "Permission denied (403). This endpoint requires a management key or appropriate access."
        elif response.status_code == 429: detail = "OpenRouter rate limit reached. Try again later."
        else: detail = f"OpenRouter returned HTTP {response.status_code}."
        raise OpenRouterError(detail)
    try: return response.json()
    except ValueError as exc: raise OpenRouterError("OpenRouter returned invalid JSON.") from exc

async def validate_key(key: str):
    credits = await _request(key, "GET", "/credits")
    # Analytics metadata validates access to the endpoint used for reporting.
    await _request(key, "GET", "/analytics/meta")
    data = credits.get("data", {})
    return {"total_credits": data.get("total_credits"), "total_usage": data.get("total_usage")}

def make_time_range(start_date=None, end_date=None, days: int = 30):
    if start_date and end_date:
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    else:
        end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start_dt = end_dt - timedelta(days=max(1, min(days or 30, 365)))
    return {
        "start": start_dt.isoformat().replace("+00:00", "Z"),
        "end": end_dt.isoformat().replace("+00:00", "Z"),
    }

async def fetch_analytics(key: str, days: int = 30, start_date=None, end_date=None):
    tr = make_time_range(start_date, end_date, days)
    payload = {
        "metrics": ["total_usage", "request_count", "tokens_total", "tokens_prompt", "tokens_completion"],
        "granularity": "day",
        "time_range": tr,
        "limit": 1000,
    }
    result = await _request(key, "POST", "/analytics/query", json=payload)
    data = result.get("data", {})
    rows = data.get("data", []) if isinstance(data, dict) else []
    parsed = []
    for row in rows:
        stamp = row.get("date__day") or row.get("date") or row.get("day")
        if not stamp: continue
        try: day = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).date()
        except ValueError: continue
        parsed.append({
            "day": day,
            "spend_usd": float(row.get("total_usage") or 0),
            "requests": int(row.get("request_count") or 0),
            "tokens": int(row.get("tokens_total") or 0),
            "prompt_tokens": int(row.get("tokens_prompt") or 0),
            "completion_tokens": int(row.get("tokens_completion") or 0),
        })
    # Aggregate query gives authoritative totals for the current summary even if daily query is empty.
    totals_payload = {
        "metrics": ["total_usage", "request_count", "tokens_total", "tokens_prompt", "tokens_completion"],
        "time_range": tr,
        "limit": 10,
    }
    totals_result = await _request(key, "POST", "/analytics/query", json=totals_payload)
    total_rows = totals_result.get("data", {}).get("data", [])
    totals = total_rows[0] if total_rows else {}
    return parsed, {
        "spend_usd": float(totals.get("total_usage") or 0),
        "requests": int(totals.get("request_count") or 0),
        "tokens": int(totals.get("tokens_total") or 0),
        "prompt_tokens": int(totals.get("tokens_prompt") or 0),
        "completion_tokens": int(totals.get("tokens_completion") or 0),
    }

async def fetch_key_analytics(key: str, days: int = 30, start_date=None, end_date=None):
    tr = make_time_range(start_date, end_date, days)
    payload = {
        "metrics": ["tokens_total"],
        "dimensions": ["api_key_id"],
        "granularity": "day",
        "time_range": tr,
        "limit": 500,
    }
    result = await _request(key, "POST", "/analytics/query", json=payload)
    data = result.get("data", {})
    rows = data.get("data", []) if isinstance(data, dict) else []
    parsed = []
    for row in rows:
        stamp = row.get("date__day") or row.get("date") or row.get("day")
        if not stamp: continue
        try: day = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).date()
        except ValueError: continue
        parsed.append({
            "day": day.isoformat(),
            "api_key_id": row.get("api_key_id") or "default",
            "tokens": int(row.get("tokens_total") or 0),
        })
    return parsed

async def fetch_model_analytics(key: str, days: int = 30, start_date=None, end_date=None):
    tr = make_time_range(start_date, end_date, days)
    payload = {
        "metrics": ["tokens_total", "request_count", "total_usage"],
        "dimensions": ["model"],
        "time_range": tr,
        "limit": 100,
    }
    try:
        result = await _request(key, "POST", "/analytics/query", json=payload)
        data = result.get("data", {})
        rows = data.get("data", []) if isinstance(data, dict) else []
        models = []
        for r in rows:
            m_name = r.get("model") or "unknown"
            models.append({
                "model": m_name,
                "tokens": int(r.get("tokens_total") or 0),
                "requests": int(r.get("request_count") or 0),
                "spend_usd": float(r.get("total_usage") or 0.0),
            })
        return models
    except Exception:
        return []


def record_usage(db: Session, account_id: int, model: str, usage: dict) -> None:
    """Record token usage from an OpenRouter response into the usage_events table.

    If OpenRouter doesn't return usage, the caller should mark it unavailable
    rather than pretending the request used zero tokens.
    """
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    # Don't create a misleading event if the provider returned no usage.
    if not usage:  # empty dict or None-like
        return
    event = UsageEvent(
        account_id=account_id,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cost_usd=usage.get("cost"),
        source="router_eye",
    )
    try:
        db.add(event)
        db.commit()
    except Exception:
        db.rollback()
        raise
