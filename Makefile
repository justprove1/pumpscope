.DEFAULT_GOAL := help
SHELL := /bin/bash
PY ?= python3

.PHONY: help install install-dev lint format typecheck test test-unit test-integration \
        check up down logs ps migrate revision downgrade seed clean web-install web-dev

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Entorno -----------------------------------------------------------------

install: ## Instala el paquete y sus dependencias en el entorno actual
	$(PY) -m pip install -e .

install-dev: ## Instala tambien las herramientas de desarrollo (ruff, mypy, pytest)
	$(PY) -m pip install -e ".[dev]"

# --- Calidad (CLAUDE.md 0.6: la salida real se muestra siempre) --------------

lint: ## ruff check
	$(PY) -m ruff check .

format: ## ruff format + autofix de imports
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## mypy en modo strict
	$(PY) -m mypy

test: ## Toda la suite
	$(PY) -m pytest

test-unit: ## Solo tests unitarios (sin servicios externos)
	$(PY) -m pytest tests/unit

test-integration: ## Tests que requieren Postgres/Redis (make up primero)
	$(PY) -m pytest -m integration tests/integration

check: lint typecheck test ## Puerta de calidad completa. Debe estar en verde para cerrar una fase.

# --- Infraestructura ---------------------------------------------------------

up: ## Levanta el stack local (postgres, redis, prometheus, grafana, api, worker, signer, web)
	docker compose up -d --build

down: ## Para el stack y borra los contenedores (los volumenes se conservan)
	docker compose down

logs: ## Sigue los logs de todos los servicios
	docker compose logs -f --tail=100

ps: ## Estado de los servicios
	docker compose ps

# --- Base de datos -----------------------------------------------------------

migrate: ## Aplica todas las migraciones pendientes
	$(PY) -m alembic upgrade head

revision: ## Crea una migracion nueva:  make revision m="descripcion"
	$(PY) -m alembic revision --autogenerate -m "$(m)"

downgrade: ## Revierte una migracion
	$(PY) -m alembic downgrade -1

seed: ## Carga datos de demo (solo APP_ENV=local)
	$(PY) -m mit_api.scripts.seed_demo

# --- Frontend ----------------------------------------------------------------

web-install: ## Instala dependencias del frontend
	npm install

web-dev: ## Arranca Next.js en modo desarrollo
	npm run dev

clean: ## Borra artefactos de build y caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage dist build
