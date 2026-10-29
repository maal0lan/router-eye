import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
import bcrypt
from .config import settings
from .db import Base, engine, SessionLocal, get_db
from .models import Account, DailySnapshot, UsageEvent, User, utcnow
from .routers.analytics import router as analytics_router
from .security import encrypt_secret, decrypt_secret
from .openrouter import validate_key, fetch_analytics, fetch_key_analytics, OpenRouterError

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)

class RegisterBody(BaseModel):
    username: str = Field(min_length=2, max_length=50, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=8, max_length=300)

class AccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    management_key: str = Field(min_length=10, max_length=500)

async def poll_all():
    db = SessionLocal()
    try:
        accounts = db.scalars(select(Account)).all()
        for account in accounts:
            try:
                key = decrypt_secret(account.encrypted_key)
                credits = await validate_key(key)
                rows, _ = await fetch_analytics(key, days=30)
                now = utcnow()
                for row in rows:
                    snapshot = db.scalar(select(DailySnapshot).where(DailySnapshot.account_id == account.id, DailySnapshot.day == row["day"]))
                    if snapshot is None:
                        snapshot = DailySnapshot(account_id=account.id, day=row["day"])
                        db.add(snapshot)
                    snapshot.spend_usd = row["spend_usd"]
                    snapshot.requests = row["requests"]
                    snapshot.tokens = row["tokens"]
                    snapshot.total_credits = credits.get("total_credits")
                    snapshot.total_usage = credits.get("total_usage")
                    snapshot.captured_at = now
                account.last_checked_at = now
                account.last_error = None
                db.commit()
            except Exception as exc:
                db.rollback()
                account = db.get(Account, account.id)
                if account:
                    account.last_checked_at = utcnow()
                    account.last_error = str(exc)[:300]
                    db.commit()
    finally:
        db.close()

async def poll_loop():
    while True:
        await asyncio.sleep(max(5, settings.poll_minutes) * 60)
        try: await poll_all()
        except Exception: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.admin_password == "change-this-before-deploying" or len(settings.admin_password) < 16:
        raise RuntimeError("Set ADMIN_PASSWORD to a unique password of at least 16 characters before starting RouterScope.")
    if settings.session_secret == "change-this-session-secret-before-deploying" or len(settings.session_secret) < 32:
        raise RuntimeError("Set SESSION_SECRET to a unique random value of at least 32 characters before starting RouterScope.")
    if not settings.encryption_key or settings.encryption_key.startswith("REPLACE_"):
        raise RuntimeError("Set ENCRYPTION_KEY to a generated Fernet key before starting RouterScope.")
    # Validate the Fernet key before accepting traffic.
    encrypt_secret("startup-check")
    Base.metadata.create_all(bind=engine)
    # Seed the env-configured admin into the users table if they don't exist yet
    _seed_admin()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

def _seed_admin():
    """Ensure the ADMIN_USERNAME/ADMIN_PASSWORD user exists in the DB."""
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.username == settings.admin_username))
        if existing is None:
            hashed = hash_password(settings.admin_password)
            db.add(User(username=settings.admin_username, password_hash=hashed))
            db.commit()
        else:
            # If the env password changed, update the hash so env always wins for admin
            if not verify_password(settings.admin_password, existing.password_hash):
                existing.password_hash = hash_password(settings.admin_password)
                db.commit()
    finally:
        db.close()

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="routerscope_session", same_site="strict", https_only=settings.cookie_secure, max_age=60 * 60 * 12)
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
app.include_router(analytics_router)

def require_auth(request: Request):
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return True

def account_json(account: Account):
    return {"id": account.id, "label": account.label, "created_at": account.created_at.isoformat() if account.created_at else None, "last_checked_at": account.last_checked_at.isoformat() if account.last_checked_at else None, "last_error": account.last_error, "key_hint": "•••••••• (encrypted)"}

@app.get("/api/health")
def health(): return {"ok": True, "app": settings.app_name}

@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    # Look up user in DB first (DB is source of truth after first boot)
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    request.session.clear()
    request.session["authenticated"] = True
    request.session["username"] = user.username
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"authenticated": True, "csrf": request.session["csrf"], "username": user.username}

