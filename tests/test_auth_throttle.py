"""The auth surface must pace abuse: failures lock the account (even against
the correct password), request floods hit per-IP ceilings, and success
resets the meter. Counters live in the database and hold no raw addresses.

Needs Postgres; skipped when SENTHIRE_TEST_DATABASE_URL is unset.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run auth throttle tests"
)


@pytest.fixture(scope="module")
def app_client():
    name = f"senthire_throttle_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name

    patcher = pytest.MonkeyPatch()
    patcher.setenv("SENTHIRE_DATABASE_URL", url)
    from senthire.config import get_settings

    get_settings.cache_clear()
    from senthire.db.session import get_engine, get_sessionmaker

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    import senthire.api.routes.auth as auth_routes

    sent_mail: list[tuple] = []
    patcher.setattr(auth_routes, "enqueue_mail", lambda *a, **k: sent_mail.append(a))

    from starlette.testclient import TestClient

    from senthire.api.app import create_app

    client = TestClient(create_app())
    client.__enter__()
    yield client, sent_mail
    client.__exit__(None, None, None)
    patcher.undo()

    get_engine().dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    """Every test declares its own knobs; the cache never leaks between them.
    The account-lock tests raise the per-IP ceiling out of the way, because
    TestClient traffic all comes from one 'address'."""
    from senthire.config import get_settings

    monkeypatch.setenv("SENTHIRE_THROTTLE_LOGIN_ATTEMPTS_PER_IP", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def fresh_user(client, password="Parola-123456"):
    email = f"kisi-{uuid.uuid4().hex[:10]}@ornek.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": password,
            "name": "Test Kişi",
            "company_name": "Throttle A.Ş.",
        },
    )
    assert response.status_code == 201, response.text
    client.cookies.clear()
    return email, password


def login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def clear_ip_scope(kind: str) -> None:
    """The module shares one database and TestClient has one 'address'; a test
    that lowers an IP ceiling must not inherit earlier tests' traffic."""
    from senthire.db.session import get_sessionmaker
    from senthire.services import throttle

    session = get_sessionmaker()()
    try:
        throttle.clear(session, throttle.scope_for(kind, "testclient"))
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# window mechanics (service level)
# --------------------------------------------------------------------------- #


def test_window_counts_rolls_over_and_clears(app_client):
    from senthire.db.session import get_sessionmaker
    from senthire.services import throttle

    session = get_sessionmaker()()
    try:
        scope = throttle.scope_for("unit", uuid.uuid4().hex)
        now = datetime.now(UTC)
        for expected in (1, 2, 3):
            decision = throttle.hit(session, scope, limit=3, window_seconds=60, now=now)
            assert decision.count == expected and decision.allowed
        over = throttle.hit(session, scope, limit=3, window_seconds=60, now=now)
        assert not over.allowed and over.retry_after_seconds >= 1

        later = now + timedelta(seconds=61)
        rolled = throttle.hit(session, scope, limit=3, window_seconds=60, now=later)
        assert rolled.count == 1 and rolled.allowed, "an expired window starts over"

        throttle.clear(session, scope)
        session.commit()
        assert throttle.peek(session, scope, limit=3, window_seconds=60).count == 0
    finally:
        session.close()


def test_scopes_never_store_the_raw_address(app_client):
    from senthire.services import throttle

    scope = throttle.scope_for("login:email", "Gizli.Adres@ornek.com")
    assert "gizli" not in scope.lower() and "@" not in scope
    assert scope == throttle.scope_for("login:email", "  gizli.adres@ORNEK.com ")


# --------------------------------------------------------------------------- #
# login lockout
# --------------------------------------------------------------------------- #


def test_login_failures_lock_the_account_even_for_the_right_password(app_client):
    client, _ = app_client
    email, password = fresh_user(client)

    for _ in range(5):
        assert login(client, email, "yanlis-parola").status_code == 401

    locked = login(client, email, password)
    assert locked.status_code == 429, "the lock must hold against the correct password too"
    assert int(locked.headers["Retry-After"]) > 0
    assert "dakika" in locked.json()["detail"]

    # an unrelated account is untouched
    other_email, other_password = fresh_user(client)
    assert login(client, other_email, other_password).status_code == 200


