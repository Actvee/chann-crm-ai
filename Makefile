.PHONY: help up down migrate seed test validate preflight manifest

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

up: ## start the local stack
	docker compose up --build -d

down: ## stop the local stack
	docker compose down -v

migrate: ## apply migrations (step 1 of the mandatory order)
	cd database && DATABASE_URL=$${DATABASE_URL} python3 -m alembic upgrade head

seed: ## idempotent reference seed (step 2)
	DATABASE_URL=$${DATABASE_URL} APP_ENV=$${APP_ENV:-dev} python3 database/scripts/seed_reference.py

test: ## full test suite
	python3 -m pytest tests -q

validate: ## dependency-aware validation
	./scripts/dev-validate.sh $${BASE:-origin/main}

preflight: ## read-only GCP inventory
	./scripts/infra-preflight.sh

manifest: ## generate a release manifest
	python3 scripts/release-manifest.py --phase 1 --platform-version $${PLATFORM_VERSION:-0.1.0} --environment $${APP_ENV:-dev}
