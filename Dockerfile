FROM node:24.22.1-bookworm-slim AS webui_build
WORKDIR /webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.14.8-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates btrfs-progs iptables util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home harness
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY qq_agent_harness/ ./qq_agent_harness/
RUN uv sync --locked --no-dev --no-editable && rm -rf /app/qq_agent_harness
COPY --from=webui_build /webui/dist/ ./webui/dist/
COPY config.example/ ./config.example/
COPY deploy/sandbox/ ./deploy/sandbox/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HARNESS_WEBUI_DIST=/app/webui/dist
USER 1000:1000
EXPOSE 6055
CMD ["qq-agent-harness", "--config", "/app/config/harness.toml"]
