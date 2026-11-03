FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev && useradd --create-home app

USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "trophy_buddy_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
