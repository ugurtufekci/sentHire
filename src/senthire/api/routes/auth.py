"""Signup, login, session management, and invitation acceptance.

The signup unit is the company: the first user creates the organization
(workspace) and becomes its admin; colleagues join the same organization
through invitations (see team.py) and never create a second workspace.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db
from senthire.config import get_settings
from senthire.db.models import AuditLog, Invitation, Organization, PasswordReset, User
from senthire.services import auth as auth_service
from senthire.services.auth import MIN_PASSWORD_LENGTH
from senthire.services.email import password_reset_email
from senthire.workers.tasks.mail import enqueue_mail

router = APIRouter(tags=["auth"])


class SignupIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AcceptInvitationIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


def _me_payload(user: User, org: Organization) -> dict:
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
        "org": {"id": str(org.id), "name": org.name},
    }


def _start_session(response: Response, session: Session, user: User) -> None:
    token = auth_service.create_session(session, user.id)
    settings = get_settings()
    response.set_cookie(
        value=token,
        max_age=settings.session_ttl_days * 86400,
        **auth_service.session_cookie_kwargs(),
    )


@router.post("/auth/signup", status_code=201)
def signup(
    payload: SignupIn, response: Response, session: Session = Depends(get_db)
) -> dict:
    existing = session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists")

    org = Organization(name=payload.company_name.strip())
    session.add(org)
    session.flush()
    user = User(
        org_id=org.id,
        email=payload.email,
        name=payload.name.strip(),
        role="admin",
        password_hash=auth_service.hash_password(payload.password),
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:  # signup race on the same email
        raise HTTPException(
            status_code=409, detail="an account with this email already exists"
        ) from exc
    session.add(
        AuditLog(
            org_id=org.id,
            actor=user.id,
            event="auth.signup",
            entity={"user_id": str(user.id)},
        )
    )
    _start_session(response, session, user)
    session.commit()
    return _me_payload(user, org)


@router.post("/auth/login")
def login(
    payload: LoginIn, response: Response, session: Session = Depends(get_db)
) -> dict:
    user = session.scalar(select(User).where(User.email == payload.email))
    # Same error for unknown email, wrong password, and deactivated account.
    if (
        user is None
        or not user.is_active
        or not auth_service.verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="invalid email or password")
    user.last_login_at = datetime.now(UTC)
    session.add(
        AuditLog(
            org_id=user.org_id,
            actor=user.id,
            event="auth.login",
            entity={"user_id": str(user.id)},
        )
    )
    _start_session(response, session, user)
    org = session.get(Organization, user.org_id)
    session.commit()
    return _me_payload(user, org)


@router.post("/auth/logout")
def logout(
    request: Request, response: Response, session: Session = Depends(get_db)
) -> dict:
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        auth_service.revoke_session(session, token)
        session.commit()
    response.delete_cookie(**auth_service.session_cookie_kwargs())
    return {"ok": True}


@router.get("/auth/me")
def me(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> dict:
    org = session.get(Organization, user.org_id)
    return _me_payload(user, org)


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordIn, session: Session = Depends(get_db)) -> dict:
    """Always 200 with the same body, so responses don't reveal which emails exist."""
    settings = get_settings()
    user = session.scalar(select(User).where(User.email == payload.email))
    if user is not None and user.is_active:
        now = datetime.now(UTC)
        active_resets = session.scalars(
            select(PasswordReset).where(
                PasswordReset.user_id == user.id,
                PasswordReset.used_at.is_(None),
                PasswordReset.expires_at > now,
            )
        ).all()
        if len(active_resets) < settings.password_reset_max_active:
            token = auth_service.new_token()
            session.add(
                PasswordReset(
                    user_id=user.id,
                    token_hash=auth_service.hash_token(token),
                    expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
                )
            )
            session.add(
                AuditLog(
                    org_id=user.org_id,
                    actor=user.id,
                    event="auth.password_reset_requested",
                    entity={"user_id": str(user.id)},
                )
            )
            session.commit()
            subject, html, text = password_reset_email(
                reset_url=f"{settings.app_base_url}/reset-password/{token}",
                ttl_minutes=settings.password_reset_ttl_minutes,
            )
            enqueue_mail(user.email, subject, html, text)
    return {"ok": True}


