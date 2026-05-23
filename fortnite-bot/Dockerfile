# syntax=docker/dockerfile:1.7
# ---- builder stage ----------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for psycopg2 / lxml / asyncpg compile fallbacks
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
# Install into an isolated prefix we can copy into the runtime stage
RUN pip install --prefix=/install -r requirements.txt

# ---- runtime stage ----------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/install/bin:$PATH \
    PYTHONPATH=/install/lib/python3.11/site-packages \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Runtime deps:
#   - libpq5  → psycopg2 / asyncpg
#   - curl    → healthcheck
#   - chromium runtime libs → Playwright headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-dejavu-core \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libxss1 \
        libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /install

# Install Playwright Chromium (skip Firefox/WebKit to keep image small)
RUN python -m playwright install chromium

WORKDIR /app
COPY app/ ./app/
COPY scripts/ ./scripts/
RUN mkdir -p /app/images

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
