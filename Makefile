.PHONY: help install dev run worker beat migrate test lint clean docker-up docker-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -e .

dev: ## Install development dependencies
	pip install -e ".[dev]"

run: ## Run FastAPI dev server
	uvicorn logmind.main:app --host 0.0.0.0 --port 8000 --reload --app-dir src

worker: ## Run Celery worker
	cd src && celery -A logmind.core.celery_app worker --loglevel=info --concurrency=4

beat: ## Run Celery beat scheduler
	cd src && celery -A logmind.core.celery_app beat --loglevel=info

migrate: ## Run database migrations
	alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create msg="description")
	alembic revision --autogenerate -m "$(msg)"

test: ## Run tests
	pytest tests/ -v --cov=src/logmind --cov-report=term-missing

lint: ## Run linter
	ruff check src/ tests/
	ruff format --check src/ tests/

format: ## Auto-format code
	ruff check --fix src/ tests/
	ruff format src/ tests/

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info

docker-up: ## Start development environment
	docker compose up -d

docker-down: ## Stop development environment
	docker compose down

docker-build: ## Build production Docker image
	docker build -t logmind:latest .

seed-prompts: ## Seed default prompt templates
	cd src && python3 -m logmind.scripts.seed_prompts
