SHELL := /bin/sh

COMPOSE ?= docker compose
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
IMAGE ?= watchfacts:local
BOT_SERVICE ?= watchfacts-bot
LEGACY_BOT_CONTAINER ?= watchfacts
LOG_LINES ?= 80
SKIP_PULL ?= 0
OPENWA_COMPOSE ?= 0
SMOKE_QUERY ?= 5712g
QUALITY_AUDIT_LIMIT ?= 5
SEARCH_ENGINE_AUDIT_LIMIT ?= 5
SEARCH_ENGINE_AUDIT_QUERIES ?= "rm07-01 rg snow" "rm07-01 rose gold" "rm07-01 white gold" "rm07-01 mother of pearl"
SEARCH_ENGINE_BASELINE_DIR ?= logs/search-engine-baseline
SEARCH_ENGINE_BASELINE_LABEL ?= $(shell date +%Y%m%d-%H%M%S)
MCP_SMOKE_URL ?= http://127.0.0.1:8765/mcp
MCP_SMOKE_TIMEOUT_SECONDS ?= 120
MCP_BENCHMARK_FORMAT ?= markdown
MCP_BENCHMARK_LIMIT ?= 3
MCP_BENCHMARK_REPEAT ?= 1
MCP_BENCHMARK_EXTRA_ARGS ?=
MCP_COLD_BUDGET_FORMAT ?= markdown
MCP_PREWARM_FORMAT ?= text
MCP_PREWARM_LIMIT ?= 5
MCP_PREWARM_VERIFY_HOT ?= 1
MCP_POSTDEPLOY_PREWARM ?= 1
MCP_POSTDEPLOY_PREWARM_BENCHMARK_DEFAULTS ?= 1
MCP_COMPOSE_SUFFIX ?= -f docker-compose.watchfacts-mcp.yml
MCP_SERVICE ?= watchfacts-mcp
AI_AUDIT_ARTIFACT ?= audit-report.jsonl
AI_AUDIT_TRIAGE_FORMAT ?= markdown
AI_AUDIT_TRIAGE_OPENAI ?= 0

ifeq ($(OPENWA_COMPOSE),1)
COMPOSE := $(COMPOSE) -f docker-compose.yml -f docker-compose.openwa.yml
endif

export IMAGE

.DEFAULT_GOAL := help

.PHONY: help init verify-env verify-bot-env pull build predeploy-check deploy deploy-bot deploy-mcp deploy-bot-mcp update up down restart logs ps shell run login check clean mcp-build mcp-predeploy-check mcp-up mcp-down mcp-restart mcp-logs mcp-ps mcp-smoke mcp-smoke-set mcp-benchmark mcp-cold-budget mcp-prewarm mcp-prewarm-benchmark-defaults mcp-postdeploy-prewarm mcp-runtime-config mcp-wait-healthy search-engine-predeploy-check search-engine-postdeploy-check search-engine-deploy-check search-engine-baseline-snapshot quality-audit ai-audit-triage predeploy-quality-check