def _load_open_reset(session: Session, token: str) -> tuple[PasswordReset, User]:
    reset = session.scalar(
        select(PasswordReset).where(
            PasswordReset.token_hash == auth_service.hash_token(token)
        )
    )
    if reset is None:
        raise HTTPException(status_code=404, detail="reset link not found")
    if reset.used_at is not None or reset.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="reset link expired or already used")
    user = session.get(User, reset.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=410, detail="reset link expired or already used")
    return reset, user


@router.get("/auth/password-resets/{token}")
def password_reset_lookup(token: str, session: Session = Depends(get_db)) -> dict:
    """Public: lets the reset page confirm the link before asking for a password."""
    _, user = _load_open_reset(session, token)
    return {"email_masked": _mask_email(user.email)}


@router.post("/auth/password-resets/{token}")
def reset_password(
    token: str,
    payload: ResetPasswordIn,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    reset, user = _load_open_reset(session, token)
    user.password_hash = auth_service.hash_password(payload.password)
    user.last_login_at = datetime.now(UTC)
    reset.used_at = datetime.now(UTC)
    # A reset invalidates everything: other reset links and all live sessions.
    for other in session.scalars(
        select(PasswordReset).where(
            PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
        )
    ):
        other.used_at = reset.used_at
    auth_service.revoke_all_sessions(session, user.id)
    session.add(
        AuditLog(
            org_id=user.org_id,
            actor=user.id,
            event="auth.password_reset_completed",
            entity={"user_id": str(user.id)},
        )
    )
    _start_session(response, session, user)
    org = session.get(Organization, user.org_id)
    session.commit()
    return _me_payload(user, org)


def _load_open_invitation(session: Session, token: str) -> Invitation:
    invitation = session.scalar(
        select(Invitation).where(Invitation.token_hash == auth_service.hash_token(token))
    )
    if invitation is None or invitation.revoked_at is not None:
        raise HTTPException(status_code=404, detail="invitation not found")
    if invitation.accepted_at is not None:
        raise HTTPException(status_code=410, detail="invitation already used")
    if invitation.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="invitation expired")
    return invitation


@router.get("/auth/invitations/{token}")
def invitation_lookup(token: str, session: Session = Depends(get_db)) -> dict:
    """Public: lets the join page greet the invitee before they set a password."""
    invitation = _load_open_invitation(session, token)
    org = session.get(Organization, invitation.org_id)
    inviter = session.get(User, invitation.invited_by)
    return {
        "email": invitation.email,
        "org_name": org.name,
        "invited_by": inviter.name or inviter.email,
        "expires_at": invitation.expires_at.isoformat(),
    }


@router.post("/auth/invitations/{token}/accept", status_code=201)
def accept_invitation(
    token: str,
    payload: AcceptInvitationIn,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    invitation = _load_open_invitation(session, token)
    org = session.get(Organization, invitation.org_id)

    if session.scalar(select(User).where(User.email == invitation.email)) is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    if org.seat_limit is not None:
        active = session.scalars(
            select(User).where(User.org_id == org.id, User.is_active.is_(True))
        ).all()
        if len(active) >= org.seat_limit:
            raise HTTPException(status_code=409, detail="organization seat limit reached")

    user = User(
        org_id=org.id,
        email=invitation.email,
        name=payload.name.strip(),
        role=invitation.role,
        password_hash=auth_service.hash_password(payload.password),
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:  # accept race on the same email
        raise HTTPException(
            status_code=409, detail="an account with this email already exists"
        ) from exc
    invitation.accepted_at = datetime.now(UTC)
    session.add(
        AuditLog(
            org_id=org.id,
            actor=user.id,
            event="auth.invitation_accepted",
            entity={"invitation_id": str(invitation.id), "user_id": str(user.id)},
        )
    )
    _start_session(response, session, user)
    session.commit()
    return _me_payload(user, org)
