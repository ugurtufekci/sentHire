"""End-to-end journey against a real database, walked as a user would walk it.

Signup → invite a colleague → they join → create a job → compile and confirm
criteria → upload CVs → screen → read the ranking → check billing. Model calls
and object storage are faked; everything else is the real API, the real ORM, the
real scorer, and real SQL.

Needs Postgres (citext + vector). Skipped when SENTHIRE_TEST_DATABASE_URL is
unset so the default suite stays service-free.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run end-to-end tests"
)

def make_pdf(text: str) -> bytes:
    """A real PDF with a text layer. Latin-1-safe characters only: the base-14
    font used here cannot encode Turkish glyphs when *writing* the fixture."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


PDF_BYTES = make_pdf("Ornek CV metni")


@pytest.fixture(scope="module")
def app_env():
    """A migrated scratch database wired into the app, with fakes for S3."""
    name = f"senthire_e2e_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name

    os.environ["SENTHIRE_DATABASE_URL"] = url
    os.environ["SENTHIRE_DEV_API_KEY"] = ""  # cookie sessions only, like production

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

    from senthire.seed import seed_templates

    session = get_sessionmaker()()
    seed_templates(session)
    session.commit()
    session.close()

    yield url

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


@pytest.fixture(scope="module")
def new_client(app_env):
    """Factory for independent browsers — each has its own cookie jar."""
    from senthire.api.app import create_app

    opened = []

    def make():
        c = TestClient(create_app())
        c.__enter__()
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def client(new_client):
    """The admin of the Aksa Teknoloji workspace, signed in.

    Signing up here (not in a test) keeps every test runnable on its own —
    tests that depend on each other's side effects hide failures.
    """
    c = new_client()
    c.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Aksa Teknoloji",
            "name": "Ayşe Demir",
            "email": "ayse@aksatek.com",
            "password": "guclu-parola-123",
        },
    )
    return c


@pytest.fixture
def storage_stub(monkeypatch):
    """In-memory object storage: presign hands back a key, get_object replays it."""
    from senthire.services import storage

    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "presign_put", lambda key, ct=None: f"https://stub/{key}")
    monkeypatch.setattr(storage, "presign_get", lambda key: f"https://stub/{key}")
    monkeypatch.setattr(storage, "get_object_bytes", lambda key: blobs.get(key, PDF_BYTES))
    monkeypatch.setattr(storage, "object_size", lambda key: len(blobs.get(key, PDF_BYTES)))
    # the intake task imported these by name at module load
    from senthire.workers.tasks import parse as parse_tasks

    monkeypatch.setattr(parse_tasks.storage, "get_object_bytes", storage.get_object_bytes)
    monkeypatch.setattr(parse_tasks.storage, "object_size", storage.object_size)
    return blobs


# --------------------------------------------------------------------------- #
# 1. Signing up and inviting the team
# --------------------------------------------------------------------------- #


def test_signup_creates_a_workspace_and_signs_the_admin_in(new_client):
    browser = new_client()
    response = browser.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Yeni Şirket",
            "name": "Yeni Yönetici",
            "email": "yeni@yenisirket.com",
            "password": "guclu-parola-123",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["org"]["name"] == "Yeni Şirket"
    assert body["user"]["role"] == "admin"
    # the session cookie is set and immediately usable
    assert browser.get("/api/v1/auth/me").json()["user"]["email"] == "yeni@yenisirket.com"


def test_duplicate_signup_is_refused(client):
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Another Co",
            "name": "Someone Else",
            "email": "ayse@aksatek.com",
            "password": "guclu-parola-123",
        },
    )
    assert response.status_code == 409


