.PHONY: install test test-all lint api worker migrate seed up evals evals-live

install:
	pip install -e ".[dev]"

test:
	pytest

# Full suite including the migration and end-to-end journey tests, which need a
# real Postgres (docker compose up db).
test-all:
	SENTHIRE_TEST_DATABASE_URL=postgresql+psycopg://senthire:senthire@localhost:5432/senthire pytest

evals:
	python -m senthire.evals

evals-live:
	python -m senthire.evals --live

lint:
	ruff check src tests

migrate:
	alembic upgrade head

seed:
	python -m senthire.seed

api:
	uvicorn senthire.api.app:app --reload --port 8000

worker:
	celery -A senthire.workers.celery_app worker -Q parse,screen,poll,mail -c 8 --loglevel=info

up:
	docker compose up --build
