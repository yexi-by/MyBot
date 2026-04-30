FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 镜像提供项目运行时和常见 MCP stdio 启动器；具体 MCP server 由部署配置决定。
# Node 与 Docker CLI 来自上游官方源，保证 stdio 工具链具备完整命令能力。
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    gnupg \
    libjemalloc2 \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list \
    && curl -fsSL https://deb.nodesource.com/setup_current.x -o /tmp/nodesource_setup.sh \
    && bash /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    nodejs \
    && npm install -g npm@latest corepack@latest \
    && corepack enable \
    && corepack prepare pnpm@latest --activate \
    && corepack prepare yarn@stable --activate \
    && rm -rf /var/lib/apt/lists/* /tmp/nodesource_setup.sh \
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
COPY app ./app

CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "app.main"]
