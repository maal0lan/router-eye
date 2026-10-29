import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .db import Base, engine, SessionLocal, get_db
from .models import Account, DailySnapshot, utcnow
from .security import encrypt_secret, decrypt_secret
from .openrouter import validate_key, fetch_analytics, OpenRouterError

class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)
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
    Base.metadata.create_all(bind=engine)
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="routerscope_session", same_site="strict", https_only=settings.cookie_secure, max_age=60 * 60 * 12)
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

def require_auth(request: Request):
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return True

def account_json(account: Account):
    return {"id": account.id, "label": account.label, "created_at": account.created_at.isoformat() if account.created_at else None, "last_checked_at": account.last_checked_at.isoformat() if account.last_checked_at else None, "last_error": account.last_error, "key_hint": "•••••••• (encrypted)"}

@app.get("/api/health")
def health(): return {"ok": True, "app": settings.app_name}

@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    # Constant-time comparisons reduce timing leakage. Configure a strong password before deployment.
    ok_user = secrets.compare_digest(body.username, settings.admin_username)
    ok_pass = secrets.compare_digest(body.password, settings.admin_password)
    if not (ok_user and ok_pass): raise HTTPException(status_code=401, detail="Invalid username or password.")
    request.session.clear()
    request.session["authenticated"] = True
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"authenticated": True, "csrf": request.session["csrf"]}

@app.get("/api/auth/me")
def me(request: Request):
    if request.session.get("authenticated") is not True: return {"authenticated": False}
    return {"authenticated": True, "csrf": request.session.get("csrf")}

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

@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
def dashboard(days: int = 30, account_id: Optional[int] = None, db: Session = Depends(get_db)):
    days = max(1, min(days, 365))
    start = date.today() - timedelta(days=days - 1)
    account_stmt = select(Account)
    if account_id is not None: account_stmt = account_stmt.where(Account.id == account_id)
    accounts = db.scalars(account_stmt.order_by(Account.id)).all()
    ids = [a.id for a in accounts]
    if not ids:
        return {"summary": {"spend_usd": 0, "requests": 0, "tokens": 0, "accounts": 0, "credits_remaining": 0}, "series": [], "accounts": [], "model_usage": []}
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
    return {"summary": summary, "series": series, "accounts": list(per_account.values()), "model_usage": [], "note": "Historical rows are collected by RouterScope after an account is connected. Model breakdown can be added using the Analytics API dimensions."}

@app.get("/api/history", dependencies=[Depends(require_auth)])
def history(days: int = 90, account_id: Optional[int] = None, db: Session = Depends(get_db)):
    days = max(1, min(days, 365)); start = date.today() - timedelta(days=days - 1)
    stmt = select(DailySnapshot, Account.label).join(Account).where(DailySnapshot.day >= start).order_by(DailySnapshot.day)
    if account_id is not None: stmt = stmt.where(DailySnapshot.account_id == account_id)
    return [{"day": r.day.isoformat(), "account_id": r.account_id, "account": label, "spend_usd": r.spend_usd, "requests": r.requests, "tokens": r.tokens, "captured_at": r.captured_at.isoformat()} for r, label in db.execute(stmt).all()]
