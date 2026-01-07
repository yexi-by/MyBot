FROM python:3.13
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT="/app/.venv"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY . .
CMD ["python", "-m", "app.main"]
