"""Auth building blocks (pure, no DB) and endpoint guards that fire pre-DB."""

from fastapi.testclient import TestClient

from senthire.api.app import create_app
from senthire.services import auth as auth_service


def test_password_hash_roundtrip():
    h = auth_service.hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert auth_service.verify_password("correct horse battery", h)
    assert not auth_service.verify_password("wrong password", h)


def test_verify_password_handles_missing_and_garbage_hashes():
    assert not auth_service.verify_password("anything", None)
    assert not auth_service.verify_password("anything", "")
    assert not auth_service.verify_password("anything", "not-an-argon2-hash")


def test_tokens_are_unique_and_hash_is_deterministic():
    a, b = auth_service.new_token(), auth_service.new_token()
    assert a != b
    assert len(a) >= 32
    assert auth_service.hash_token(a) == auth_service.hash_token(a)
    assert auth_service.hash_token(a) != auth_service.hash_token(b)
    # only the hash ever reaches the database
    assert a not in auth_service.hash_token(a)


def test_session_cookie_is_httponly_lax():
    kwargs = auth_service.session_cookie_kwargs()
    assert kwargs["httponly"] is True
    assert kwargs["samesite"] == "lax"
    assert kwargs["key"] == "senthire_session"


def test_protected_endpoints_reject_anonymous_requests():
    client = TestClient(create_app())
    # no cookie, no dev key configured -> 401 before any DB access
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/org/members").status_code == 401


def test_signup_validation_rejects_short_password_and_bad_email():
    client = TestClient(create_app())
    base = {"company_name": "Acme", "name": "Ada", "email": "ada@acme.com"}
    assert (
        client.post("/api/v1/auth/signup", json={**base, "password": "short"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/signup",
            json={**base, "email": "not-an-email", "password": "long enough"},
        ).status_code
        == 422
    )
