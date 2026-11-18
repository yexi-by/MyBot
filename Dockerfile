FROM node:24.22.1-bookworm-slim AS node_base

FROM node_base AS webui_build
WORKDIR /webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.14.8-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node_base /usr/local/bin/node /usr/local/bin/node
COPY --from=node_base /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      ripgrep \
      fd-find \
      jq \
      file \
      procps \
      zip \
      unzip \
      xz-utils \
      zstd \
      bzip2 \
      7zip \
      ffmpeg \
      libcairo2 \
      poppler-utils \
      qpdf \
      sqlite3 \
      fontconfig \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
 && ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
 && ln -sf /usr/bin/7zz /usr/local/bin/7z \
 && fc-cache -fv \
 && rm -rf /var/lib/apt/lists/* \
 && uv pip install --system --no-cache \
      requests \
      httpx \
      beautifulsoup4 \
      lxml \
      pyyaml \
      pillow \
      matplotlib \
      cairosvg \
      resvg-py \
      numpy \
      pandas \
      pypdf \
      pdfplumber \
      openpyxl \
      reportlab \
      markdown \
      qrcode \
      imageio \
 && useradd --uid 1000 --create-home harness \
 && mkdir -p /workspace \
 && chown 1000:1000 /workspace
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY qq_agent_harness/ ./qq_agent_harness/
RUN uv sync --locked --no-dev --no-editable && rm -rf /app/qq_agent_harness
COPY --from=webui_build /webui/dist/ ./webui/dist/
COPY config.example/ ./config.example/
RUN chown -R 0:0 /app \
 && chmod 0700 /app \
 && chmod 0700 /workspace \
 && (find /usr /bin /sbin /lib -xdev -perm /6000 -type f -exec chmod a-s {} + || true)
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HARNESS_WEBUI_DIST=/app/webui/dist HARNESS_WORKSPACE_UID=1000 HARNESS_WORKSPACE_GID=1000
USER 0:0
EXPOSE 6055
CMD ["qq-agent-harness", "--config", "/app/config/harness.toml"]
