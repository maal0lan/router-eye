import logging
import re
import urllib.parse
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.engine import make_url
from .config import settings

logger = logging.getLogger("routerscope.db")

raw_url = (settings.database_url or "").strip().strip("'").strip('"')

def clean_database_url(url: str) -> str:
    if not url:
        logger.warning("DATABASE_URL is empty! Falling back to local sqlite.")
        return "sqlite:///./routerscope.db"

    # Normalize render / heroku postgres prefixes to psycopg v3
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    # Validate if SQLAlchemy can parse it directly
    try:
        make_url(url)
        return url
    except Exception:
        pass

    # If parsing failed, it is usually because the password has unescaped special characters (@, #, :, /, etc.)
    # Format: scheme://username:password@host:port/database
    match = re.match(r"^([^:]+://)([^:]+):(.*)@([^/@:]+(?::\d+)?)(/.*)?$", url)
    if match:
        scheme, user, raw_pass, host_port, db_part = match.groups()
        encoded_pass = urllib.parse.quote_plus(raw_pass)
        fixed_url = f"{scheme}{user}:{encoded_pass}@{host_port}{db_part or ''}"
        try:
            make_url(fixed_url)
            return fixed_url
        except Exception:
            pass

    return url

db_url = clean_database_url(raw_url)

try:
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
except Exception as e:
    # Mask password for safe logging
    masked = re.sub(r":([^:@]+)@", ":****@", raw_url)
    print(f"\n[FATAL DB CONFIG ERROR] Failed to parse DATABASE_URL: {masked!r} (length: {len(raw_url)})\n", flush=True)
    raise

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