@app.post("/api/auth/register")
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    """Create a new RouterScope account. Anyone can register; no invite needed."""
    # Check duplicate
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already taken. Please choose a different one.")
    hashed = hash_password(body.password)
    user = User(username=body.username, password_hash=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)
    # Auto-login after registration
    request.session.clear()
    request.session["authenticated"] = True
    request.session["username"] = user.username
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"registered": True, "authenticated": True, "csrf": request.session["csrf"], "username": user.username}

@app.get("/api/auth/me")
def me(request: Request):
    if request.session.get("authenticated") is not True: return {"authenticated": False}
    return {"authenticated": True, "csrf": request.session.get("csrf"), "username": request.session.get("username")}

@app.post("/api/auth/logout", dependencies=[Depends(require_auth)])
def logout(request: Request):
    request.session.clear()
    return {"ok": True}

def require_csrf(request: Request):
    if request.method in ("POST", "DELETE"):
        sent = request.headers.get("X-CSRF-Token", "")
        expected = request.session.get("csrf", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            raise HTTPException(status_code=403, detail="CSRF check failed. Refresh the page and retry.")

@app.get("/api/accounts", dependencies=[Depends(require_auth)])
def list_accounts(db: Session = Depends(get_db)):
    return [account_json(a) for a in db.scalars(select(Account).order_by(Account.id)).all()]

@app.post("/api/accounts", dependencies=[Depends(require_auth), Depends(require_csrf)])
async def add_account(body: AccountCreate, db: Session = Depends(get_db)):
    try: await validate_key(body.management_key.strip())
    except OpenRouterError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = Account(label=body.label.strip(), encrypted_key=encrypt_secret(body.management_key.strip()))
    db.add(account); db.commit(); db.refresh(account)
    return account_json(account)

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(require_auth), Depends(require_csrf)])
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account: raise HTTPException(status_code=404, detail="Account not found.")
    db.delete(account); db.commit()
    return {"ok": True}

@app.post("/api/refresh", dependencies=[Depends(require_auth), Depends(require_csrf)])
async def refresh():
    await poll_all()
    return {"ok": True, "refreshed_at": utcnow().isoformat()}

class RequestResponse(BaseModel):
    ok: bool
    recorded_tokens: int

@app.post("/api/request", response_model=RequestResponse, dependencies=[Depends(require_auth), Depends(require_csrf)])
async def forward_request(
    model: str = Form(...),
    prompt_tokens: int = Form(0),
    completion_tokens: int = Form(0),
    db: Session = Depends(get_db),
):
    # Read the first account for now - in a full implementation,
    # the account would be selected based on routing logic
    accounts = db.scalars(select(Account)).all()
    if not accounts:
        return {"ok": False, "recorded_tokens": 0}
    account = accounts[0]

    # Decrypt the management key
    from .security import encrypt_secret, decrypt_secret
    key = decrypt_secret(account.encrypted_key)

    # Forward to OpenRouter
    from .openrouter import _request, OpenRouterError
    try:
        response_data = await _request(key, "POST", "/v1/chat/completions", 
                                       json={"model": model, "messages": [{"role": "user", "content": ""}], "stream": False})
    except OpenRouterError:
        # Track failed request separately if desired
        return {"ok": False, "recorded_tokens": 0}

    # Extract usage from response
    usage = response_data.get("usage", {})
    if usage:
        from .openrouter import record_usage
        record_usage(db, account.id, model, usage)

    return {"ok": True, "recorded_tokens": usage.get("total_tokens", 0) if usage else 0}


