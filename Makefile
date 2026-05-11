SHELL := /bin/sh

COMPOSE ?= docker compose
SERVICE ?= bot

.DEFAULT_GOAL := help

.PHONY: help init build up down restart logs ps shell run login check clean

help:
	@printf "%s\n" "watchfacts-bot commands"
	@printf "%s\n" ""
	@printf "%s\n" "  make init     Create local runtime directories and .env from .env.example when missing"
	@printf "%s\n" "  make build    Build Docker image"
	@printf "%s\n" "  make up       Start bot with Docker Compose"
	@printf "%s\n" "  make down     Stop Docker Compose services"
	@printf "%s\n" "  make restart  Restart bot service"
	@printf "%s\n" "  make logs     Follow bot logs"
	@printf "%s\n" "  make ps       Show Compose service status"
	@printf "%s\n" "  make shell    Open a shell in the bot container"
	@printf "%s\n" "  make run      Run bot locally on the host"
	@printf "%s\n" "  make login    Run WatchFacts browser login locally on the host"
	@printf "%s\n" "  make check    Run lightweight repository checks"
	@printf "%s\n" "  make clean    Remove local Python caches"

init:
	@mkdir -p data logs
	@if [ ! -f .env ]; then cp .env.example .env; fi

build:
	$(COMPOSE) build

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

check:
	git diff --check
	@if [ -d app ] || [ -d scripts ]; then python -m compileall app scripts; fi

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete
