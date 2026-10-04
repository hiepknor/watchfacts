SHELL := /bin/sh

COMPOSE ?= docker compose
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
IMAGE ?= watchfacts:local
BOT_SERVICE ?= watchfacts-bot
WEB_SERVICE ?= watchfacts-web
LEGACY_BOT_CONTAINER ?= watchfacts
LEGACY_MCP_CONTAINER ?= watchfacts-mcp
LOG_LINES ?= 80
SKIP_PULL ?= 0
SMOKE_QUERY ?= 5712g
QUALITY_AUDIT_LIMIT ?= 5
SEARCH_ENGINE_AUDIT_LIMIT ?= 5
SEARCH_ENGINE_AUDIT_QUERIES ?= "rm07-01 rg snow" "rm07-01 rose gold" "rm07-01 white gold" "rm07-01 mother of pearl"
SEARCH_ENGINE_BASELINE_DIR ?= logs/search-engine-baseline
SEARCH_ENGINE_BASELINE_LABEL ?= $(shell date +%Y%m%d-%H%M%S)
WEB_HEALTH_TIMEOUT_SECONDS ?= 120
SEARCH_BENCHMARK_FORMAT ?= markdown
SEARCH_BENCHMARK_LIMIT ?= 3
SEARCH_BENCHMARK_REPEAT ?= 1
SEARCH_BENCHMARK_EXTRA_ARGS ?=
SEARCH_COLD_BUDGET_FORMAT ?= markdown
SEARCH_PREWARM_FORMAT ?= text
SEARCH_PREWARM_LIMIT ?= 5
SEARCH_PREWARM_VERIFY_HOT ?= 1
SEARCH_POSTDEPLOY_PREWARM ?= 1
SEARCH_POSTDEPLOY_PREWARM_BENCHMARK_DEFAULTS ?= 1
AI_AUDIT_ARTIFACT ?= audit-report.jsonl
AI_AUDIT_TRIAGE_FORMAT ?= markdown
AI_AUDIT_TRIAGE_OPENAI ?= 0
PRODUCTION_HOST ?=
PRODUCTION_REPO_PATH ?= /opt/watchfacts

export IMAGE

.DEFAULT_GOAL := help

.PHONY: help init verify-env verify-bot-env pull build predeploy-check deploy deploy-bot deploy-web deploy-bot-web update up down restart logs ps shell run login check clean web-build web-predeploy-check web-up web-down web-restart web-logs web-ps watchfacts-http-smoke runtime-smoke search-benchmark search-cold-budget search-prewarm search-prewarm-benchmark-defaults search-postdeploy-prewarm runtime-config web-wait-healthy search-engine-predeploy-check search-engine-postdeploy-check search-engine-deploy-check search-engine-baseline-snapshot production-postdeploy-check production-head-print quality-audit ai-audit-triage predeploy-quality-check

help:
	@printf "%s\n" "watchfacts commands"
	@printf "%s\n" ""
	@printf "%s\n" "  make init     Create local runtime directories and .env from .env.example when missing"
	@printf "%s\n" "  make verify-env Check server runtime files before deploy"
	@printf "%s\n" "  make pull     Pull latest git changes unless SKIP_PULL=1"
	@printf "%s\n" "  make build    Build Docker image"
	@printf "%s\n" "  make predeploy-check Run tests and repository checks before deploy"
	@printf "%s\n" "  make deploy   Deploy watchfacts-bot and watchfacts-web"
	@printf "%s\n" "  make deploy-bot Deploy watchfacts-bot only"
	@printf "%s\n" "  make deploy-web Deploy watchfacts-web only (build, prechecks, recreate)"
	@printf "%s\n" "  make deploy-bot-web Alias for deploy"
	@printf "%s\n" "  make update   Alias for deploy"
	@printf "%s\n" "  make up       Start watchfacts-bot with Docker Compose"
	@printf "%s\n" "  make down     Stop Docker Compose services"
	@printf "%s\n" "  make restart  Restart watchfacts-bot service"
	@printf "%s\n" "  make logs     Follow watchfacts-bot logs"
	@printf "%s\n" "  make ps       Show Compose service status"
	@printf "%s\n" "  make shell    Open a shell in the watchfacts-bot container"
	@printf "%s\n" "  make run      Run watchfacts-bot locally on the host"
	@printf "%s\n" "  make login    Run WatchFacts browser login locally on the host"
	@printf "%s\n" "  make web-up         Start watchfacts-web"
	@printf "%s\n" "  make web-down       Stop watchfacts-web"
	@printf "%s\n" "  make web-restart    Restart watchfacts-web"
	@printf "%s\n" "  make web-logs       Follow watchfacts-web logs"
	@printf "%s\n" "  make web-ps         Show watchfacts-web status"
	@printf "%s\n" "  make watchfacts-http-smoke Run one authorized HTTPX search smoke check"
	@printf "%s\n" "  make runtime-smoke  Validate shared search runtime responses"
	@printf "%s\n" "  make search-benchmark Benchmark representative search queries"
	@printf "%s\n" "  make search-cold-budget Benchmark focused cold-path retrieval queries"
	@printf "%s\n" "  make search-prewarm Prewarm representative search cache entries"
	@printf "%s\n" "  make runtime-config Print safe effective runtime config values"
	@printf "%s\n" "  make search-engine-predeploy-check Run local search-engine deploy gate"
	@printf "%s\n" "  make search-engine-postdeploy-check Verify health, production checkout HEAD, smoke, and benchmark"
	@printf "%s\n" "  make search-engine-deploy-check Run both search-engine deploy gates"
	@printf "%s\n" "  make production-postdeploy-check Health + HEAD verification + focused audit after deploy"
	@printf "%s\n" "  make production-head-print Print local/remote production git HEAD"
	@printf "%s\n" "  make search-engine-baseline-snapshot Capture runtime config plus hot/cold search benchmark artifacts"
	@printf "%s\n" "  make quality-audit  Run the default production quality audit query set"
	@printf "%s\n" "  make ai-audit-triage Summarize an audit artifact, optionally with OpenAI"
	@printf "%s\n" "  make predeploy-quality-check Run local checks plus the default quality audit"
	@printf "%s\n" "  make check    Run repository checks"
	@printf "%s\n" "  make clean    Remove local Python caches"

