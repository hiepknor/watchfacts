FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM base AS bot

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        gnupg \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

RUN mkdir -p \
    /app/runtime/browser \
    /app/runtime/database \
    /app/runtime/result_pages \
    /app/logs

CMD ["python", "-m", "app.main"]

FROM base AS web

COPY requirements-web.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements-web.txt

COPY app ./app

RUN mkdir -p /app/runtime/database /app/runtime/result_pages

CMD ["python", "-m", "app.web_server"]