help:
	@printf "%s\n" "watchfacts commands"
	@printf "%s\n" ""
	@printf "%s\n" "  make init     Create local runtime directories and .env from .env.example when missing"
	@printf "%s\n" "  make verify-env Check server runtime files before deploy"
	@printf "%s\n" "  make pull     Pull latest git changes unless SKIP_PULL=1"
	@printf "%s\n" "  make build    Build Docker image"
	@printf "%s\n" "  make predeploy-check Run tests and repository checks before deploy"
	@printf "%s\n" "  make deploy   Deploy watchfacts-bot and watchfacts-mcp"
	@printf "%s\n" "  make deploy-bot Deploy watchfacts-bot only"
	@printf "%s\n" "  make deploy-mcp Deploy watchfacts-mcp only (build, prechecks, recreate)"
	@printf "%s\n" "  make deploy-bot-mcp Alias for deploy"
	@printf "%s\n" "  make deploy-bot OPENWA_COMPOSE=1 Deploy watchfacts-bot with OpenWA network override"
	@printf "%s\n" "  make update   Alias for deploy"
	@printf "%s\n" "  make up       Start watchfacts-bot with Docker Compose"
	@printf "%s\n" "  make down     Stop Docker Compose services"
	@printf "%s\n" "  make restart  Restart watchfacts-bot service"
	@printf "%s\n" "  make logs     Follow watchfacts-bot logs"
	@printf "%s\n" "  make ps       Show Compose service status"
	@printf "%s\n" "  make shell    Open a shell in the watchfacts-bot container"
	@printf "%s\n" "  make run      Run watchfacts-bot locally on the host"
	@printf "%s\n" "  make login    Run WatchFacts browser login locally on the host"
	@printf "%s\n" "  make mcp-build      Build watchfacts-mcp image/service"
	@printf "%s\n" "  make mcp-predeploy-check Run MCP predeploy checks"
	@printf "%s\n" "  make mcp-up         Start watchfacts-mcp with the MCP compose override"
	@printf "%s\n" "  make mcp-down       Stop watchfacts-mcp"
	@printf "%s\n" "  make mcp-restart    Restart watchfacts-mcp"
	@printf "%s\n" "  make mcp-logs       Follow watchfacts-mcp logs"
	@printf "%s\n" "  make mcp-ps         Show watchfacts-mcp status"
	@printf "%s\n" "  make mcp-smoke      Run one authorized HTTPX search smoke check"
	@printf "%s\n" "  make mcp-smoke-set  Validate MCP search response shape for representative queries"
	@printf "%s\n" "  make mcp-benchmark  Benchmark representative MCP search queries"
	@printf "%s\n" "  make mcp-cold-budget Benchmark focused cold-path retrieval budget queries"
	@printf "%s\n" "  make mcp-prewarm    Prewarm representative MCP search cache entries and verify hot cache"
	@printf "%s\n" "  make mcp-prewarm-benchmark-defaults Prewarm benchmark/common brand queries"
	@printf "%s\n" "  make mcp-postdeploy-prewarm Best-effort cache prewarm after MCP deploy"
	@printf "%s\n" "  make mcp-runtime-config Print safe effective MCP runtime config values"
	@printf "%s\n" "  make search-engine-predeploy-check Run local search-engine deploy gate"
	@printf "%s\n" "  make search-engine-postdeploy-check Run MCP smoke and benchmark after deploy"
	@printf "%s\n" "  make search-engine-deploy-check Run both search-engine deploy gates"
	@printf "%s\n" "  make search-engine-baseline-snapshot Capture runtime config plus hot/cold MCP benchmark artifacts"
	@printf "%s\n" "  make quality-audit  Run the default production quality audit query set"
	@printf "%s\n" "  make ai-audit-triage Summarize an audit artifact, optionally with OpenAI"
	@printf "%s\n" "  make predeploy-quality-check Run local checks plus the default quality audit"
	@printf "%s\n" "  make check    Run repository checks"
	@printf "%s\n" "  make clean    Remove local Python caches"

init:
	@mkdir -p data logs
	@if [ ! -f .env ]; then cp .env.example .env; fi

verify-env: init
	@test -s .env || { printf "%s\n" "Missing .env. Run make init and edit .env."; exit 1; }
	@test -s data/watchfacts_state.json || { printf "%s\n" "Missing data/watchfacts_state.json. Run make login on a machine with browser access."; exit 1; }

verify-bot-env: verify-env
	@awk -F= '/^TELEGRAM_BOT_TOKEN=/{ token=$$2 } END { if (token == "" || token == "your_telegram_token") { printf "%s\n", "TELEGRAM_BOT_TOKEN is missing or still set to the placeholder. Edit .env before deploying watchfacts-bot."; exit 1 } }' .env

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
	$(COMPOSE) run --rm $(BOT_SERVICE) python -m pytest -q
	$(COMPOSE) run --rm $(BOT_SERVICE) python -m compileall app scripts

deploy: deploy-bot-mcp

deploy-bot: verify-bot-env pull build predeploy-check
	@docker rm -f $(LEGACY_BOT_CONTAINER) 2>/dev/null || true
	$(COMPOSE) up -d --force-recreate --remove-orphans $(BOT_SERVICE)
	$(COMPOSE) ps
	$(COMPOSE) logs --tail=$(LOG_LINES) $(BOT_SERVICE)

