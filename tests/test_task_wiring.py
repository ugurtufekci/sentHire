"""Every function dispatched with .delay() must actually be a Celery task.

This exists because of a real outage-shaped bug: during a refactor a
`@celery_app.task` decorator drifted from the task onto a helper extracted just
above it. The task became a plain function, the helper became a task nobody
calls, and `deep_application.delay(...)` started raising AttributeError inside a
worker — so Stage 5 never ran. Nothing caught it: unit tests call the function
directly, and the end-to-end fixture had deep analysis stubbed out.

The mistake is invisible by reading (the decorator is *there*, just one
function too high), so it needs a test that reads structure rather than
behaviour.
"""

import ast
import importlib
from pathlib import Path

import pytest

TASK_MODULES = sorted(Path("src/senthire/workers/tasks").glob("*.py"))


def _dispatched_names(path: Path) -> set[str]:
    """Names X in `X.delay(...)` and `X.apply_async(...)` at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"delay", "apply_async"}:
            continue
        if isinstance(node.func.value, ast.Name):
            found.add(node.func.value.id)
    return found


@pytest.mark.parametrize("path", TASK_MODULES, ids=lambda p: p.name)
def test_everything_dispatched_is_a_registered_task(path):
    module = importlib.import_module(f"senthire.workers.tasks.{path.stem}")
    for name in sorted(_dispatched_names(path)):
        target = getattr(module, name, None)
        if target is None:
            continue  # locally bound (e.g. a parameter) — not a module-level task
        assert hasattr(target, "delay"), (
            f"{path.name}: {name}.delay(...) is dispatched, but {name} is a plain "
            "function — its @celery_app.task decorator is missing or landed on "
            "the wrong function"
        )


def test_the_stages_the_pipeline_depends_on_are_all_registered():
    """A named check, so a renamed or undecorated stage fails loudly here."""
    import senthire.workers.tasks.mail
    import senthire.workers.tasks.parse
    import senthire.workers.tasks.screen  # noqa: F401
    from senthire.workers.celery_app import celery_app

    expected = {
        "senthire.intake.document",
        "senthire.screen.run_start",
        "senthire.screen.application",
        "senthire.screen.finalize",
        "senthire.screen.deep",
        "senthire.screen.score",
        "senthire.poll.batch",
        "senthire.mail.send",
    }
    missing = sorted(expected - set(celery_app.tasks))
    assert not missing, f"stages not registered as tasks: {missing}"


def test_no_task_takes_an_orm_object_as_an_argument():
    """Task arguments cross a queue: they must be JSON, not a Session-bound row.

    The decorator that drifted had landed on a helper whose first parameter is
    an Evaluation — had anything ever dispatched it, the failure would have
    been a serialization error in production rather than an obvious one here.
    """
    import inspect

    from senthire.workers.celery_app import celery_app

    orm_names = {"Evaluation", "Application", "Document", "ScreeningRun", "Session", "Organization"}
    offenders = []
    for name, task in celery_app.tasks.items():
        if not name.startswith("senthire."):
            continue
        signature = inspect.signature(task.run)
        for parameter in signature.parameters.values():
            annotation = parameter.annotation
            text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
            if text in orm_names:
                offenders.append(f"{name}({parameter.name}: {text})")
    assert not offenders, f"tasks taking ORM objects across the queue: {offenders}"
