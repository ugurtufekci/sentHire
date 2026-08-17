"""Workspace administration: members and invitations.

Any member can see who is in the workspace; only admins can invite, revoke
invitations, change roles, or deactivate accounts. Creating an invitation
emails the invitee and returns the link to the admin as a fallback; only the
token hash is stored, so "resend" rotates the token (old link dies).
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, parse_uuid, require_admin
from senthire.config import get_settings
from senthire.db.models import AuditLog, Invitation, Organization, User
from senthire.services import auth as auth_service
from senthire.services.email import invitation_email
from senthire.workers.tasks.mail import enqueue_mail

router = APIRouter(tags=["team"])

INVITABLE_ROLES = {"admin", "member"}


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "member"


class MemberPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


def _member_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _invitation_out(invitation: Invitation) -> dict:
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "role": invitation.role,
        "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
        "expires_at": invitation.expires_at.isoformat(),
    }


def _pending_invitations(session: Session, org_id) -> list[Invitation]:
    return list(
        session.scalars(
            select(Invitation)
            .where(
                Invitation.org_id == org_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > datetime.now(UTC),
            )
            .order_by(Invitation.created_at.desc())
        )
    )


@router.get("/org")
def org_info(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> dict:
    org = session.get(Organization, user.org_id)
    active_members = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.org_id == org.id, User.is_active.is_(True))
    )
    return {
        "id": str(org.id),
        "name": org.name,
        "seat_limit": org.seat_limit,
        "active_members": active_members,
        "pending_invitations": len(_pending_invitations(session, org.id)),
    }


@router.get("/org/members")
def list_members(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> list[dict]:
    members = session.scalars(
        select(User).where(User.org_id == user.org_id).order_by(User.created_at)
    )
    return [_member_out(m) for m in members]


@router.get("/org/invitations")
def list_invitations(
    admin: User = Depends(require_admin), session: Session = Depends(get_db)
) -> list[dict]:
    return [_invitation_out(i) for i in _pending_invitations(session, admin.org_id)]


@router.post("/org/invitations", status_code=201)
def create_invitation(
    payload: InviteIn,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    if payload.role not in INVITABLE_ROLES:
        raise HTTPException(status_code=422, detail="role must be admin or member")
    if session.scalar(select(User).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="this email already has an account")

    org = session.get(Organization, admin.org_id)
    pending = _pending_invitations(session, org.id)
    if any(i.email.lower() == payload.email.lower() for i in pending):
        raise HTTPException(status_code=409, detail="this email already has a pending invitation")
    if org.seat_limit is not None:
        active = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.org_id == org.id, User.is_active.is_(True))
        )
        if active + len(pending) >= org.seat_limit:
            raise HTTPException(status_code=409, detail="organization seat limit reached")

    settings = get_settings()
    token = auth_service.new_token()
    invitation = Invitation(
        org_id=org.id,
        email=payload.email,
        role=payload.role,
        token_hash=auth_service.hash_token(token),
        invited_by=admin.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.invitation_ttl_days),
    )
    session.add(invitation)
    session.flush()
    session.add(
        AuditLog(
            org_id=org.id,
            actor=admin.id,
            event="team.invitation_created",
            entity={"invitation_id": str(invitation.id)},
            detail={"role": payload.role},
        )
    )
    session.commit()
    invite_url = f"{settings.app_base_url}/join/{token}"
    email_queued = _send_invitation(org, admin, invitation.email, invite_url, settings)
    # The raw link also comes back to the admin as a fallback (spam folders happen).
    return {
        **_invitation_out(invitation),
        "invite_url": invite_url,
        "email_queued": email_queued,
    }


def _send_invitation(org, admin: User, email: str, invite_url: str, settings) -> bool:
    subject, html, text = invitation_email(
        org_name=org.name,
        inviter_name=admin.name or admin.email,
        invite_url=invite_url,
        expires_days=settings.invitation_ttl_days,
    )
    return enqueue_mail(email, subject, html, text)


@router.post("/org/invitations/{invitation_id}/resend")
def resend_invitation(
    invitation_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    """Rotate the token (invalidating the old link) and email a fresh one."""
    invitation = session.get(Invitation, parse_uuid(invitation_id, "invitation_id"))
    if invitation is None or invitation.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="invitation not found")
    if invitation.accepted_at is not None or invitation.revoked_at is not None:
        raise HTTPException(status_code=409, detail="invitation is no longer pending")

    settings = get_settings()
    token = auth_service.new_token()
    invitation.token_hash = auth_service.hash_token(token)
    invitation.expires_at = datetime.now(UTC) + timedelta(days=settings.invitation_ttl_days)
    org = session.get(Organization, admin.org_id)
    session.add(
        AuditLog(
            org_id=admin.org_id,
            actor=admin.id,
            event="team.invitation_resent",
            entity={"invitation_id": str(invitation.id)},
        )
    )
    session.commit()
    invite_url = f"{settings.app_base_url}/join/{token}"
    email_queued = _send_invitation(org, admin, invitation.email, invite_url, settings)
    return {
        **_invitation_out(invitation),
        "invite_url": invite_url,
        "email_queued": email_queued,
    }


@router.delete("/org/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> None:
    invitation = session.get(Invitation, parse_uuid(invitation_id, "invitation_id"))
    if invitation is None or invitation.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="invitation not found")
    if invitation.accepted_at is not None:
        raise HTTPException(status_code=409, detail="invitation already used")
    if invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC)
        session.add(
            AuditLog(
                org_id=admin.org_id,
                actor=admin.id,
                event="team.invitation_revoked",
                entity={"invitation_id": str(invitation.id)},
            )
        )
        session.commit()


@router.patch("/org/members/{user_id}")
def update_member(
    user_id: str,
    payload: MemberPatch,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    target = session.get(User, parse_uuid(user_id, "user_id"))
    if target is None or target.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="member not found")
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="you cannot change your own account here")

    demoting = payload.role is not None and payload.role != "admin" and target.role == "admin"
    deactivating = payload.is_active is False and target.is_active
    if (demoting or deactivating) and target.role == "admin":
        other_admins = session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.org_id == admin.org_id,
                User.role == "admin",
                User.is_active.is_(True),
                User.id != target.id,
            )
        )
        if other_admins == 0:
            raise HTTPException(status_code=409, detail="workspace needs at least one admin")

    changes: dict = {}
    if payload.role is not None:
        if payload.role not in INVITABLE_ROLES:
            raise HTTPException(status_code=422, detail="role must be admin or member")
        if payload.role != target.role:
            changes["role"] = {"from": target.role, "to": payload.role}
            target.role = payload.role
    if payload.is_active is not None and payload.is_active != target.is_active:
        changes["is_active"] = {"from": target.is_active, "to": payload.is_active}
        target.is_active = payload.is_active
        if not payload.is_active:
            auth_service.revoke_all_sessions(session, target.id)

    if changes:
        session.add(
            AuditLog(
                org_id=admin.org_id,
                actor=admin.id,
                event="team.member_updated",
                entity={"user_id": str(target.id)},
                detail=changes,
            )
        )
        session.commit()
    return _member_out(target)