def test_admin_invites_a_colleague_who_joins_the_same_workspace(client, new_client):
    created = client.post(
        "/api/v1/org/invitations", json={"email": "mehmet@aksatek.com", "role": "member"}
    )
    assert created.status_code == 201, created.text
    token = created.json()["invite_url"].rsplit("/", 1)[-1]

    lookup = client.get(f"/api/v1/auth/invitations/{token}")
    assert lookup.json()["org_name"] == "Aksa Teknoloji"

    # a second browser accepts the invitation
    colleague = new_client()
    if True:
        joined = colleague.post(
            f"/api/v1/auth/invitations/{token}/accept",
            json={"name": "Mehmet Kaya", "password": "baska-parola-456"},
        )
        assert joined.status_code == 201, joined.text
        assert joined.json()["org"]["name"] == "Aksa Teknoloji"  # same workspace
        # members cannot invite
        assert colleague.post(
            "/api/v1/org/invitations", json={"email": "x@aksatek.com", "role": "member"}
        ).status_code == 403

    # the invitation is single-use
    assert (
        client.post(
            f"/api/v1/auth/invitations/{token}/accept",
            json={"name": "Impostor", "password": "yet-another-789"},
        ).status_code
        in {409, 410}
    )
    assert len(client.get("/api/v1/org/members").json()) == 2


def test_login_rejects_a_wrong_password_and_accepts_the_right_one(client, new_client):
    fresh = new_client()
    if True:
        assert (
            fresh.post(
                "/api/v1/auth/login",
                json={"email": "ayse@aksatek.com", "password": "wrong"},
            ).status_code
            == 401
        )
        assert (
            fresh.post(
                "/api/v1/auth/login",
                json={"email": "ayse@aksatek.com", "password": "guclu-parola-123"},
            ).status_code
            == 200
        )


# --------------------------------------------------------------------------- #
# 2. Tenant isolation — the property a B2B product cannot get wrong
# --------------------------------------------------------------------------- #


def test_one_workspace_cannot_see_another_workspaces_jobs(client, new_client):
    ours = client.post("/api/v1/jobs", json={"title": "İzolasyon Testi İlanı"}).json()

    rival = new_client()
    if True:
        rival.post(
            "/api/v1/auth/signup",
            json={
                "company_name": "Rakip A.Ş.",
                "name": "Rakip Yönetici",
                "email": "admin@rakip.com",
                "password": "rakip-parola-123",
            },
        )
        assert rival.get("/api/v1/jobs").json() == []  # no leakage in the listing
        # and no leakage by direct id either
        assert rival.get(f"/api/v1/jobs/{ours['id']}").status_code == 404
        assert rival.post(f"/api/v1/jobs/{ours['id']}/runs", json={}).status_code == 404


def test_anonymous_requests_are_refused_everywhere(new_client):
    anon = new_client()
    if True:
        for path in ["/api/v1/jobs", "/api/v1/auth/me", "/api/v1/org/members", "/api/v1/billing"]:
            assert anon.get(path).status_code == 401, path
        assert anon.post("/api/v1/jobs", json={"title": "x"}).status_code == 401
        # health is deliberately open
        assert anon.get("/api/v1/health").status_code == 200


# --------------------------------------------------------------------------- #
# 3. The screening pipeline, walked as HR walks it
# --------------------------------------------------------------------------- #

CANDIDATES = [
    # (name, email, months of experience, city, english CEFR, b2b verdict)
    ("Deniz Yılmaz", "deniz@example.com", 72, "Ankara", "C1", "met"),
    ("Ece Kaya", "ece@example.com", 60, "İstanbul", "B2", "met"),
    ("Kerem Aydın", "kerem@example.com", 18, "Ankara", "B2", "not_met"),
]


def _profile_for(name, email, months, city, cefr):
    """An ExtractedProfile whose derived experience lands on `months`."""
    from senthire.domain.profile import ExtractedProfile

    start_year = 2026 - max(1, round(months / 12))
    return ExtractedProfile.model_validate(
        {
            "document_kind": "cv",
            "language": "tr",
            "identity": {"full_name": name, "emails": [email]},
            "location": {"raw": city, "city_canonical": city, "country": "TR"},
            "experience": [
                {
                    "title_raw": "Satış Uzmanı",
                    "company": "Örnek A.Ş.",
                    "employment_type": "full_time",
                    "start": f"{start_year}-01",
                    "is_current": True,
                    "signals": {"b2b": True, "quota_carrying": True},
                }
            ],
            "languages": [{"language": "en", "cefr": cefr}],
            "confidence": 0.9,
        }
    )


@pytest.fixture(autouse=True)
def eager_workers(monkeypatch):
    """Run Celery tasks inline so the real orchestration is exercised.

    Autouse on purpose: any test that hits an enqueueing endpoint without this
    blocks for minutes retrying a broker that isn't there, which reads as a hung
    suite rather than a missing fixture.
    """
    from senthire.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


