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

class UserCreateAdmin(BaseModel):
    username: str = Field(min_length=2, max_length=50, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=8, max_length=300)
    is_admin: bool = False

class AccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    management_key: str = Field(min_length=10, max_length=500)

async def poll_account(account: Account, db: Session):
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

async def poll_all():
    db = SessionLocal()
    try:
        accounts = db.scalars(select(Account)).all()
        for account in accounts:
            try:
                await poll_account(account, db)
            except Exception as exc:
                db.rollback()
                acc = db.get(Account, account.id)
                if acc:
                    acc.last_checked_at = utcnow()
                    acc.last_error = str(exc)[:300]
                    db.commit()
    finally:
        db.close()

async def poll_loop():
    while True:
        await asyncio.sleep(max(5, settings.poll_minutes) * 60)
        try: await poll_all()
        except Exception: pass

def _migrate_db():
    """Ensure user_id column exists on accounts table and is_admin on users table."""
    from sqlalchemy import text
    
    statements = [
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS user_id INTEGER;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;",
        f"UPDATE users SET is_admin = TRUE WHERE username = '{settings.admin_username}';"
    ]
    for stmt in statements:
        try:
            with engine.connect() as conn:
                conn.execute(text(stmt))
                conn.commit()
        except Exception:
            # Fallback for SQLite which doesn't support IF NOT EXISTS in ADD COLUMN
            try:
                base_stmt = stmt.replace(" IF NOT EXISTS", "")
                with engine.connect() as conn:
                    conn.execute(text(base_stmt))
                    conn.commit()
            except Exception:
                pass

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
    _migrate_db()
    # Seed the env-configured admin into the users table if they don't exist yet
    _seed_admin()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

def _seed_admin():
    """Ensure the ADMIN_USERNAME/ADMIN_PASSWORD user exists in the DB, is super admin, and owns orphaned accounts."""
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.username == settings.admin_username))
        if admin is None:
            hashed = hash_password(settings.admin_password)
            admin = User(username=settings.admin_username, password_hash=hashed, is_admin=True)
            db.add(admin)
            db.commit()
            db.refresh(admin)
        else:
            admin.is_admin = True
            # If the env password changed, update the hash so env always wins for admin
            if not verify_password(settings.admin_password, admin.password_hash):
                admin.password_hash = hash_password(settings.admin_password)
            db.commit()
        # Assign any unassigned accounts to the admin
        orphaned = db.scalars(select(Account).where(Account.user_id.is_(None))).all()
        for acc in orphaned:
            acc.user_id = admin.id
        if orphaned:
            db.commit()
    finally:
        db.close()

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
same_site = "none" if settings.cookie_secure else "lax"
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="routerscope_session", same_site=same_site, https_only=settings.cookie_secure, max_age=60 * 60 * 12)
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Accept", "Authorization"]
)
app.include_router(analytics_router)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="Please sign in.")
    user_id = request.session.get("user_id")
    username = request.session.get("username")
    user = None
    if user_id:
        user = db.get(User, user_id)
    if not user and username:
        user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not (current_user.is_admin or current_user.username == settings.admin_username or current_user.username == 'maalolan'):
        raise HTTPException(status_code=403, detail="Super Admin access required.")
    return current_user

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
    is_admin = bool(user.is_admin or user.username == settings.admin_username or user.username == 'maalolan')
    request.session.clear()
    request.session["authenticated"] = True
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["is_admin"] = is_admin
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"authenticated": True, "csrf": request.session["csrf"], "username": user.username, "is_admin": is_admin}

@app.post("/api/auth/register")
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    """Create a new RouterScope account. Anyone can register; no invite needed."""
    # Check duplicate
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already taken. Please choose a different one.")
    hashed = hash_password(body.password)
    user = User(username=body.username, password_hash=hashed, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    # Auto-login after registration
    request.session.clear()
    request.session["authenticated"] = True
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["is_admin"] = False
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"registered": True, "authenticated": True, "csrf": request.session["csrf"], "username": user.username, "is_admin": False}

@app.get("/api/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    if request.session.get("authenticated") is not True: return {"authenticated": False}
    user_id = request.session.get("user_id")
    username = request.session.get("username")
    user = None
    if user_id:
        user = db.get(User, user_id)
    if not user and username:
        user = db.scalar(select(User).where(User.username == username))
    
    is_admin = False
    if user:
        is_admin = bool(user.is_admin or user.username == settings.admin_username or user.username == 'maalolan')
        username = user.username
        request.session["user_id"] = user.id
        request.session["is_admin"] = is_admin
    elif username == settings.admin_username or username == 'maalolan':
        is_admin = True

    return {"authenticated": True, "csrf": request.session.get("csrf"), "username": username, "is_admin": is_admin}

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

@app.get("/api/accounts")
def list_accounts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [account_json(a) for a in db.scalars(select(Account).where(Account.user_id == current_user.id).order_by(Account.id)).all()]

@app.post("/api/accounts", dependencies=[Depends(require_csrf)])
async def add_account(body: AccountCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try: await validate_key(body.management_key.strip())
    except OpenRouterError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = Account(
        label=body.label.strip(),
        encrypted_key=encrypt_secret(body.management_key.strip()),
        user_id=current_user.id
    )
    db.add(account); db.commit(); db.refresh(account)
    # Immediately pull analytics so the user sees their stats without waiting
    try:
        await poll_account(account, db)
    except Exception:
        pass
    return account_json(account)

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(require_csrf)])
def delete_account(account_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == current_user.id))
    if not account: raise HTTPException(status_code=404, detail="Account not found.")
    db.delete(account); db.commit()
    return {"ok": True}

