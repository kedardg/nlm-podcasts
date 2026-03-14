# Stage 1: Builder — install Node.js, uv, Python venv, openclaw, nlm
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install openclaw globally
RUN npm install -g openclaw@latest

# Install nlm CLI
RUN uv tool install notebooklm-mcp-cli

# Create Python venv and install dependencies from requirements.txt
# Filter out mlx-lm (Apple Silicon only — classifier falls back to domain rules in Docker)
COPY requirements.txt /tmp/requirements.txt
RUN uv venv /app/.venv && \
    grep -v 'mlx-lm' /tmp/requirements.txt > /tmp/requirements-docker.txt && \
    uv pip install --python /app/.venv/bin/python3 -r /tmp/requirements-docker.txt

# Stage 2: Runtime — lean image with system deps + copied artifacts
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf2.0-0 \
        pandoc ffmpeg chromium bash && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Copy openclaw global install from builder and re-link binary
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ln -sf ../lib/node_modules/openclaw/openclaw.mjs /usr/bin/openclaw

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy uv + nlm from builder
COPY --from=builder /root/.local /root/.local
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH"

# Copy project source
WORKDIR /app
COPY . /app

# Environment
ENV PROJECT_ROOT=/app \
    CHROME_PATH=/usr/bin/chromium \
    PYTHONUNBUFFERED=1

# Install Playwright browser binaries
RUN /app/.venv/bin/python3 -m playwright install --with-deps chromium

# Data and RAG directories (expected to be bind-mounted)
RUN mkdir -p /app/data /app/data/daily-feed /app/rag /app/tmp

CMD ["openclaw", "daemon", "start", "--foreground"]
