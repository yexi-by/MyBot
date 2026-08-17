FROM node:24-bookworm-slim AS node_runtime

FROM ghcr.io/github/github-mcp-server@sha256:881b53d6f75f69bdbc1b5b10fc2f1361717c19054143b3a8529fb5c32061a50e AS github_mcp

FROM node_runtime AS webui_build

# WebUI 前端在镜像内构建，产物由 FastAPI 直接伺服。
WORKDIR /build/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=node_runtime /usr/local/ /usr/local/
COPY --from=github_mcp /server/github-mcp-server /usr/local/bin/github-mcp-server

# 镜像提供项目运行时、Node MCP stdio 启动器和官方 GitHub MCP server。
# Node 官方镜像的 Yarn 软链接指向未复制的 /opt；交给 Corepack 重建。
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    libjemalloc2 \
    && rm -f /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && corepack enable \
    && corepack prepare pnpm@latest --activate \
    && corepack prepare yarn@stable --activate \
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
COPY pyproject.toml uv.lock package.json package-lock.json README.md ./
# 锁文件里 tldjs 的 postinstall 只在显式联网更新规则时工作，运行时不消费。
RUN uv sync --frozen --no-dev \
    && npm ci --omit=dev --ignore-scripts --loglevel=error \
    && npm audit --omit=dev --audit-level=low --loglevel=error \
    && rm -rf /app/.uv-cache
COPY alembic.ini ./alembic.ini
COPY app ./app
COPY --from=webui_build /build/webui/dist ./webui/dist

CMD ["python", "-m", "app.main"]
