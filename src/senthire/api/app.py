from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from senthire import __version__
from senthire.api.routes import (
    auth,
    billing,
    candidates,
    health,
    jobs,
    pipeline,
    requirements,
    runs,
    team,
    templates,
    uploads,
)
from senthire.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="sentHire API", version=__version__, docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(team.router, prefix=prefix)
    app.include_router(billing.router, prefix=prefix)
    app.include_router(templates.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(uploads.router, prefix=prefix)
    app.include_router(candidates.router, prefix=prefix)
    app.include_router(requirements.router, prefix=prefix)
    app.include_router(runs.router, prefix=prefix)
    app.include_router(pipeline.router, prefix=prefix)
    return app


app = create_app()
