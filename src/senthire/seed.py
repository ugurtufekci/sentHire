"""Bootstrap seeding: built-in job templates.

Usage:  python -m senthire.seed
"""

import json
from importlib import resources

from sqlalchemy import select

from senthire.config import get_settings
from senthire.db.models import JobTemplate
from senthire.db.session import get_sessionmaker
from senthire.domain.spec import EvaluationSpec


def seed_templates(session) -> int:
    count = 0
    root = resources.files("senthire") / "templates_seed"
    for entry in root.iterdir():
        if not entry.name.endswith(".json"):
            continue
        data = json.loads(entry.read_text(encoding="utf-8"))
        EvaluationSpec.model_validate(data["spec_seed"])  # templates must be valid specs
        existing = session.scalar(select(JobTemplate).where(JobTemplate.slug == data["slug"]))
        if existing is None:
            session.add(
                JobTemplate(
                    slug=data["slug"],
                    locale=data.get("locale", "tr"),
                    title=data["title"],
                    spec_seed=data["spec_seed"],
                )
            )
        else:
            existing.title = data["title"]
            existing.locale = data.get("locale", "tr")
            existing.spec_seed = data["spec_seed"]
        count += 1
    return count


def main() -> None:
    settings = get_settings()
    session = get_sessionmaker()()
    try:
        n = seed_templates(session)
        session.commit()
        print(f"seeded {n} template(s)")
        if settings.dev_api_key:
            print(f"dev key backdoor enabled — header  X-API-Key: {settings.dev_api_key}")
        else:
            print("auth: cookie sessions only (sign up at /signup)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