init:
	@mkdir -p data/browser data/database data/result_pages logs
	@$(PYTHON) scripts/ops/migrate_runtime_layout.py
	@if [ ! -f .env ]; then cp .env.example .env; fi

verify-env: init
	@test -s .env || { printf "%s\n" "Missing .env. Run make init and edit .env."; exit 1; }

verify-bot-env: verify-env
	@test -s data/browser/watchfacts_state.json || { printf "%s\n" "Missing data/browser/watchfacts_state.json. Run make login on a machine with browser access."; exit 1; }
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

deploy: deploy-bot-web

deploy-bot: verify-bot-env pull build predeploy-check
	@docker rm -f $(LEGACY_BOT_CONTAINER) 2>/dev/null || true
	$(COMPOSE) up -d --force-recreate --remove-orphans $(BOT_SERVICE)
	$(COMPOSE) ps
	$(COMPOSE) logs --tail=$(LOG_LINES) $(BOT_SERVICE)

deploy-web: verify-env pull web-build web-predeploy-check
	@docker rm -f $(LEGACY_MCP_CONTAINER) 2>/dev/null || true
	$(COMPOSE) up -d --force-recreate --remove-orphans $(WEB_SERVICE)
	$(COMPOSE) ps
	$(MAKE) web-wait-healthy
	$(MAKE) search-postdeploy-prewarm
	$(COMPOSE) logs --tail=$(LOG_LINES) $(WEB_SERVICE)

deploy-bot-web: deploy-bot deploy-web

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

SEARCH_PREWARM_VERIFY_HOT_ARGS = $(if $(filter 1 true yes on,$(SEARCH_PREWARM_VERIFY_HOT)),--verify-hot,)

web-build:
	$(COMPOSE) build $(WEB_SERVICE)

web-predeploy-check:
	git diff --check
	$(PYTHON) -m pytest -q tests/test_web_server.py tests/test_result_pages.py tests/test_runtime_health.py tests/test_docker_compose.py
	$(COMPOSE) run --rm $(WEB_SERVICE) python -m compileall app

web-up:
	$(COMPOSE) up -d --build $(WEB_SERVICE)

web-down:
	$(COMPOSE) stop $(WEB_SERVICE)

web-restart:
	$(COMPOSE) restart $(WEB_SERVICE)

web-logs:
	$(COMPOSE) logs -f $(WEB_SERVICE)

web-ps:
	$(COMPOSE) ps $(WEB_SERVICE)

watchfacts-http-smoke:
	$(PYTHON) scripts/diagnostics/benchmark_watchfacts_http.py --query "$(SMOKE_QUERY)" --warmup --repeat 1

runtime-smoke:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/runtime_smoke.py --timeout-seconds $(WEB_HEALTH_TIMEOUT_SECONDS)

search-benchmark:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/benchmark_search_queries.py --timeout-seconds $(WEB_HEALTH_TIMEOUT_SECONDS) --limit $(SEARCH_BENCHMARK_LIMIT) --repeat $(SEARCH_BENCHMARK_REPEAT) --format $(SEARCH_BENCHMARK_FORMAT) --allow-empty $(SEARCH_BENCHMARK_EXTRA_ARGS)

search-cold-budget:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/benchmark_search_queries.py --timeout-seconds $(WEB_HEALTH_TIMEOUT_SECONDS) --limit $(SEARCH_BENCHMARK_LIMIT) --repeat $(SEARCH_BENCHMARK_REPEAT) --format $(SEARCH_COLD_BUDGET_FORMAT) --allow-empty --clear-search-cache --use-cold-path-budget-defaults