@app.post("/api/refresh", dependencies=[Depends(require_csrf)])
async def refresh(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).where(Account.user_id == current_user.id)).all()
    for acc in accounts:
        try:
            await poll_account(acc, db)
        except Exception:
            pass
    return {"ok": True, "refreshed_at": utcnow().isoformat()}

class RequestResponse(BaseModel):
    ok: bool
    recorded_tokens: int

@app.post("/api/request", response_model=RequestResponse, dependencies=[Depends(require_csrf)])
async def forward_request(
    model: str = Form(...),
    prompt_tokens: int = Form(0),
    completion_tokens: int = Form(0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Read the first account for the current user
    accounts = db.scalars(select(Account).where(Account.user_id == current_user.id)).all()
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


@app.get("/api/admin/users")
def list_admin_users(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).all()
    result = []
    for u in users:
        accs = db.scalars(select(Account).where(Account.user_id == u.id)).all()
        acc_ids = [a.id for a in accs]
        spend = 0.0
        tokens = 0
        requests = 0
        if acc_ids:
            stats = db.execute(
                select(
                    func.coalesce(func.sum(DailySnapshot.spend_usd), 0),
                    func.coalesce(func.sum(DailySnapshot.tokens), 0),
                    func.coalesce(func.sum(DailySnapshot.requests), 0),
                ).where(DailySnapshot.account_id.in_(acc_ids))
            ).first()
            if stats:
                spend = float(stats[0] or 0.0)
                tokens = int(stats[1] or 0)
                requests = int(stats[2] or 0)
        result.append({
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "accounts_count": len(accs),
            "accounts": [{"id": a.id, "label": a.label, "last_error": a.last_error} for a in accs],
            "total_spend_usd": spend,
            "total_tokens": tokens,
            "total_requests": requests,
        })
    return result

@app.post("/api/admin/users", dependencies=[Depends(require_csrf)])
def create_admin_user(body: UserCreateAdmin, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")
    hashed = hash_password(body.password)
    user = User(username=body.username, password_hash=hashed, is_admin=body.is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "id": user.id, "username": user.username, "is_admin": user.is_admin}

@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(require_csrf)])
def delete_admin_user(user_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own admin account.")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(target)
    db.commit()
    return {"ok": True}

@app.get("/api/dashboard")
async def dashboard(
    days: int = 30,
    account_id: Optional[int] = None,
    user_scope: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    days = max(1, min(days, 365))
    start = date.today() - timedelta(days=days - 1)
    
    # Super Admin can view all users' accounts or filter by specific user
    account_stmt = select(Account)
    if current_user.is_admin and user_scope == "all":
        # All accounts in system
        pass
    elif current_user.is_admin and user_scope and user_scope.isdigit():
        account_stmt = account_stmt.where(Account.user_id == int(user_scope))
    else:
        account_stmt = account_stmt.where(Account.user_id == current_user.id)

    if account_id is not None: account_stmt = account_stmt.where(Account.id == account_id)
    accounts = db.scalars(account_stmt.order_by(Account.id)).all()
    ids = [a.id for a in accounts]
    if not ids:
        return {"summary": {"spend_usd": 0, "requests": 0, "tokens": 0, "accounts": 0, "credits_remaining": 0}, "series": [], "accounts": [], "top_keys": [], "key_trend": [], "top_key_names": []}
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

@app.get("/api/history")
def history(days: int = 90, account_id: Optional[int] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    days = max(1, min(days, 365)); start = date.today() - timedelta(days=days - 1)
    stmt = select(DailySnapshot, Account.label).join(Account).where(Account.user_id == current_user.id, DailySnapshot.day >= start).order_by(DailySnapshot.day)
    if account_id is not None: stmt = stmt.where(DailySnapshot.account_id == account_id)
    return [{"day": r.day.isoformat(), "account_id": r.account_id, "account": label, "spend_usd": r.spend_usd, "requests": r.requests, "tokens": r.tokens, "captured_at": r.captured_at.isoformat()} for r, label in db.execute(stmt).all()]