@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
async def dashboard(days: int = 30, account_id: Optional[int] = None, db: Session = Depends(get_db)):
    days = max(1, min(days, 365))
    start = date.today() - timedelta(days=days - 1)
    account_stmt = select(Account)
    if account_id is not None: account_stmt = account_stmt.where(Account.id == account_id)
    accounts = db.scalars(account_stmt.order_by(Account.id)).all()
    ids = [a.id for a in accounts]
    if not ids:
        return {"summary": {"spend_usd": 0, "requests": 0, "tokens": 0, "accounts": 0, "credits_remaining": 0}, "series": [], "accounts": [], "top_keys": [], "key_trend": []}
    rows = db.scalars(select(DailySnapshot).where(DailySnapshot.account_id.in_(ids), DailySnapshot.day >= start).order_by(DailySnapshot.day)).all()
    by_day = {}
    per_account = {a.id: {"id": a.id, "label": a.label, "spend_usd": 0.0, "requests": 0, "tokens": 0, "credits_remaining": None, "last_error": a.last_error} for a in accounts}
    latest = {}
    for r in rows:
        key = r.day.isoformat()
        point = by_day.setdefault(key, {"day": key, "spend_usd": 0.0, "requests": 0, "tokens": 0})
        point["spend_usd"] += r.spend_usd or 0
        point["requests"] += r.requests or 0
        point["tokens"] += r.tokens or 0
        p = per_account[r.account_id]
        p["spend_usd"] += r.spend_usd or 0
        p["requests"] += r.requests or 0
        p["tokens"] += r.tokens or 0
        if r.day >= latest.get(r.account_id, date.min):
            latest[r.account_id] = r.day
            p["credits_remaining"] = max(0, (r.total_credits or 0) - (r.total_usage or 0)) if r.total_credits is not None else None
    series = []
    for i in range(days):
        d = start + timedelta(days=i)
        series.append(by_day.get(d.isoformat(), {"day": d.isoformat(), "spend_usd": 0, "requests": 0, "tokens": 0}))
    summary = {"spend_usd": sum(x["spend_usd"] for x in series), "requests": sum(x["requests"] for x in series), "tokens": sum(x["tokens"] for x in series), "accounts": len(accounts), "credits_remaining": sum((p["credits_remaining"] or 0) for p in per_account.values())}

    # Fetch API Key breakdown across accounts
    key_totals = {}
    key_by_day = { (start + timedelta(days=i)).isoformat(): {} for i in range(days) }

    for a in accounts:
        try:
            k = decrypt_secret(a.encrypted_key)
            k_rows = await fetch_key_analytics(k, days=days)
            for r in k_rows:
                key_name = f"{a.label} / {r['api_key_id']}" if len(accounts) > 1 else r['api_key_id']
                d_str = r['day']
                tokens = r['tokens']
                key_totals[key_name] = key_totals.get(key_name, 0) + tokens
                if d_str in key_by_day:
                    key_by_day[d_str][key_name] = key_by_day[d_str].get(key_name, 0) + tokens
        except Exception:
            pass

    # Sort top keys
    sorted_keys = sorted(key_totals.items(), key=lambda x: x[1], reverse=True)
    top_keys = [{"key": k, "tokens": v} for k, v in sorted_keys]

    # Build key_trend series
    key_trend = []
    top_key_names = [k for k, _ in sorted_keys[:8]]  # Top 8 keys
    for i in range(days):
        d_str = (start + timedelta(days=i)).isoformat()
        point = {"day": d_str}
        day_counts = key_by_day.get(d_str, {})
        for kn in top_key_names:
            point[kn] = day_counts.get(kn, 0)
        key_trend.append(point)

    return {
        "summary": summary,
        "series": series,
        "accounts": list(per_account.values()),
        "top_keys": top_keys,
        "key_trend": key_trend,
        "top_key_names": top_key_names
    }

@app.get("/api/history", dependencies=[Depends(require_auth)])
def history(days: int = 90, account_id: Optional[int] = None, db: Session = Depends(get_db)):
    days = max(1, min(days, 365)); start = date.today() - timedelta(days=days - 1)
    stmt = select(DailySnapshot, Account.label).join(Account).where(DailySnapshot.day >= start).order_by(DailySnapshot.day)
    if account_id is not None: stmt = stmt.where(DailySnapshot.account_id == account_id)
    return [{"day": r.day.isoformat(), "account_id": r.account_id, "account": label, "spend_usd": r.spend_usd, "requests": r.requests, "tokens": r.tokens, "captured_at": r.captured_at.isoformat()} for r, label in db.execute(stmt).all()]
