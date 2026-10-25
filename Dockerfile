FROM ghcr.io/github/github-mcp-server@sha256:881b53d6f75f69bdbc1b5b10fc2f1361717c19054143b3a8529fb5c32061a50e AS github_mcp

FROM node:24-bookworm-slim AS webui_build

# WebUI 前端在镜像内构建，产物由 FastAPI 直接伺服。
WORKDIR /build/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=github_mcp /server/github-mcp-server /usr/local/bin/github-mcp-server

# 运行镜像只保留 Python 应用与实际使用的 GitHub MCP server。
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libjemalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/*/libjemalloc.so.2 /usr/lib/libjemalloc.so.2

ENV LANG="C.UTF-8"
ENV LC_ALL="C.UTF-8"
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING="utf-8"
ENV PYTHONUNBUFFERED=1
ENV LD_PRELOAD="/usr/lib/libjemalloc.so.2"
ENV UV_PROJECT_ENVIRONMENT="/app/.venv"
ENV UV_CACHE_DIR="/app/.uv-cache"
ENV UV_LINK_MODE="copy"
ENV UV_COMPILE_BYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev \
    && rm -rf /app/.uv-cache
COPY alembic.ini ./alembic.ini
COPY app ./app
COPY --from=webui_build /build/webui/dist ./webui/dist

CMD ["python", "-m", "app.main"]
