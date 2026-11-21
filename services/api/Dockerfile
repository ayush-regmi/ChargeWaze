# ChargeWaze API image (P6-F3, docs/deployment.md).
#
# Built by Render from render.yaml and by CI's docker-build job. Runtime
# dependencies only (no dev group); configuration comes from the environment,
# never from a baked-in .env (see .dockerignore).

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so code-only changes reuse this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev \
    && useradd --system --uid 10001 --no-create-home chargewaze

USER chargewaze

# Render sets PORT; 8000 is the local default. --proxy-headers so client IPs and
# https scheme come from Render's proxy. Migrations are not run here: the
# deploy workflow runs `alembic upgrade head` before it ships a new image.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
