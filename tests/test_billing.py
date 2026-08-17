"""Billing building blocks: plan catalog, quota gate, iyzico signing (pure)."""

import base64
import hashlib
import hmac

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from senthire.api.app import create_app
from senthire.billing.iyzico import auth_headers
from senthire.billing.plans import PLANS, TRIAL_PLAN_ID, current_period, get_plan
from senthire.billing.service import assert_within_quota


def test_plan_catalog_invariants():
    ids = [p.id for p in PLANS]
    assert len(ids) == len(set(ids))
    trial = get_plan(TRIAL_PLAN_ID)
    assert trial is not None and trial.monthly_price_try == 0
    paid = [p for p in PLANS if p.monthly_price_try > 0]
    assert paid, "catalog needs at least one paid plan"
    # more money must always mean more CVs
    ordered = sorted(PLANS, key=lambda p: p.monthly_price_try)
    quotas = [p.cv_quota_per_month for p in ordered]
    assert quotas == sorted(quotas) and len(set(quotas)) == len(quotas)


def test_current_period_shape():
    period = current_period()
    year, month = period.split("-")
    assert len(year) == 4 and 1 <= int(month) <= 12


def test_quota_gate_allows_up_to_the_limit_and_blocks_past_it():
    plan = get_plan(TRIAL_PLAN_ID)
    assert_within_quota(used=0, plan=plan, n_new=plan.cv_quota_per_month)
    with pytest.raises(HTTPException) as exc:
        assert_within_quota(used=plan.cv_quota_per_month - 1, plan=plan, n_new=2)
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["code"] == "cv_quota_exceeded"
    assert detail["remaining"] == 1


def test_iyzico_auth_header_signs_the_documented_payload():
    headers = auth_headers(
        api_key="api-key",
        secret_key="secret-key",
        random_key="rnd123",
        path="/v2/subscription/checkoutform/initialize",
        body_json='{"locale":"tr"}',
    )
    assert headers["x-iyzi-rnd"] == "rnd123"
    scheme, encoded = headers["Authorization"].split(" ", 1)
    assert scheme == "IYZWSv2"
    decoded = base64.b64decode(encoded).decode()
    expected_sig = hmac.new(
        b"secret-key",
        b'rnd123/v2/subscription/checkoutform/initialize{"locale":"tr"}',
        hashlib.sha256,
    ).hexdigest()
    assert decoded == f"apiKey:api-key&randomKey:rnd123&signature:{expected_sig}"


def test_billing_endpoints_require_auth():
    client = TestClient(create_app())
    assert client.get("/api/v1/billing").status_code == 401
    assert (
        client.post("/api/v1/billing/checkout", json={"plan_id": "baslangic"}).status_code
        == 401
    )
    # webhook path token unset in tests -> route hides itself
    assert client.post("/api/v1/billing/webhook/guess", json={}).status_code == 404
