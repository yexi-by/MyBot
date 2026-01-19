FROM python:3.13
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 安装 jemalloc 以优化内存分配和减少碎片
RUN apt-get update && apt-get install -y libjemalloc2 && rm -rf /var/lib/apt/lists/* && \
    ln -s /usr/lib/*/libjemalloc.so.2 /usr/lib/libjemalloc.so.2

ENV PYTHONUNBUFFERED=1
# 启用 jemalloc
ENV LD_PRELOAD="/usr/lib/libjemalloc.so.2"
ENV UV_PROJECT_ENVIRONMENT="/app/.venv"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY . .
CMD ["python", "-m", "app.main"]
