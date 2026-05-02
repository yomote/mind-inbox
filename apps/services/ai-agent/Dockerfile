FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

COPY app/ app/

EXPOSE 8000

CMD ["uv", "run", "--no-project", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