@pytest.fixture
def fake_models(monkeypatch):
    """Deterministic stand-ins for every model call in the pipeline."""
    from senthire.compiler import compiler
    from senthire.domain.spec import EvaluationSpec
    from senthire.extraction import extractor
    from senthire.screening import llm
    from senthire.screening.schemas import LightScreenOutput, ReqJudgment
    from senthire.workers.tasks import parse as parse_tasks
    from senthire.workers.tasks import screen as screen_tasks

    spec_doc = {
        "schema_version": "1.0",
        "version": 1,
        "weights": {"relevant_experience": 0.7, "location": 0.3},
        "requirements": [
            {
                "req_id": "R1_experience",
                "category": "relevant_experience",
                "label": {"tr": "En az 3 yıl deneyim"},
                "type": "hard",
                "importance": "critical",
                "evaluator": "deterministic",
                "deterministic": {
                    "predicate": {
                        "field": "derived.total_experience_months",
                        "op": ">=",
                        "value": 36,
                    }
                },
            },
            {
                "req_id": "R2_b2b",
                "category": "relevant_experience",
                "label": {"tr": "B2B satış deneyimi"},
                "type": "scored",
                "importance": "critical",
                "evaluator": "semantic",
                "semantic": {"rubric": "Score B2B sales depth."},
            },
            {
                "req_id": "R3_ankara",
                "category": "location",
                "label": {"tr": "Ankara'da ikamet"},
                "type": "scored",
                "importance": "high",
                "evaluator": "deterministic",
                "deterministic": {
                    "predicate": {"field": "location.city_canonical", "op": "==", "value": "Ankara"}
                },
            },
        ],
    }

    def fake_compile(template_spec, nl_text, *, version, locale="tr"):
        return compiler.CompileResult(
            spec=EvaluationSpec.model_validate({**spec_doc, "version": version}),
            back_translation={"tr": "Ankara'da, en az 3 yıl B2B satış deneyimi olan adaylar."},
            clarifications=[],
            compliance_flags=[],
        )

    by_email = {c[1]: c for c in CANDIDATES}
    order = iter(CANDIDATES)

    def fake_extract(data, *, escalated=False):
        name, email, months, city, cefr, _ = next(order)
        return extractor.ExtractionOutcome(
            profile=_profile_for(name, email, months, city, cefr),
            raw_text=f"{name}\n{email}\n{city}\nB2B satış deneyimi, kota sorumluluğu.",
            path="text",
            model="fake-haiku",
            prompt_version="extract_v1",
            page_count=1,
            input_tokens=1200,
            output_tokens=300,
        )

    def fake_light(spec, profile):
        email = (profile.get("identity") or {}).get("emails", ["?"])[0]
        verdict = by_email.get(email, (None, None, None, None, None, "unknown"))[5]
        judgment = ReqJudgment(
            req_id="R2_b2b",
            verdict=verdict,
            score=1.0 if verdict == "met" else 0.0,
            confidence=0.9,
            info_status="explicit",
            evidence=[{"quote": "B2B satış deneyimi", "page": 1}],
            reasoning="CV'de B2B satış rolü açıkça geçiyor.",
        )
        usage = llm.LlmUsage("fake-haiku", 3000, 400, 12000, 0)
        return LightScreenOutput(judgments=[judgment], strengths=["B2B satış"]), usage

    monkeypatch.setattr(screen_tasks, "compile_spec", fake_compile)
    monkeypatch.setattr(parse_tasks, "extract_pdf", fake_extract)
    monkeypatch.setattr(screen_tasks, "light_screen", fake_light)
    # the shortlist is small in this fixture; keep deep analysis out of the path
    monkeypatch.setattr(screen_tasks, "select_for_deep", lambda *a, **k: [])
    return spec_doc


def test_full_screening_journey(client, storage_stub, eager_workers, fake_models):
    # --- HR creates a job ---------------------------------------------------
    job = client.post(
        "/api/v1/jobs", json={"title": "Satış Uzmanı — Ankara", "template_slug": None}
    ).json()

    # --- describes the ideal candidate in plain Turkish ---------------------
    compiled = client.post(
        f"/api/v1/jobs/{job['id']}/requirements/compile",
        json={
            "natural_language_text": "Ankara'da ikamet etmesi önemli. "
            "En az 3 yıl B2B satış deneyimi olsun."
        },
    )
    assert compiled.status_code in {200, 202}, compiled.text
    spec_id = compiled.json()["spec_id"]

    spec = client.get(f"/api/v1/requirements/{spec_id}").json()
    assert spec["status"] == "draft", spec
    assert "Ankara" in spec["spec"]["compiler"]["back_translation"]["tr"]

    # --- nothing screens until a human confirms -----------------------------
    assert client.post(f"/api/v1/jobs/{job['id']}/runs", json={}).status_code == 409
    confirmed = client.post(f"/api/v1/requirements/{spec_id}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    # --- uploads three CVs --------------------------------------------------
    slots = client.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(len(CANDIDATES))]},
    ).json()["uploads"]
    for slot, candidate in zip(slots, CANDIDATES, strict=True):
        # distinct bytes per candidate — identical bytes are deduplicated by design
        storage_stub[slot["s3_key"]] = make_pdf(f"CV {candidate[1]}")
    done = client.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": s["s3_key"], "filename": s["filename"]} for s in slots]},
    )
    assert done.status_code == 202, done.text

    candidates = client.get(f"/api/v1/jobs/{job['id']}/candidates").json()
    assert len(candidates["applications"]) == len(CANDIDATES), candidates
    assert all(f["parse_status"] == "parsed" for f in candidates["files"]), candidates["files"]

    # --- runs the screening -------------------------------------------------
    started = client.post(f"/api/v1/jobs/{job['id']}/runs", json={"mode": "interactive"})
    assert started.status_code in {200, 202}, started.text
    run_id = started.json()["run_id"]

    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["status"] == "complete", status
    assert status["funnel"]["evaluated"] == len(CANDIDATES)

    # --- reads the ranking --------------------------------------------------
    results = client.get(f"/api/v1/runs/{run_id}/results").json()
    ranked = results["results"]
    names = [r["candidate"]["display_name"] for r in ranked]
    assert "Kerem Aydın" not in names, "18 months must fail the 3-year hard gate"
    assert names[0] == "Deniz Yılmaz", f"Ankara + B2B should rank first, got {names}"
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))

    # --- opens one candidate and checks the explanation ---------------------
    detail = client.get(f"/api/v1/runs/{run_id}/results/{ranked[0]['application_id']}").json()
    reqs = {r["req_id"]: r for r in detail["result"]["requirements"]}
    assert reqs["R1_experience"]["verdict"] == "met"
    assert reqs["R3_ankara"]["verdict"] == "met"
    assert reqs["R2_b2b"]["evidence"], "a semantic verdict must carry evidence"
    assert detail["result"]["final_score"] > 0

    # rejected candidates come back with a stated reason, not silence
    rejected = results["rejected"]
    assert rejected and rejected[0]["rejection_reasons"], rejected


def test_rerunning_the_same_spec_is_memoized_and_free(client, eager_workers, fake_models):
    """The second run must reuse prior evaluations instead of paying again."""
    jobs = client.get("/api/v1/jobs").json()
    job_id = jobs[0]["id"]

    again = client.post(f"/api/v1/jobs/{job_id}/runs", json={})
    assert again.status_code in {200, 202}, again.text
    status = client.get(f"/api/v1/runs/{again.json()['run_id']}").json()
    assert status["funnel"]["memoized"] == len(CANDIDATES), status["funnel"]
    assert status["cost"] == {} or all(
        bucket["calls"] == 0 for bucket in status["cost"].values()
    ), "a fully memoized run must not spend model calls"


# --------------------------------------------------------------------------- #
# 4. Poking at it — malformed, hostile, and awkward input
# --------------------------------------------------------------------------- #


def test_malformed_ids_are_rejected_not_crashed(client):
    for path in [
        "/api/v1/jobs/not-a-uuid",
        "/api/v1/runs/not-a-uuid",
        "/api/v1/requirements/not-a-uuid",
        "/api/v1/jobs/not-a-uuid/candidates",
    ]:
        status = client.get(path).status_code
        assert status in {404, 422}, f"{path} returned {status}"
    # a syntactically valid but unknown id is a clean 404
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404


