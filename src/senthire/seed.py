"""Dev/bootstrap seeding: job templates + the dev organization.

Usage:  python -m senthire.seed
"""

import json
from importlib import resources

from sqlalchemy import select

from senthire.config import get_settings
from senthire.db.models import JobTemplate, Organization
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
        org = session.scalar(select(Organization).where(Organization.name == "Dev Org"))
        if org is None and settings.env == "dev":
            org = Organization(name="Dev Org")
            session.add(org)
        session.commit()
        print(f"seeded {n} template(s)")
        if settings.env == "dev":
            print(f"dev org ready — use header  X-API-Key: {settings.dev_api_key}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
