"""Billing: CV-volume plans, usage, and iyzico subscription checkout.

Flow (provider "iyzico"): admin saves billing details once, POST /billing/checkout
initializes the hosted iyzico form (rendered by the web app), iyzico redirects the
browser to /billing/callback with a token, and we confirm server-to-server via
retrieve-by-token before activating — the redirect itself is never trusted.
Provider "mock" (default in dev) activates instantly with no payment.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, require_admin
from senthire.billing import service as billing
from senthire.billing.iyzico import IyzicoClient, IyzicoError
from senthire.billing.plans import PLANS, TRIAL_PLAN_ID, current_period, get_plan
from senthire.config import get_settings
from senthire.db.models import AuditLog, Organization, Subscription, User

router = APIRouter(tags=["billing"])

BILLING_DETAIL_FIELDS = ("company_title", "tax_number", "tax_office", "address", "city")


class BillingDetailsIn(BaseModel):
    company_title: str = Field(min_length=2, max_length=200)
    tax_number: str = Field(min_length=10, max_length=11)
    tax_office: str = Field(min_length=2, max_length=100)
    address: str = Field(min_length=5, max_length=300)
    city: str = Field(min_length=2, max_length=60)


class CheckoutIn(BaseModel):
    plan_id: str


def _plan_out(plan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "monthly_price_try": plan.monthly_price_try,
        "cv_quota_per_month": plan.cv_quota_per_month,
    }


@router.get("/billing")
def billing_info(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> dict:
    org = session.get(Organization, user.org_id)
    sub = billing.get_subscription(session, org.id)
    plan = billing.active_plan(session, org.id)
    used = billing.get_usage(session, org.id)
    return {
        "plan": _plan_out(plan),
        "status": sub.status if sub else "trial",
        "provider": sub.provider if sub else None,
        "usage": {
            "period": current_period(),
            "used": used,
            "quota": plan.cv_quota_per_month,
            "remaining": max(0, plan.cv_quota_per_month - used),
        },
        "catalog": [_plan_out(p) for p in PLANS],
        "billing_details": (org.settings or {}).get("billing"),
        "provider_mode": get_settings().billing_provider,
    }


@router.put("/billing/details")
def save_billing_details(
    payload: BillingDetailsIn,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    org = session.get(Organization, admin.org_id)
    details = payload.model_dump()
    org.settings = {**(org.settings or {}), "billing": details}
    session.add(
        AuditLog(
            org_id=org.id,
            actor=admin.id,
            event="billing.details_updated",
            entity={"org_id": str(org.id)},
        )
    )
    session.commit()
    return {"billing_details": details}


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name or "-", "-"
    return " ".join(parts[:-1]), parts[-1]


@router.post("/billing/checkout")
def checkout(
    payload: CheckoutIn,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    plan = get_plan(payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="unknown plan")
    if plan.id == TRIAL_PLAN_ID:
        raise HTTPException(status_code=422, detail="the trial plan cannot be purchased")
    sub = billing.get_subscription(session, admin.org_id)
    if sub is not None and sub.status == "active" and sub.plan_id == plan.id:
        raise HTTPException(status_code=409, detail="this plan is already active")

    settings = get_settings()
    org = session.get(Organization, admin.org_id)

    if settings.billing_provider != "iyzico":
        billing.activate(session, org.id, plan.id, provider="mock", provider_ref=None)
        session.add(
            AuditLog(
                org_id=org.id,
                actor=admin.id,
                event="billing.activated",
                entity={"plan_id": plan.id},
                detail={"provider": "mock"},
            )
        )
        session.commit()
        return {"mode": "mock", "status": "active", "plan": _plan_out(plan)}

    plan_ref = settings.iyzico_plan_refs.get(plan.id)
    if not plan_ref:
        raise HTTPException(
            status_code=409,
            detail=f"iyzico pricing-plan reference for '{plan.id}' is not configured",
        )
    details = (org.settings or {}).get("billing")
    if not details or any(not details.get(f) for f in BILLING_DETAIL_FIELDS):
        raise HTTPException(status_code=409, detail="billing details are required first")

    name, surname = _split_name(admin.name or admin.email)
    address = {
        "contactName": details["company_title"],
        "city": details["city"],
        "country": "Turkey",
        "address": details["address"],
        "zipCode": "00000",
    }
    try:
        client = IyzicoClient()
        result = client.initialize_subscription_checkout(
            pricing_plan_ref=plan_ref,
            callback_url=f"{settings.app_base_url}/api/v1/billing/callback",
            conversation_id=str(org.id),
            customer={
                "name": name,
                "surname": surname,
                "email": admin.email,
                "identityNumber": details["tax_number"],
                "billingAddress": address,
                "shippingAddress": address,
            },
        )
    except IyzicoError as exc:
        raise HTTPException(status_code=502, detail=f"iyzico: {exc}") from exc

    billing.upsert_pending_checkout(
        session, org.id, plan.id, provider="iyzico", token=result["token"]
    )
    session.add(
        AuditLog(
            org_id=org.id,
            actor=admin.id,
            event="billing.checkout_started",
            entity={"plan_id": plan.id},
            detail={"provider": "iyzico"},
        )
    )
    session.commit()
    return {
        "mode": "iyzico",
        "token": result["token"],
        "checkout_html": result["checkout_form_content"],
    }


async def _handle_callback(request: Request, session: Session) -> RedirectResponse:
    """iyzico redirects the buyer's browser here; unauthenticated by design.

    Activation only happens after a server-to-server retrieve with our API
    keys confirms the subscription — a forged redirect cannot activate anything.
    """
    settings = get_settings()
    token = request.query_params.get("token")
    if token is None and request.method == "POST":
        form = await request.form()
        token = form.get("token")
    fail = RedirectResponse(f"{settings.app_base_url}/billing?checkout=failed", 303)
    if not token:
        return fail

    sub = session.scalar(
        select(Subscription).where(
            Subscription.provider_ref == token, Subscription.status == "pending_checkout"
        )
    )
    if sub is None:
        return fail
    try:
        state = IyzicoClient().retrieve_checkout(token)
    except IyzicoError:
        return fail
    if state["subscription_status"] != "ACTIVE":
        session.add(
            AuditLog(
                org_id=sub.org_id,
                actor=None,
                event="billing.checkout_failed",
                entity={"plan_id": sub.plan_id},
                detail={"subscription_status": state["subscription_status"]},
            )
        )
        session.commit()
        return fail

    billing.activate(
        session, sub.org_id, sub.plan_id, provider="iyzico",
        provider_ref=state["reference_code"],
    )
    session.add(
        AuditLog(
            org_id=sub.org_id,
            actor=None,
            event="billing.activated",
            entity={"plan_id": sub.plan_id},
            detail={"provider": "iyzico"},
        )
    )
    session.commit()
    return RedirectResponse(f"{settings.app_base_url}/billing?checkout=success", 303)


@router.get("/billing/callback")
async def billing_callback_get(
    request: Request, session: Session = Depends(get_db)
) -> RedirectResponse:
    return await _handle_callback(request, session)


@router.post("/billing/callback")
async def billing_callback_post(
    request: Request, session: Session = Depends(get_db)
) -> RedirectResponse:
    return await _handle_callback(request, session)


@router.post("/billing/cancel")
def cancel_subscription(
    admin: User = Depends(require_admin), session: Session = Depends(get_db)
) -> dict:
    sub = billing.get_subscription(session, admin.org_id)
    if sub is None or sub.status not in {"active", "past_due"}:
        raise HTTPException(status_code=409, detail="no active subscription to cancel")
    if sub.provider == "iyzico" and sub.provider_ref:
        try:
            IyzicoClient().cancel_subscription(sub.provider_ref)
        except IyzicoError as exc:
            # keep local state unchanged so a retry stays possible
            raise HTTPException(status_code=502, detail=f"iyzico: {exc}") from exc
    sub.status = "canceled"
    sub.canceled_at = datetime.now(UTC)
    sub.updated_at = sub.canceled_at
    session.add(
        AuditLog(
            org_id=admin.org_id,
            actor=admin.id,
            event="billing.canceled",
            entity={"plan_id": sub.plan_id},
        )
    )
    session.commit()
    return {"status": "canceled"}


@router.post("/billing/webhook/{token}")
async def billing_webhook(
    token: str, request: Request, session: Session = Depends(get_db)
) -> dict:
    """Renewal notifications. Auth = unguessable path token from config; the
    handler is deliberately conservative: it only flips active <-> past_due for
    a subscription we can match by iyzico reference code."""
    settings = get_settings()
    if not settings.billing_webhook_token or token != settings.billing_webhook_token:
        raise HTTPException(status_code=404, detail="not found")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None

    event_type = str(payload.get("iyziEventType", "")).lower()
    reference = payload.get("subscriptionReferenceCode") or (
        (payload.get("data") or {}).get("subscriptionReferenceCode")
        if isinstance(payload.get("data"), dict)
        else None
    )
    sub = None
    if reference:
        sub = session.scalar(
            select(Subscription).where(Subscription.provider_ref == reference)
        )
    if sub is not None:
        if "fail" in event_type:
            sub.status = "past_due"
        elif "success" in event_type or "renew" in event_type:
            sub.status = "active"
        sub.updated_at = datetime.now(UTC)
        session.add(
            AuditLog(
                org_id=sub.org_id,
                actor=None,
                event="billing.webhook",
                entity={"subscription_id": str(sub.id)},
                detail={"event_type": event_type},
            )
        )
        session.commit()
    return {"ok": True}
