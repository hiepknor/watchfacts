SHELL := /bin/sh

COMPOSE ?= docker compose
SERVICE ?= bot
LOG_LINES ?= 80
SKIP_PULL ?= 0
OPENWA_COMPOSE ?= 0
MCP_COMPOSE_SUFFIX ?= -f docker-compose.watchfacts-mcp.yml
MCP_SERVICE ?= watchfacts-mcp

ifeq ($(OPENWA_COMPOSE),1)
COMPOSE := $(COMPOSE) -f docker-compose.yml -f docker-compose.openwa.yml
endif

.DEFAULT_GOAL := help

.PHONY: help init verify-env pull build predeploy-check deploy update up down restart logs ps shell run login check clean mcp-build mcp-up mcp-down mcp-restart mcp-logs mcp-ps

help:
	@printf "%s\n" "watchfacts-bot commands"
	@printf "%s\n" ""
	@printf "%s\n" "  make init     Create local runtime directories and .env from .env.example when missing"
	@printf "%s\n" "  make verify-env Check server runtime files before deploy"
	@printf "%s\n" "  make pull     Pull latest git changes unless SKIP_PULL=1"
	@printf "%s\n" "  make build    Build Docker image"
	@printf "%s\n" "  make predeploy-check Run tests and lightweight checks before deploy"
	@printf "%s\n" "  make deploy   Pull, build, recreate bot, show status and startup logs"
	@printf "%s\n" "  make deploy OPENWA_COMPOSE=1 Deploy with the OpenWA network override"
	@printf "%s\n" "  make update   Alias for deploy"
	@printf "%s\n" "  make up       Start bot with Docker Compose"
	@printf "%s\n" "  make down     Stop Docker Compose services"
	@printf "%s\n" "  make restart  Restart bot service"
	@printf "%s\n" "  make logs     Follow bot logs"
	@printf "%s\n" "  make ps       Show Compose service status"
	@printf "%s\n" "  make shell    Open a shell in the bot container"
	@printf "%s\n" "  make run      Run bot locally on the host"
	@printf "%s\n" "  make login    Run WatchFacts browser login locally on the host"
	@printf "%s\n" "  make mcp-build      Build watchfacts-mcp image/service"
	@printf "%s\n" "  make mcp-up         Start watchfacts-mcp (with Hermes network override file)"
	@printf "%s\n" "  make mcp-down       Stop watchfacts-mcp"
	@printf "%s\n" "  make mcp-restart    Restart watchfacts-mcp"
	@printf "%s\n" "  make mcp-logs       Follow watchfacts-mcp logs"
	@printf "%s\n" "  make mcp-ps         Show watchfacts-mcp status"
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

predeploy-check:
	git diff --check
	$(COMPOSE) run --rm $(SERVICE) python -m pytest -q
	$(COMPOSE) run --rm $(SERVICE) python -m compileall app scripts

deploy: verify-env pull build predeploy-check
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
	python scripts/ops/login.py

MCP_COMPOSE_CMD = docker compose -f docker-compose.yml $(MCP_COMPOSE_SUFFIX)

mcp-build:
	$(MCP_COMPOSE_CMD) build $(MCP_SERVICE)

mcp-up:
	$(MCP_COMPOSE_CMD) up -d --build $(MCP_SERVICE)

mcp-down:
	$(MCP_COMPOSE_CMD) down $(MCP_SERVICE)

mcp-restart:
	$(MCP_COMPOSE_CMD) restart $(MCP_SERVICE)

mcp-logs:
	$(MCP_COMPOSE_CMD) logs -f $(MCP_SERVICE)

mcp-ps:
	$(MCP_COMPOSE_CMD) ps $(MCP_SERVICE)

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
