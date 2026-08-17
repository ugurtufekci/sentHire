from fastapi import FastAPI

from senthire import __version__
from senthire.api.routes import candidates, health, jobs, templates, uploads


def create_app() -> FastAPI:
    app = FastAPI(title="sentHire API", version=__version__, docs_url="/api/docs")
    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(templates.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(uploads.router, prefix=prefix)
    app.include_router(candidates.router, prefix=prefix)
    return app


app = create_app()
