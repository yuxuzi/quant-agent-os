# Quant-Agent-OS team server: the platform's research and analytics toolsets as MCP at /mcp
# and REST at /v1 (B-19, B-33, decision 0012, docs/deployment.md), over a SQLite file
# (compose.yaml) or PostgreSQL with pgvector (compose.postgres.yaml). The App Factory toolset
# is an optional extra and is not in the image.
#
# Build from the repository root:  docker build -f deploy/Dockerfile -t quant-agent-os .
# The image holds code only: no knowledge store, API keys, run store, or secrets. Those
# arrive as volumes at run time (see deploy/compose.yaml).
#
# Base images are pinned by digest. Move them forward deliberately, like the uv.lock cut-off.

ARG PYTHON_IMAGE=python:3.13.18-slim-trixie@sha256:76baad6037690d6e8c4397fbcaef17bca6f19eecef822b708fe8fa3417d30bb5
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first, from the lockfile only, so code changes reuse this layer.
COPY pyproject.toml uv.lock ./
COPY packages/quant-research/pyproject.toml packages/quant-research/
COPY packages/quant-analytics/pyproject.toml packages/quant-analytics/
COPY packages/quant-app-factory/pyproject.toml packages/quant-app-factory/
COPY apps/quant-platform/pyproject.toml apps/quant-platform/
RUN uv sync --locked --no-dev --no-install-workspace \
        --package quant-agent-os-platform --extra pdf --extra postgres
COPY README.md LICENSE ./
COPY src src
COPY packages/quant-research packages/quant-research
COPY packages/quant-analytics packages/quant-analytics
COPY apps/quant-platform apps/quant-platform
RUN uv sync --locked --no-dev --no-editable \
        --package quant-agent-os-platform --extra pdf --extra postgres

FROM ${PYTHON_IMAGE}
# A fixed non-root user. The data directories are created here, owned by that user, so named
# volumes mounted on them start with the right owner.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/knowledge /data/datasets /data/runs /keys \
    && chown app:app /data/knowledge /data/datasets /data/runs /keys
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUANT_AGENT_OS_API_KEYS_FILE=/keys/api-keys.toml \
    QUANT_AGENT_OS_DATASETS_DIR=/data/datasets
USER app
WORKDIR /home/app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
ENTRYPOINT ["quant-platform"]
CMD ["serve-http", "--store", "/data/knowledge/kb.db", "--runs", "/data/runs/runs.db", \
     "--host", "0.0.0.0", "--port", "8000"]