search-prewarm:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/prewarm_search_cache.py --timeout-seconds $(WEB_HEALTH_TIMEOUT_SECONDS) --limit $(SEARCH_PREWARM_LIMIT) --format $(SEARCH_PREWARM_FORMAT) $(SEARCH_PREWARM_VERIFY_HOT_ARGS)

search-prewarm-benchmark-defaults:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/prewarm_search_cache.py --timeout-seconds $(WEB_HEALTH_TIMEOUT_SECONDS) --limit $(SEARCH_PREWARM_LIMIT) --format $(SEARCH_PREWARM_FORMAT) $(SEARCH_PREWARM_VERIFY_HOT_ARGS) --use-benchmark-defaults

search-postdeploy-prewarm:
	@if [ "$(SEARCH_POSTDEPLOY_PREWARM)" = "1" ]; then \
		$(MAKE) search-prewarm || printf "%s\n" "Warning: search cache prewarm failed; deploy remains active."; \
		if [ "$(SEARCH_POSTDEPLOY_PREWARM_BENCHMARK_DEFAULTS)" = "1" ]; then \
			$(MAKE) search-prewarm-benchmark-defaults || printf "%s\n" "Warning: benchmark-default prewarm failed; deploy remains active."; \
		fi; \
	else \
		printf "%s\n" "Skipping search cache prewarm because SEARCH_POSTDEPLOY_PREWARM=$(SEARCH_POSTDEPLOY_PREWARM)"; \
	fi

runtime-config:
	$(COMPOSE) exec -T $(BOT_SERVICE) python scripts/diagnostics/runtime_config.py

web-wait-healthy:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "Docker CLI not found; install Docker before running production health checks."; \
		exit 1; \
	fi; \
	if ! docker info >/dev/null 2>&1; then \
		echo "Docker daemon unavailable. Start Docker and retry."; \
		exit 1; \
	fi; \
	elapsed=0; \
	while :; do \
		status=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $(WEB_SERVICE) 2>/dev/null || true); \
		if [ "$$status" = "healthy" ] || [ "$$status" = "none" ]; then \
			printf "%s\n" "watchfacts-web health status: $$status"; \
			exit 0; \
		fi; \
		if [ "$$status" = "unhealthy" ]; then \
			printf "%s\n" "watchfacts-web health status: unhealthy"; \
			exit 1; \
		fi; \
		if [ "$$elapsed" -ge "$(WEB_HEALTH_TIMEOUT_SECONDS)" ]; then \
			printf "%s\n" "watchfacts-web did not become healthy within $(WEB_HEALTH_TIMEOUT_SECONDS)s"; \
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
	$(MAKE) web-wait-healthy
	$(MAKE) production-head-print
	$(MAKE) runtime-smoke
	$(MAKE) search-cold-budget
	$(MAKE) search-prewarm-benchmark-defaults
	$(MAKE) search-benchmark
	$(MAKE) quality-audit

production-postdeploy-check: search-engine-postdeploy-check

production-head-print:
	@local_head="$$(git rev-parse --short HEAD)"; \
	current_path="$(abspath $(CURDIR))"; \
	production_path="$(abspath $(PRODUCTION_REPO_PATH))"; \
	echo "Local checkout HEAD: $$local_head"; \
	if [ -n "$(PRODUCTION_HOST)" ] && [ "$$current_path" = "$$production_path" ]; then \
		echo "Remote production HEAD: $$local_head (current production checkout; self-SSH skipped)"; \
	elif [ -n "$(PRODUCTION_HOST)" ]; then \
		remote_head="$$(ssh $(PRODUCTION_HOST) 'cd $(PRODUCTION_REPO_PATH) && git rev-parse --short HEAD')"; \
		echo "Remote production HEAD: $$remote_head"; \
	else \
		echo "PRODUCTION_HOST not set; remote HEAD check skipped."; \
	fi

search-engine-deploy-check: search-engine-predeploy-check search-engine-postdeploy-check

search-engine-baseline-snapshot:
	@set -e; \
	out="$(SEARCH_ENGINE_BASELINE_DIR)/$(SEARCH_ENGINE_BASELINE_LABEL)"; \
	mkdir -p "$$out"; \
	$(MAKE) runtime-config > "$$out/runtime-config.txt"; \
	$(MAKE) SEARCH_BENCHMARK_FORMAT=markdown SEARCH_BENCHMARK_EXTRA_ARGS= search-benchmark > "$$out/hot-benchmark.md"; \
	$(MAKE) SEARCH_BENCHMARK_FORMAT=markdown SEARCH_BENCHMARK_EXTRA_ARGS=--clear-search-cache search-benchmark > "$$out/cold-benchmark.md"; \
	$(MAKE) SEARCH_COLD_BUDGET_FORMAT=markdown search-cold-budget > "$$out/cold-budget.md"; \
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
		$(COMPOSE) config >/dev/null; \
	else \
		printf "%s\n" "Skipping Docker Compose config check because docker is not installed"; \
	fi

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete
