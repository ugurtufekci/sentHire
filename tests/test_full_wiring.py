"""Nothing exists unless it is wired: routes, tasks, frontend calls, labels.

This suite exists because of a specific failure mode that unit tests cannot
see: a piece is written, its own tests pass, and it is never actually connected
— a router not included, an endpoint the UI calls that the API never grew, a
stage name the frontend has no label for. Each test here reads *structure*
(files, registries, the frontend source) rather than behaviour, so the failure
message is "X is not wired", before any user finds out.
"""

import importlib
import json
import pkgutil
import re
from pathlib import Path

import pytest

SRC = Path("src/senthire")
WEB = Path("web")


# --------------------------------------------------------------------------- #
# 1. Everything imports
# --------------------------------------------------------------------------- #


def _all_modules() -> list[str]:
    import senthire

    return sorted(
        name
        for _finder, name, _ispkg in pkgutil.walk_packages(
            senthire.__path__, prefix="senthire."
        )
    )


@pytest.mark.parametrize("module_name", _all_modules())
def test_every_module_imports(module_name):
    """A syntax error or bad import in an untested module stays invisible until
    a worker dies on it in production."""
    importlib.import_module(module_name)


# --------------------------------------------------------------------------- #
# 2. Every route module is mounted
# --------------------------------------------------------------------------- #


def test_every_route_module_is_included_in_the_app():
    from senthire.api.app import create_app

    app_source = (SRC / "api" / "app.py").read_text(encoding="utf-8")
    modules = {
        path.stem
        for path in (SRC / "api" / "routes").glob("*.py")
        if path.stem != "__init__"
    }
    unmounted = {
        module for module in modules if f"{module}.router" not in app_source
    }
    assert not unmounted, f"route modules never included in app.py: {sorted(unmounted)}"

    # ...and the app actually serves them. Counted via the OpenAPI schema, not
    # app.routes: FastAPI 0.141 wraps included routers in composite entries, so
    # inspecting internals answers a different question than "what is served".
    assert len(_served_paths(create_app())) > 40


def test_conditional_routers_have_their_condition():
    """local_storage is mounted only for the local backend — deliberately. If
    that guard ever disappears, the dev upload endpoint ships to production."""
    app_source = (SRC / "api" / "app.py").read_text(encoding="utf-8")
    assert 'storage_backend == "local"' in app_source


# --------------------------------------------------------------------------- #
# 3. Every frontend API call has a backend route
# --------------------------------------------------------------------------- #


def _frontend_paths() -> list[str]:
    """Paths the web client requests, template literals normalized to {param}."""
    source = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
    raw = re.findall(r"request<[^>]*>\(\s*[`\"']([^`\"']+)[`\"']", source)
    return sorted({re.sub(r"\$\{[^}]+\}", "{param}", path) for path in raw})


def _served_paths(app) -> list[str]:
    """Every path the app actually serves, from its own OpenAPI schema."""
    return sorted(app.openapi()["paths"])


def _backend_routes() -> list[str]:
    from senthire.api.app import create_app

    return [path.removeprefix("/api/v1") for path in _served_paths(create_app())]


def _matches(frontend: str, backend: str) -> bool:
    pattern = re.sub(r"\{[^}]+\}", r"[^/]+", backend)
    return re.fullmatch(pattern, re.sub(r"\{param\}", "x", frontend)) is not None


def test_every_frontend_call_reaches_a_real_endpoint():
    """The composer calling /messages/send means nothing if the API never grew
    it. This is the test that fails when the two halves are built apart."""
    backends = _backend_routes()
    orphans = [
        path
        for path in _frontend_paths()
        if not any(_matches(path, backend_path) for backend_path in backends)
    ]
    assert not orphans, f"frontend calls endpoints that do not exist: {orphans}"


# --------------------------------------------------------------------------- #
# 4. Every value the backend emits has a face in the UI
# --------------------------------------------------------------------------- #