deploy-mcp: verify-env pull mcp-build mcp-predeploy-check
	$(MCP_COMPOSE_CMD) up -d --force-recreate --remove-orphans $(MCP_SERVICE)
	$(MCP_COMPOSE_CMD) ps
	$(MAKE) mcp-wait-healthy
	$(MAKE) mcp-postdeploy-prewarm
	$(MCP_COMPOSE_CMD) logs --tail=$(LOG_LINES) $(MCP_SERVICE)

deploy-bot-mcp: deploy-bot deploy-mcp

update: deploy

up: init
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart $(BOT_SERVICE)

logs:
	$(COMPOSE) logs -f $(BOT_SERVICE)

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) run --rm $(BOT_SERVICE) /bin/sh

run:
	$(PYTHON) -m app.main

login:
	$(PYTHON) scripts/ops/login.py

MCP_COMPOSE_CMD = docker compose -f docker-compose.yml $(MCP_COMPOSE_SUFFIX)
MCP_PREWARM_VERIFY_HOT_ARGS = $(if $(filter 1 true yes on,$(MCP_PREWARM_VERIFY_HOT)),--verify-hot,)

mcp-build:
	$(MCP_COMPOSE_CMD) build $(MCP_SERVICE)

mcp-predeploy-check:
	git diff --check
	$(MCP_COMPOSE_CMD) run --rm $(MCP_SERVICE) python -m pytest -q
	$(MCP_COMPOSE_CMD) run --rm $(MCP_SERVICE) python -m compileall app scripts
	$(MCP_COMPOSE_CMD) run --rm $(MCP_SERVICE) python scripts/diagnostics/audit_quality.py --limit $(QUALITY_AUDIT_LIMIT)

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

mcp-smoke:
	$(PYTHON) scripts/diagnostics/benchmark_watchfacts_http.py --query "$(SMOKE_QUERY)" --warmup --repeat 1

mcp-smoke-set:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/mcp_smoke.py --url "$(MCP_SMOKE_URL)" --timeout-seconds $(MCP_SMOKE_TIMEOUT_SECONDS)

mcp-benchmark:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/benchmark_mcp_queries.py --url "$(MCP_SMOKE_URL)" --timeout-seconds $(MCP_SMOKE_TIMEOUT_SECONDS) --limit $(MCP_BENCHMARK_LIMIT) --repeat $(MCP_BENCHMARK_REPEAT) --format $(MCP_BENCHMARK_FORMAT) --allow-empty $(MCP_BENCHMARK_EXTRA_ARGS)

mcp-cold-budget:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/benchmark_mcp_queries.py --url "$(MCP_SMOKE_URL)" --timeout-seconds $(MCP_SMOKE_TIMEOUT_SECONDS) --limit $(MCP_BENCHMARK_LIMIT) --repeat $(MCP_BENCHMARK_REPEAT) --format $(MCP_COLD_BUDGET_FORMAT) --allow-empty --clear-search-cache --use-cold-path-budget-defaults

mcp-prewarm:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/prewarm_mcp_cache.py --url "$(MCP_SMOKE_URL)" --timeout-seconds $(MCP_SMOKE_TIMEOUT_SECONDS) --limit $(MCP_PREWARM_LIMIT) --format $(MCP_PREWARM_FORMAT) $(MCP_PREWARM_VERIFY_HOT_ARGS)

mcp-prewarm-benchmark-defaults:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/prewarm_mcp_cache.py --url "$(MCP_SMOKE_URL)" --timeout-seconds $(MCP_SMOKE_TIMEOUT_SECONDS) --limit $(MCP_PREWARM_LIMIT) --format $(MCP_PREWARM_FORMAT) $(MCP_PREWARM_VERIFY_HOT_ARGS) --use-benchmark-defaults

mcp-postdeploy-prewarm:
	@if [ "$(MCP_POSTDEPLOY_PREWARM)" = "1" ]; then \
		$(MAKE) mcp-prewarm || printf "%s\n" "Warning: MCP cache prewarm failed; deploy remains active."; \
		if [ "$(MCP_POSTDEPLOY_PREWARM_BENCHMARK_DEFAULTS)" = "1" ]; then \
			$(MAKE) mcp-prewarm-benchmark-defaults || printf "%s\n" "Warning: MCP benchmark-default prewarm failed; deploy remains active."; \
		fi; \
	else \
		printf "%s\n" "Skipping MCP cache prewarm because MCP_POSTDEPLOY_PREWARM=$(MCP_POSTDEPLOY_PREWARM)"; \
	fi