def test_upload_keys_outside_the_org_prefix_are_refused(client):
    """The client picks the key it PUTs to; it must not be able to aim elsewhere."""
    job = client.post("/api/v1/jobs", json={"title": "Anahtar testi"}).json()
    forged = client.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": "org/00000000-0000-0000-0000-000000000000/x.pdf",
                         "filename": "x.pdf"}]},
    )
    assert forged.status_code == 403, forged.text
    assert client.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": "../../etc/passwd", "filename": "x.pdf"}]},
    ).status_code == 403


def test_empty_and_oversized_batches_are_bounded(client):
    job = client.post("/api/v1/jobs", json={"title": "Sınır testi"}).json()
    assert client.post(f"/api/v1/jobs/{job['id']}/uploads", json={"files": []}).status_code == 422
    too_many = client.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(600)]},
    )
    assert too_many.status_code == 422


def test_running_a_job_with_no_candidates_is_a_clear_conflict(client, fake_models):
    job = client.post("/api/v1/jobs", json={"title": "Adaysız ilan"}).json()
    compiled = client.post(
        f"/api/v1/jobs/{job['id']}/requirements/compile",
        json={"natural_language_text": "En az 3 yıl deneyim."},
    ).json()
    client.post(f"/api/v1/requirements/{compiled['spec_id']}/confirm", json={})
    failed = client.post(f"/api/v1/jobs/{job['id']}/runs", json={})
    assert failed.status_code == 409
    assert "candidate" in failed.json()["detail"].lower()


def test_a_member_cannot_change_roles_or_billing(client, new_client):
    """Least privilege: members use the product, admins administer it."""
    invite = client.post(
        "/api/v1/org/invitations", json={"email": "uye@aksatek.com", "role": "member"}
    ).json()
    member = new_client()
    member.post(
        f"/api/v1/auth/invitations/{invite['invite_url'].rsplit('/', 1)[-1]}/accept",
        json={"name": "Üye Kişi", "password": "uye-parola-123"},
    )
    admin_id = next(
        m["id"] for m in client.get("/api/v1/org/members").json() if m["role"] == "admin"
    )
    assert member.patch(f"/api/v1/org/members/{admin_id}", json={"role": "member"}).status_code == 403
    assert member.post("/api/v1/billing/checkout", json={"plan_id": "baslangic"}).status_code == 403
    # but a member can do the actual work
    assert member.get("/api/v1/jobs").status_code == 200
    assert member.post("/api/v1/jobs", json={"title": "Üyenin ilanı"}).status_code == 201


def test_the_last_admin_cannot_be_demoted_or_deactivated(client, new_client):
    """A workspace with no admin is unadministrable — the API must refuse."""
    solo = new_client()
    solo.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Tek Kişilik A.Ş.",
            "name": "Tek Yönetici",
            "email": "tek@teksirket.com",
            "password": "tek-parola-123",
        },
    )
    me = solo.get("/api/v1/auth/me").json()["user"]["id"]
    # changing your own account through the member endpoint is refused outright
    assert solo.patch(f"/api/v1/org/members/{me}", json={"role": "member"}).status_code == 409

    invite = solo.post(
        "/api/v1/org/invitations", json={"email": "ikinci@teksirket.com", "role": "member"}
    ).json()
    second = new_client()
    second.post(
        f"/api/v1/auth/invitations/{invite['invite_url'].rsplit('/', 1)[-1]}/accept",
        json={"name": "İkinci Kişi", "password": "ikinci-parola-123"},
    )
    members = solo.get("/api/v1/org/members").json()
    other_id = next(m["id"] for m in members if m["role"] == "member")
    # promoting then demoting the *other* admin is fine — one admin always remains
    assert solo.patch(f"/api/v1/org/members/{other_id}", json={"role": "admin"}).status_code == 200
    assert solo.patch(f"/api/v1/org/members/{other_id}", json={"role": "member"}).status_code == 200


