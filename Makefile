.PHONY: install test lint api worker migrate seed up

install:
	pip install -e ".[dev]"

test:
	pytest

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
