# Energy Demand & Price Analytics Warehouse
# Reproducible make targets for local development and CI-style verification.

.EXPORT_ALL_VARIABLES:
POSTGRES_USER ?= energy
POSTGRES_PASSWORD ?= energy
POSTGRES_DB ?= energywarehouse
DB_HOST ?= localhost
DB_PORT ?= 5432

.PHONY: up down wait-db schema seed verify clean notebook

up:
	@if [ ! -f .env ] && [ -f .env.example ]; then \
		echo "Creating .env from .env.example"; \
		cp .env.example .env; \
	fi
	docker compose up -d

down:
	docker compose down

clean:
	docker compose down -v
	rm -rf output/*
	find . -type d -name __pycache__ -exec rm -rf {} +

wait-db:
	@echo "Waiting for PostgreSQL on $(DB_HOST):$(DB_PORT)..."
	@for i in $$(seq 1 30); do \
		if docker compose exec -T postgres pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB) >/dev/null 2>&1; then \
			echo "PostgreSQL is ready"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "PostgreSQL did not become ready"; exit 1

schema: wait-db
	docker compose exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -f /dev/stdin < src/schema.sql

seed: up wait-db schema
	python src/etl.py

verify: seed
	bash queries/run_all.sh
	python tests/test_assertions.py

notebook:
	jupyter lab analysis/