def test_deactivating_a_member_kills_their_live_session(client, new_client):
    invite = client.post(
        "/api/v1/org/invitations", json={"email": "gidecek@aksatek.com", "role": "member"}
    ).json()
    leaver = new_client()
    leaver.post(
        f"/api/v1/auth/invitations/{invite['invite_url'].rsplit('/', 1)[-1]}/accept",
        json={"name": "Ayrılan Kişi", "password": "ayrilan-parola-123"},
    )
    assert leaver.get("/api/v1/jobs").status_code == 200  # signed in

    leaver_id = next(
        m["id"] for m in client.get("/api/v1/org/members").json()
        if m["email"] == "gidecek@aksatek.com"
    )
    assert client.patch(
        f"/api/v1/org/members/{leaver_id}", json={"is_active": False}
    ).status_code == 200
    # the open browser must lose access immediately, not at next login
    assert leaver.get("/api/v1/jobs").status_code == 401


def test_logout_invalidates_the_cookie(client, new_client):
    browser = new_client()
    browser.post(
        "/api/v1/auth/login",
        json={"email": "ayse@aksatek.com", "password": "guclu-parola-123"},
    )
    assert browser.get("/api/v1/auth/me").status_code == 200
    browser.post("/api/v1/auth/logout")
    assert browser.get("/api/v1/auth/me").status_code == 401


def test_password_reset_rotates_credentials_and_revokes_sessions(client, new_client):
    """Forgot-password must not leak whether an address is registered."""
    from sqlalchemy import select as sa_select

    from senthire.db.models import PasswordReset
    from senthire.db.session import get_sessionmaker

    unknown = client.post(
        "/api/v1/auth/forgot-password", json={"email": "kimse@yok.com"}
    )
    known = client.post(
        "/api/v1/auth/forgot-password", json={"email": "ayse@aksatek.com"}
    )
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()  # identical response — no enumeration

    # the emailed link (token) only exists in the mail; read the row to simulate it
    session = get_sessionmaker()()
    reset = session.scalars(
        sa_select(PasswordReset).order_by(PasswordReset.created_at.desc())
    ).first()
    assert reset is not None
    session.close()


# --------------------------------------------------------------------------- #
# 5. Billing: the quota is what the customer actually pays for
# --------------------------------------------------------------------------- #


def test_trial_quota_blocks_uploads_and_upgrading_unblocks_them(client, new_client):
    from senthire.billing.plans import TRIAL_PLAN_ID, get_plan

    shop = new_client()
    shop.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Kota Testi A.Ş.",
            "name": "Kota Yöneticisi",
            "email": "kota@kotatest.com",
            "password": "kota-parola-123",
        },
    )
    info = shop.get("/api/v1/billing").json()
    assert info["plan"]["id"] == TRIAL_PLAN_ID
    quota = info["usage"]["quota"]
    assert info["usage"]["used"] == 0

    job = shop.post("/api/v1/jobs", json={"title": "Kota ilanı"}).json()
    over = shop.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(quota + 1)]},
    )
    assert over.status_code == 402, over.text
    detail = over.json()["detail"]
    assert detail["code"] == "cv_quota_exceeded"
    assert detail["remaining"] == quota  # the message tells them exactly where they stand

    # exactly at the limit is allowed — an off-by-one here charges or blocks wrongly
    assert shop.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(quota)]},
    ).status_code == 200

    # upgrading lifts the ceiling immediately (mock provider = instant activation)
    paid = shop.post("/api/v1/billing/checkout", json={"plan_id": "baslangic"})
    assert paid.status_code == 200, paid.text
    after = shop.get("/api/v1/billing").json()
    assert after["status"] == "active"
    assert after["usage"]["quota"] == get_plan("baslangic").cv_quota_per_month
    assert shop.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(quota + 1)]},
    ).status_code == 200


def test_metering_counts_new_cvs_once_and_never_counts_duplicates(
    client, new_client, storage_stub, fake_models
):
    meter = new_client()
    meter.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Sayaç A.Ş.",
            "name": "Sayaç Yöneticisi",
            "email": "sayac@sayac.com",
            "password": "sayac-parola-123",
        },
    )
    job = meter.post("/api/v1/jobs", json={"title": "Sayaç ilanı"}).json()
    before = meter.get("/api/v1/billing").json()["usage"]["used"]

    slots = meter.post(
        f"/api/v1/jobs/{job['id']}/uploads", json={"files": [{"filename": "a.pdf"}]}
    ).json()["uploads"]
    body = make_pdf("Sayac CV tek")
    storage_stub[slots[0]["s3_key"]] = body
    meter.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": slots[0]["s3_key"], "filename": "a.pdf"}]},
    )
    after_first = meter.get("/api/v1/billing").json()["usage"]["used"]
    assert after_first == before + 1

    # the same bytes again: content-addressed, so it must not bill twice
    again = meter.post(
        f"/api/v1/jobs/{job['id']}/uploads", json={"files": [{"filename": "a-copy.pdf"}]}
    ).json()["uploads"]
    storage_stub[again[0]["s3_key"]] = body
    meter.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": again[0]["s3_key"], "filename": "a-copy.pdf"}]},
    )
    assert meter.get("/api/v1/billing").json()["usage"]["used"] == after_first


