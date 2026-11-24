# Wrenfield's image: the platform's image plus the fund's code, DuckDB for the lake, and pyarrow.
# Not run in tests. The platform's own Dockerfile installs the `postgres` extra but neither
# `parquet` (pyarrow) nor duckdb, so a stage installs both with uv, which the image does not keep
# (docs/deployment.md, "Drivers in your image"). A failed install fails the build.
#
#   docker build -f deploy/Dockerfile.wrenfield -t 123456789012.dkr.ecr.us-east-1.amazonaws.com/wrenfield-qaos:1 .
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc
FROM ${UV_IMAGE} AS uv

FROM quant-agent-os:local AS lake
USER root
COPY --from=uv /uv /usr/local/bin/uv
RUN uv pip install --no-cache --python /opt/venv/bin/python "duckdb>=2,<3" "pyarrow>=26,<27"

FROM quant-agent-os:local
COPY --from=lake /opt/venv /opt/venv
COPY wrenfield_credit.py wrenfield_store.py wrenfield_server.py /home/app/
COPY wrenfield_sign_in.toml /etc/qaos/sign-in.toml
COPY access.toml /etc/qaos/access.toml  # from facility/
ENV WRENFIELD_HOST=0.0.0.0 \
    QUANT_AGENT_OS_RUNS_FILE=/data/runs/runs.db \
    QUANT_AGENT_OS_ACCESS_FILE=/etc/qaos/access.toml \
    QUANT_AGENT_OS_SIGN_IN_FILE=/etc/qaos/sign-in.toml
ENTRYPOINT ["python", "/home/app/wrenfield_server.py"]
CMD []
