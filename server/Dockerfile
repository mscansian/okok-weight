FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile alone, so editing the server does not
# rebuild them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY scale_server ./scale_server
RUN uv sync --locked --no-dev


FROM python:3.14-slim

# /data is a bind mount owned by the deploy host's login user, uid 1000, and a
# bind mount keeps the host's ownership — so that is the uid the process runs as.
RUN groupadd --gid 1000 scale \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /app scale \
 && install --directory --owner scale --group scale /data

COPY --from=build --chown=scale:scale /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SCALE_DB_PATH=/data/weights.db \
    SCALE_HOST=0.0.0.0 \
    SCALE_PORT=8080

# The weigh-in history is the one thing worth surviving the container.
VOLUME /data
EXPOSE 8080
USER scale
WORKDIR /app

# The health route answers only once SQLite has, so an unwritable volume shows up here.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2)"]

CMD ["scale-server"]
