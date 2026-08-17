"""Minimal iyzico client for subscription checkout (Turkish market).

Implements the documented IYZWSv2 HMAC request signing and the three
subscription-checkout calls we need: initialize the hosted checkout form,
retrieve its result by token (the authoritative activation check — we never
trust the browser redirect alone), and cancel.

Configuration: `iyzico_api_key` / `iyzico_secret_key` (sandbox keys work
against the default `iyzico_base_url`), and `iyzico_plan_refs` mapping our
plan ids to pricing-plan reference codes created in the iyzico dashboard.
"""

import base64
import hashlib
import hmac
import json
import uuid

import httpx

from senthire.config import get_settings


class IyzicoError(RuntimeError):
    pass


def auth_headers(
    api_key: str, secret_key: str, random_key: str, path: str, body_json: str
) -> dict[str, str]:
    """IYZWSv2 signing: HMAC-SHA256(secret, randomKey + path + body) in hex,
    wrapped as base64("apiKey:...&randomKey:...&signature:...")."""
    payload = f"{random_key}{path}{body_json}"
    signature = hmac.new(
        secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = f"apiKey:{api_key}&randomKey:{random_key}&signature:{signature}"
    return {
        "Authorization": "IYZWSv2 " + base64.b64encode(authorization.encode()).decode(),
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }


class IyzicoClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.iyzico_api_key or not settings.iyzico_secret_key:
            raise IyzicoError("iyzico API keys are not configured")
        self._api_key = settings.iyzico_api_key
        self._secret_key = settings.iyzico_secret_key
        self._base_url = settings.iyzico_base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        body_json = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = auth_headers(
            self._api_key, self._secret_key, uuid.uuid4().hex, path, body_json
        )
        response = httpx.request(
            method,
            self._base_url + path,
            content=body_json or None,
            headers=headers,
            timeout=20,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise IyzicoError(f"iyzico returned non-JSON (HTTP {response.status_code})") from exc
        if response.status_code >= 400 or data.get("status") != "success":
            raise IyzicoError(
                data.get("errorMessage") or f"iyzico error (HTTP {response.status_code})"
            )
        return data

    def initialize_subscription_checkout(
        self,
        pricing_plan_ref: str,
        callback_url: str,
        conversation_id: str,
        customer: dict,
    ) -> dict:
        """Returns {'token': ..., 'checkoutFormContent': '<script..'} on success."""
        data = self._request(
            "POST",
            "/v2/subscription/checkoutform/initialize",
            {
                "locale": "tr",
                "conversationId": conversation_id,
                "callbackUrl": callback_url,
                "pricingPlanReferenceCode": pricing_plan_ref,
                "subscriptionInitialStatus": "ACTIVE",
                "customer": customer,
            },
        )
        return {
            "token": data.get("token"),
            "checkout_form_content": data.get("checkoutFormContent"),
        }

    def retrieve_checkout(self, token: str) -> dict:
        """Authoritative post-checkout state, queried server-to-server by token."""
        data = self._request("GET", f"/v2/subscription/checkoutform/{token}")
        payload = data.get("data") or {}
        return {
            "subscription_status": payload.get("subscriptionStatus"),
            "reference_code": payload.get("referenceCode"),
        }

    def cancel_subscription(self, reference_code: str) -> None:
        self._request(
            "POST", f"/v2/subscription/subscriptions/{reference_code}/cancel", {}
        )
