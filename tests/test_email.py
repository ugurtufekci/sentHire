"""Email rendering + console delivery (pure, no network) and pre-DB endpoint guards."""

from fastapi.testclient import TestClient

from senthire.api.app import create_app
from senthire.services.email import invitation_email, password_reset_email, send_email


def test_invitation_email_carries_link_in_both_bodies():
    subject, html, text = invitation_email(
        org_name="Aksa Teknoloji",
        inviter_name="Ayşe Demir",
        invite_url="http://localhost:3000/join/tok123",
        expires_days=7,
    )
    assert "Aksa Teknoloji" in subject
    for body in (html, text):
        assert "http://localhost:3000/join/tok123" in body
        assert "Ayşe Demir" in body
    assert "7 gün" in text


def test_password_reset_email_carries_link_and_ttl():
    subject, html, text = password_reset_email(
        reset_url="http://localhost:3000/reset-password/tok456", ttl_minutes=60
    )
    assert "sentHire" in subject
    for body in (html, text):
        assert "http://localhost:3000/reset-password/tok456" in body
    assert "60 dakika" in text


def test_console_backend_prints_instead_of_connecting(capsys):
    send_email("someone@example.com", "Subject here", "<p>html</p>", "plain body")
    out = capsys.readouterr().out
    assert "someone@example.com" in out
    assert "Subject here" in out
    assert "plain body" in out


def test_reset_endpoints_validate_before_db():
    client = TestClient(create_app())
    assert (
        client.post("/api/v1/auth/forgot-password", json={"email": "not-an-email"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/password-resets/some-token", json={"password": "short"}
        ).status_code
        == 422
    )