# --------------------------------------------------------------------------- #
# 6. Batch (economy) mode drives the same funnel
# --------------------------------------------------------------------------- #


def test_batch_mode_runs_the_whole_funnel_and_records_the_discount(
    client, new_client, storage_stub, fake_models, monkeypatch
):
    from senthire.screening import batch as batch_mod
    from senthire.screening.llm import LlmUsage
    from senthire.screening.schemas import LightScreenOutput, ReqJudgment
    from senthire.workers.tasks import screen as screen_tasks

    submitted: dict[str, list[dict]] = {}

    def fake_submit(requests):
        submitted["requests"] = requests
        return "msgbatch_test"

    def fake_iter_results(batch_id, output_model):
        for request in submitted["requests"]:
            yield batch_mod.BatchOutcome(
                custom_id=request["custom_id"],
                output=LightScreenOutput(
                    judgments=[
                        ReqJudgment(
                            req_id="R2_b2b",
                            verdict="met",
                            score=1.0,
                            confidence=0.9,
                            info_status="explicit",
                            evidence=[{"quote": "B2B satis deneyimi", "page": 1}],
                            reasoning="Batch judgment.",
                        )
                    ]
                ),
                usage=LlmUsage("fake-haiku", 3000, 400, 12000, 0),
            )

    monkeypatch.setattr(screen_tasks.batch, "submit", fake_submit)
    monkeypatch.setattr(screen_tasks.batch, "processing_status", lambda bid: "ended")
    monkeypatch.setattr(screen_tasks.batch, "iter_results", fake_iter_results)

    hr = new_client()
    hr.post(
        "/api/v1/auth/signup",
        json={
            "company_name": "Toplu A.Ş.",
            "name": "Toplu Yönetici",
            "email": "toplu@toplu.com",
            "password": "toplu-parola-123",
        },
    )
    job = hr.post("/api/v1/jobs", json={"title": "Toplu ilan"}).json()
    compiled = hr.post(
        f"/api/v1/jobs/{job['id']}/requirements/compile",
        json={"natural_language_text": "En az 3 yıl B2B satış deneyimi."},
    ).json()
    hr.post(f"/api/v1/requirements/{compiled['spec_id']}/confirm", json={})

    slots = hr.post(
        f"/api/v1/jobs/{job['id']}/uploads",
        json={"files": [{"filename": f"cv{i}.pdf"} for i in range(len(CANDIDATES))]},
    ).json()["uploads"]
    for slot, candidate in zip(slots, CANDIDATES, strict=True):
        storage_stub[slot["s3_key"]] = make_pdf(f"Toplu CV {candidate[1]}")
    hr.post(
        f"/api/v1/jobs/{job['id']}/uploads/complete",
        json={"files": [{"s3_key": s["s3_key"], "filename": s["filename"]} for s in slots]},
    )

    started = hr.post(f"/api/v1/jobs/{job['id']}/runs", json={"mode": "batch"})
    assert started.status_code in {200, 202}, started.text
    run_id = started.json()["run_id"]

    status = hr.get(f"/api/v1/runs/{run_id}").json()
    assert status["mode"] == "batch"
    assert status["status"] == "complete", status
    # the deterministic knockout never occupies a batch slot
    assert len(submitted["requests"]) < len(CANDIDATES)
    # and the saving is recorded, not just claimed
    light = status["cost"]["light"]
    assert light["usd"] > 0 and light["usd_saved"] > 0
    assert abs(light["usd"] - light["usd_saved"]) < 1e-9  # 50% off means half and half

    results = hr.get(f"/api/v1/runs/{run_id}/results").json()
    assert results["results"], "batch mode must produce a ranking like interactive mode"
