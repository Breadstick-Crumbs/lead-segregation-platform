# Build stage: resolve the locked dependencies into a virtualenv with uv.
FROM python:3.11-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime stage: the virtualenv and the code, no uv or build tools, as a non-root user.
FROM python:3.11-slim
ARG APP_RELEASE=""
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app PATH=/app/.venv/bin:$PATH \
    APP_RELEASE=$APP_RELEASE \
    # Reverse proxies on the Docker networks (nginx, Caddy) may set X-Forwarded-For; uvicorn
    # takes the client address from the last hop outside these ranges (rate limits use it).
    FORWARDED_ALLOW_IPS=127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --chown=root:root . .
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4)"]
# uvicorn-log.json hands uvicorn's own lines to the app's handler, so they are JSON too.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", \
     "--no-server-header", "--log-config", "uvicorn-log.json"]
