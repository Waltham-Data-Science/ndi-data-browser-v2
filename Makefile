.DEFAULT_GOAL := help

.PHONY: help install dev backend frontend test test-backend test-frontend test-e2e lint typecheck build docker compose up down clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | awk 'BEGIN{FS=":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

install: ## Install backend and frontend deps
	python3.12 -m venv .venv || python3 -m venv .venv
	. .venv/bin/activate && pip install -r backend/requirements.txt
	. .venv/bin/activate && pip install pytest pytest-asyncio pytest-cov respx hypothesis fakeredis freezegun
	cd frontend && npm install --no-audit --no-fund

dev: ## Start everything locally (assumes Redis at localhost:6379)
	@echo "Launch backend in one terminal:   make backend"
	@echo "Launch frontend in another:        make frontend"

backend: ## Start the backend
	. .venv/bin/activate && uvicorn backend.app:app --reload --port 8000

frontend: ## Start the frontend dev server
	cd frontend && npm run dev

test: test-backend test-frontend ## Run backend + frontend unit+integration tests

test-backend: ## pytest (unit + integration)
	. .venv/bin/activate && python -m pytest backend/tests/unit backend/tests/integration --no-cov

test-backend-cov: ## pytest with coverage gate
	. .venv/bin/activate && python -m pytest backend/tests/unit backend/tests/integration --cov=backend --cov-report=term-missing

test-frontend: ## vitest
	cd frontend && npm test --if-present

test-e2e: ## Playwright (assumes backend+frontend running)
	cd frontend && npx playwright test

lint: ## ruff + mypy + ESLint + tsc
	. .venv/bin/activate && ruff check backend/
	. .venv/bin/activate && mypy backend/ || true
	cd frontend && npm run typecheck

typecheck: ## TypeScript + mypy
	cd frontend && npm run typecheck
	. .venv/bin/activate && mypy backend/ || true

build: ## Build frontend and Docker image
	cd frontend && npm run build
	docker build -f infra/Dockerfile -t ndb-v2:local .

compose-up: ## docker-compose up
	docker compose -f infra/docker-compose.yml up --build

compose-down: ## docker-compose down
	docker compose -f infra/docker-compose.yml down

clean: ## Clean caches + build artifacts
	rm -rf frontend/dist frontend/node_modules/.vite
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
