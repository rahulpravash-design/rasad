# RASAD developer commands. `make help` lists them.
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python

# Pick up non-secret settings from .env if present (see .env.example).
-include .env
DB_PATH        ?= data/cache/rasad.db
SEED           ?= 42
OFFLINE_MODE   ?= true
WEATHER_SOURCE ?= auto
export DB_PATH SEED OFFLINE_MODE WEATHER_SOURCE

.DEFAULT_GOAL := help
.PHONY: help setup data data-check train eval run dev test lint demo clean

help: ## list commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-11s %s\n", $$1, $$2}'

setup: ## install python + node deps (pinned: requirements.lock, ui/package-lock.json)
	python3.11 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r requirements.lock
	$(VENV)/bin/pip install --quiet --no-deps -e .
	cd ui && npm ci

data: ## generate synthetic data + fetch/cache weather into the SQLite db
	$(PY) -m data.build

data-check: ## prove `make data` is deterministic: build twice, compare content digests
	@tmp=$$(mktemp -d); \
	a=$$($(PY) -m data.build --source synthetic --out $$tmp/a.db --cache-dir $$tmp/cache --digest 2>/dev/null); \
	b=$$($(PY) -m data.build --source synthetic --out $$tmp/b.db --cache-dir $$tmp/cache --digest 2>/dev/null); \
	rm -rf $$tmp; \
	echo "build 1: $$a"; echo "build 2: $$b"; \
	[ "$$a" = "$$b" ] && echo "deterministic: yes" || { echo "deterministic: NO"; exit 1; }

train: ## local, federated, central forecast models (Day 5-6: not implemented yet)
	@echo "make train: forecast models land on Day 5-6; nothing to train yet." >&2; exit 1

eval: ## gate, forecast and 100-winter experiments -> eval/results.md (Day 4-9: not implemented yet)
	@echo "make eval: experiments land on Day 4-9; there are no results yet." >&2; exit 1

run: ## docker compose up (offline-capable once images are built)
	docker compose up --build

dev: ## run API (:8000) and UI (:5173) locally without docker
	@test -f $(DB_PATH) || { echo "no database at $(DB_PATH): run 'make data' first" >&2; exit 1; }
	@trap 'kill 0' EXIT; \
	$(VENV)/bin/uvicorn api.main:app --reload --port 8000 & \
	(cd ui && npm run dev) & \
	wait

test: ## pytest
	$(PY) -m pytest

lint: ## ruff + UI typecheck
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	cd ui && npm run typecheck

demo: data ## seed data, then start the app and print the demo flow
	@echo ""; echo "Demo flow: docs/demo-script.md   UI: http://localhost:5173"; echo ""
	@$(MAKE) --no-print-directory dev

clean: ## remove generated data and build output (keeps the weather cache)
	rm -f data/cache/rasad.db data/cache/rasad.db.tmp
	rm -rf ui/dist .pytest_cache .ruff_cache