def _label_map(name: str) -> set[str]:
    source = (WEB / "lib" / "format.ts").read_text(encoding="utf-8")
    match = re.search(name + r"[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL)
    assert match, f"{name} not found in format.ts"
    return set(re.findall(r'^\s*"?([\w-]+)"?\s*:', match.group(1), re.MULTILINE))


def test_every_pipeline_stage_has_a_turkish_label():
    from senthire.api.routes.pipeline import STAGES

    missing = set(STAGES) - _label_map("PIPELINE_STAGE_LABEL")
    assert not missing, f"stages the board cannot name: {sorted(missing)}"


def test_every_event_kind_has_a_turkish_label():
    from senthire.api.routes.pipeline import EVENT_KINDS

    missing = (set(EVENT_KINDS) | {"stage_change"}) - _label_map("EVENT_KIND_LABEL")
    assert not missing, f"event kinds the timeline cannot name: {sorted(missing)}"


def test_every_review_reason_has_a_turkish_label():
    """Review reasons are emitted in the pipeline and rendered in the drawer;
    an unlabeled one reaches the recruiter as a raw slug."""
    reasons = set()
    for path in SRC.rglob("*.py"):
        reasons.update(
            re.findall(r'review_reasons[^\n]*\{"([a-z_]+)"\}', path.read_text(encoding="utf-8"))
        )
    reasons.update({"low_confidence", "hard_requirement_unverified", "disqualifier_triggered"})
    missing = reasons - _label_map("REVIEW_REASON_LABEL")
    assert not missing, f"review reasons without a label: {sorted(missing)}"


def test_every_injection_kind_has_a_turkish_label():
    from senthire.screening.injection import PATTERNS

    missing = {kind for kind, _ in PATTERNS} - _label_map("INJECTION_KIND_LABEL")
    assert not missing, f"injection kinds without a label: {sorted(missing)}"


def test_every_parse_error_reason_has_a_turkish_label():
    reasons = set()
    for path in (SRC / "workers" / "tasks").glob("*.py"):
        reasons.update(
            re.findall(r'_register_failed\([^)]*"([a-z_]+)"\s*\)|_mark_failed\([^,]+,\s*"([a-z_]+)"',
                       path.read_text(encoding="utf-8")),
        )
    flat = {r for pair in reasons for r in (pair if isinstance(pair, tuple) else (pair,)) if r}
    missing = flat - _label_map("PARSE_ERROR_LABEL")
    assert not missing, f"parse errors without a label: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# 5. Seeds and templates are valid
# --------------------------------------------------------------------------- #


def test_every_seed_template_compiles_into_a_valid_spec():
    from senthire.domain.spec import EvaluationSpec

    seeds = sorted((SRC / "templates_seed").glob("*.json"))
    assert seeds, "no seed templates found"
    for path in seeds:
        payload = json.loads(path.read_text(encoding="utf-8"))
        EvaluationSpec.model_validate(payload["spec_seed"])


def test_default_outreach_templates_render_cleanly():
    from senthire.services.outreach import DEFAULT_TEMPLATES, VARIABLES, render

    context = {variable: f"<{variable}>" for variable in VARIABLES}
    for template in DEFAULT_TEMPLATES:
        for field in ("subject", "body"):
            rendered = render(template[field], context)
            assert "{{" not in rendered, f"{template['slug']}.{field} left a placeholder"


# --------------------------------------------------------------------------- #
# 6. Configuration tells the truth
# --------------------------------------------------------------------------- #


def test_settings_construct_from_nothing(monkeypatch):
    """Every setting has a working default; a required-but-undocumented env var
    is a deploy that fails at 2am."""
    import os

    import senthire.config as config

    for key in list(os.environ):
        if key.startswith("SENTHIRE_"):
            monkeypatch.delenv(key)
    settings = config.Settings(_env_file=None)
    assert settings.database_url and settings.redis_url
    assert settings.storage_backend == "s3", "production default must not be the dev backend"
    assert settings.fake_models is False, "production default must be real models"


def test_worker_queues_cover_every_task_route():
    """A task routed to a queue no worker listens on is enqueued forever."""
    from senthire.workers.celery_app import celery_app

    routed = {
        route["queue"] for route in celery_app.conf.task_routes.values()
    } | {celery_app.conf.task_default_queue}
    documented = set()
    for path in [Path("README.md"), *Path("docs").glob("*.md"), *Path("scripts").rglob("*.md"),
                 Path("docker-compose.yml")]:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            for queue in routed:
                if queue in text:
                    documented.add(queue)
    missing = routed - documented
    assert not missing, f"queues no run instruction mentions: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# 7. The SMTP path, against a real SMTP conversation
# --------------------------------------------------------------------------- #


def test_smtp_delivery_speaks_actual_smtp(monkeypatch):
    """The smtp backend had never met an SMTP server outside of mocks.

    A minimal in-process server accepts one real connection; the assertion is
    on the wire product — headers, Reply-To, both bodies — not on which client
    method was called.
    """
    import asyncio
    import threading
    from email import message_from_bytes

    received: list[bytes] = []
    started = threading.Event()
    loop_holder: dict = {}

    async def _serve():
        loop = asyncio.get_running_loop()
        loop_holder["loop"] = loop

        class Handler(asyncio.Protocol):
            def connection_made(self, transport):
                self.transport = transport
                self.buffer = b""
                self.in_data = False
                transport.write(b"220 test ESMTP\r\n")

            def data_received(self, chunk):
                self.buffer += chunk
                while b"\r\n" in self.buffer:
                    line, _, self.buffer = self.buffer.partition(b"\r\n")
                    if self.in_data:
                        if line == b".":
                            self.in_data = False
                            received.append(self.payload)
                            self.transport.write(b"250 OK\r\n")
                        else:
                            self.payload += line + b"\r\n"
                    elif line.upper().startswith((b"EHLO", b"HELO")):
                        self.transport.write(b"250 test\r\n")
                    elif line.upper().startswith(b"DATA"):
                        self.in_data = True
                        self.payload = b""
                        self.transport.write(b"354 go\r\n")
                    elif line.upper().startswith(b"QUIT"):
                        self.transport.write(b"221 bye\r\n")
                        self.transport.close()
                    else:
                        self.transport.write(b"250 OK\r\n")

        server = await loop.create_server(Handler, "127.0.0.1", 0)
        loop_holder["port"] = server.sockets[0].getsockname()[1]
        started.set()
        async with server:
            await server.serve_forever()

    thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
    thread.start()
    assert started.wait(5), "test SMTP server failed to start"

    monkeypatch.setenv("SENTHIRE_EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SENTHIRE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SENTHIRE_SMTP_PORT", str(loop_holder["port"]))
    from senthire.config import get_settings

    get_settings.cache_clear()
    try:
        from senthire.services.email import send_email

        send_email(
            "aday@example.com",
            "Görüşme daveti",
            "<p>Merhaba</p>",
            "Merhaba",
            reply_to="selin@dumanlojistik.com",
        )
    finally:
        get_settings.cache_clear()
        loop_holder["loop"].call_soon_threadsafe(loop_holder["loop"].stop)

    assert received, "nothing arrived over SMTP"
    message = message_from_bytes(received[0])
    assert message["To"] == "aday@example.com"
    assert message["Reply-To"] == "selin@dumanlojistik.com"
    assert "Görüşme daveti" in str(message["Subject"]) or "=?utf-8" in str(message["Subject"])
    parts = {part.get_content_type() for part in message.walk()}
    assert {"text/plain", "text/html"} <= parts, f"missing alternative: {parts}"