def test_successful_login_resets_the_failure_meter(app_client):
    client, _ = app_client
    email, password = fresh_user(client)

    for _ in range(4):
        assert login(client, email, "yanlis-parola").status_code == 401
    assert login(client, email, password).status_code == 200
    client.cookies.clear()

    for _ in range(5):
        assert login(client, email, "yanlis-parola").status_code == 401
    assert login(client, email, password).status_code == 429


def test_ip_ceiling_paces_spraying_across_accounts(app_client, monkeypatch):
    from senthire.config import get_settings

    monkeypatch.setenv("SENTHIRE_THROTTLE_LOGIN_ATTEMPTS_PER_IP", "3")
    get_settings.cache_clear()
    client, _ = app_client
    clear_ip_scope("login:ip")

    for index in range(3):
        assert login(client, f"yok-{index}@ornek.com", "x" * 12).status_code == 401
    assert login(client, "yok-3@ornek.com", "x" * 12).status_code == 429


# --------------------------------------------------------------------------- #
# forgot / reset / signup / change-password pacing
# --------------------------------------------------------------------------- #


def test_forgot_password_paces_per_address(app_client):
    client, sent_mail = app_client
    email, _ = fresh_user(client)
    before = len(sent_mail)

    for _ in range(3):
        response = client.post("/api/v1/auth/forgot-password", json={"email": email})
        assert response.status_code == 200 and response.json() == {"ok": True}
    over = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert over.status_code == 429
    assert len(sent_mail) == before + 3, "the refused request must not send mail"


def test_reset_token_guessing_hits_the_ip_ceiling(app_client, monkeypatch):
    from senthire.config import get_settings

    monkeypatch.setenv("SENTHIRE_THROTTLE_RESET_ATTEMPTS_PER_IP", "3")
    get_settings.cache_clear()
    client, _ = app_client
    clear_ip_scope("reset:ip")

    for _ in range(3):
        response = client.post(
            "/api/v1/auth/password-resets/kestirilen-token",
            json={"password": "Yepyeni-Parola1"},
        )
        assert response.status_code in (400, 404), response.status_code
    over = client.post(
        "/api/v1/auth/password-resets/kestirilen-token",
        json={"password": "Yepyeni-Parola1"},
    )
    assert over.status_code == 429


def test_signup_burst_hits_the_ip_ceiling(app_client, monkeypatch):
    from senthire.config import get_settings

    monkeypatch.setenv("SENTHIRE_THROTTLE_SIGNUP_PER_IP", "2")
    get_settings.cache_clear()
    client, _ = app_client
    clear_ip_scope("signup:ip")

    fresh_user(client)
    fresh_user(client)
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"taskin-{uuid.uuid4().hex[:8]}@ornek.com",
            "password": "Parola-123456",
            "name": "Taşkın",
            "company_name": "Taşkın A.Ş.",
        },
    )
    assert response.status_code == 429


def test_change_password_failures_lock_and_success_clears(app_client):
    client, _ = app_client
    email, password = fresh_user(client)
    assert login(client, email, password).status_code == 200

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "yanlis-mevcut", "new_password": "Yeni-Parola-123"},
        )
        assert response.status_code == 403
    locked = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password, "new_password": "Yeni-Parola-123"},
    )
    assert locked.status_code == 429, "the lock must hold against the correct password too"

    # counters are scoped per account: a fresh user changes freely
    client.cookies.clear()
    email2, password2 = fresh_user(client)
    assert login(client, email2, password2).status_code == 200
    ok = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": password2, "new_password": "Yeni-Parola-456"},
    )
    assert ok.status_code == 200


def test_throttle_rows_carry_only_hashes(app_client):
    from senthire.db.models import AuthThrottle
    from senthire.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        scopes = session.scalars(select(AuthThrottle.scope)).all()
        assert scopes, "the suite above must have produced counters"
        assert all("@" not in scope for scope in scopes)
    finally:
        session.close()