mcp-runtime-config:
	$(MCP_COMPOSE_CMD) exec -T $(MCP_SERVICE) python scripts/diagnostics/runtime_config.py

mcp-wait-healthy:
	@elapsed=0; \
	while :; do \
		status=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $(MCP_SERVICE) 2>/dev/null || true); \
		if [ "$$status" = "healthy" ] || [ "$$status" = "none" ]; then \
			printf "%s\n" "watchfacts-mcp health status: $$status"; \
			exit 0; \
		fi; \
		if [ "$$status" = "unhealthy" ]; then \
			printf "%s\n" "watchfacts-mcp health status: unhealthy"; \
			exit 1; \
		fi; \
		if [ "$$elapsed" -ge "$(MCP_SMOKE_TIMEOUT_SECONDS)" ]; then \
			printf "%s\n" "watchfacts-mcp did not become healthy within $(MCP_SMOKE_TIMEOUT_SECONDS)s"; \
			exit 1; \
		fi; \
		sleep 3; \
		elapsed=$$((elapsed + 3)); \
	done

search-engine-predeploy-check:
	git diff --check
	$(PYTHON) -m pytest -q
	$(PYTHON) -m compileall app scripts
	$(PYTHON) scripts/diagnostics/audit_quality.py $(SEARCH_ENGINE_AUDIT_QUERIES) --limit $(SEARCH_ENGINE_AUDIT_LIMIT)

search-engine-postdeploy-check:
	$(MAKE) mcp-smoke-set
	$(MAKE) mcp-cold-budget
	$(MAKE) mcp-prewarm-benchmark-defaults
	$(MAKE) mcp-benchmark

search-engine-deploy-check: search-engine-predeploy-check search-engine-postdeploy-check

search-engine-baseline-snapshot:
	@set -e; \
	out="$(SEARCH_ENGINE_BASELINE_DIR)/$(SEARCH_ENGINE_BASELINE_LABEL)"; \
	mkdir -p "$$out"; \
	$(MAKE) mcp-runtime-config > "$$out/runtime-config.txt"; \
	$(MAKE) MCP_BENCHMARK_FORMAT=markdown MCP_BENCHMARK_EXTRA_ARGS= mcp-benchmark > "$$out/hot-benchmark.md"; \
	$(MAKE) MCP_BENCHMARK_FORMAT=markdown MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache mcp-benchmark > "$$out/cold-benchmark.md"; \
	$(MAKE) MCP_COLD_BUDGET_FORMAT=markdown mcp-cold-budget > "$$out/cold-budget.md"; \
	printf "%s\n" "SEARCH_ENGINE_BASELINE_DIR=$$out"; \
	ls -1 "$$out"

quality-audit:
	$(PYTHON) scripts/diagnostics/audit_quality.py --limit $(QUALITY_AUDIT_LIMIT)

ai-audit-triage:
	@extra=""; \
	if [ "$(AI_AUDIT_TRIAGE_OPENAI)" = "1" ]; then extra="--use-openai"; fi; \
	$(PYTHON) scripts/diagnostics/ai_audit_triage.py "$(AI_AUDIT_ARTIFACT)" --format "$(AI_AUDIT_TRIAGE_FORMAT)" $$extra

predeploy-quality-check: check quality-audit

check:
	git diff --check
	@if [ -d tests ]; then $(PYTHON) -m pytest -q; fi
	@paths=""; \
	for path in app scripts; do \
		if [ -d "$$path" ]; then paths="$$paths $$path"; fi; \
	done; \
	if [ -n "$$paths" ]; then $(PYTHON) -m compileall $$paths; fi
	@if command -v docker >/dev/null 2>&1; then \
		$(MCP_COMPOSE_CMD) config >/dev/null; \
	else \
		printf "%s\n" "Skipping Docker Compose config check because docker is not installed"; \
	fi

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete
