import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User, UserSession, Workspace

_bearer = HTTPBearer(auto_error=False)
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, salt_hex, digest_hex = stored.split("$", 2)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def workspace_for_user(db: Session, user: User) -> Workspace:
    workspace = db.scalar(select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.id))
    if workspace:
        return workspace
    workspace = Workspace(user_id=user.id, name="Main workspace", slug=f"user-{user.id}-main")
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


def create_user(db: Session, email: str, password: str) -> tuple[User, Workspace]:
    normalized = normalize_email(email)
    if db.scalar(select(User).where(User.email == normalized)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=normalized, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    workspace = Workspace(user_id=user.id, name="Main workspace", slug=f"user-{user.id}-main")
    db.add(workspace)
    db.commit()
    db.refresh(user)
    db.refresh(workspace)
    return user, workspace


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hmac.new(settings.auth_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    session = UserSession(user_id=user.id, token_hash=token_hash, expires_at=datetime.utcnow() + timedelta(days=settings.auth_session_days))
    db.add(session)
    db.commit()
    return token


def _user_from_token(db: Session, token: str) -> User | None:
    token_hash = hmac.new(settings.auth_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    if not session or session.expires_at <= datetime.utcnow():
        return None
    user = db.get(User, session.user_id)
    return user if user and user.active else None


def revoke_session(db: Session, token: str) -> None:
    token_hash = hmac.new(settings.auth_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    if session:
        db.delete(session)
        db.commit()


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.auth_session_days * 86400,
        httponly=True,
        secure=settings.app_environment.lower() == "production",
        samesite="lax",
        path="/",
    )


def development_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == settings.dev_user_email))
    if user:
        workspace_for_user(db, user)
        return user
    user = User(email=settings.dev_user_email, password_hash=None, plan="BUSINESS", subscription_status="NOT_CONFIGURED")
    db.add(user)
    db.flush()
    db.add(Workspace(user_id=user.id, name="Local development workspace", slug="local-development"))
    db.commit()
    db.refresh(user)
    return user


def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials else request.cookies.get(settings.session_cookie_name)
    if token:
        user = _user_from_token(db, token)
        if user:
            return user
    if not settings.auth_required:
        return development_user(db)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
