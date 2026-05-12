SHELL := /bin/sh

COMPOSE ?= docker compose
SERVICE ?= bot
LOG_LINES ?= 80
SKIP_PULL ?= 0

.DEFAULT_GOAL := help

.PHONY: help init verify-env pull build deploy update up down restart logs ps shell run login llm-up llm-down llm-logs llm-smoke check clean

help:
	@printf "%s\n" "watchfacts-bot commands"
	@printf "%s\n" ""
	@printf "%s\n" "  make init     Create local runtime directories and .env from .env.example when missing"
	@printf "%s\n" "  make verify-env Check server runtime files before deploy"
	@printf "%s\n" "  make pull     Pull latest git changes unless SKIP_PULL=1"
	@printf "%s\n" "  make build    Build Docker image"
	@printf "%s\n" "  make deploy   Pull, build, recreate bot, show status and startup logs"
	@printf "%s\n" "  make update   Alias for deploy"
	@printf "%s\n" "  make up       Start bot with Docker Compose"
	@printf "%s\n" "  make down     Stop Docker Compose services"
	@printf "%s\n" "  make restart  Restart bot service"
	@printf "%s\n" "  make logs     Follow bot logs"
	@printf "%s\n" "  make ps       Show Compose service status"
	@printf "%s\n" "  make shell    Open a shell in the bot container"
	@printf "%s\n" "  make run      Run bot locally on the host"
	@printf "%s\n" "  make login    Run WatchFacts browser login locally on the host"
	@printf "%s\n" "  make llm-up   Start experimental llama.cpp service"
	@printf "%s\n" "  make llm-down Stop experimental llama.cpp service"
	@printf "%s\n" "  make llm-logs Follow experimental llama.cpp logs"
	@printf "%s\n" "  make llm-smoke Call the local LLM chat endpoint"
	@printf "%s\n" "  make check    Run lightweight repository checks"
	@printf "%s\n" "  make clean    Remove local Python caches"

init:
	@mkdir -p data logs
	@if [ ! -f .env ]; then cp .env.example .env; fi

verify-env: init
	@test -s .env || { printf "%s\n" "Missing .env. Run make init and edit .env."; exit 1; }
	@test -s data/watchfacts_state.json || { printf "%s\n" "Missing data/watchfacts_state.json. Run make login on a machine with browser access."; exit 1; }

pull:
	@if [ "$(SKIP_PULL)" = "1" ]; then \
		printf "%s\n" "Skipping git pull because SKIP_PULL=1"; \
	else \
		git pull --ff-only; \
	fi

build:
	$(COMPOSE) build

deploy: verify-env pull build
	$(COMPOSE) up -d --force-recreate $(SERVICE)
	$(COMPOSE) ps
	$(COMPOSE) logs --tail=$(LOG_LINES) $(SERVICE)

update: deploy

up: init
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart $(SERVICE)

logs:
	$(COMPOSE) logs -f $(SERVICE)

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) run --rm $(SERVICE) /bin/sh

run:
	python -m app.main

login:
	python scripts/login.py

llm-up: init
	COMPOSE_PROFILES=llm $(COMPOSE) up -d llama-cpp

llm-down:
	COMPOSE_PROFILES=llm $(COMPOSE) stop llama-cpp
	COMPOSE_PROFILES=llm $(COMPOSE) rm -f llama-cpp

llm-logs:
	COMPOSE_PROFILES=llm $(COMPOSE) logs -f llama-cpp

llm-smoke:
	python scripts/smoke_local_llm.py

check:
	git diff --check
	@paths=""; \
	for path in app scripts; do \
		if [ -d "$$path" ]; then paths="$$paths $$path"; fi; \
	done; \
	if [ -n "$$paths" ]; then python -m compileall $$paths; fi

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete
