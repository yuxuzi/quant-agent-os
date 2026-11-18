# Wrenfield's image: the platform's image plus the fund's code, DuckDB for the lake, and pyarrow.
# Not run in tests. The platform's own Dockerfile installs the `postgres` extra but neither
# `parquet` (pyarrow) nor duckdb, so this image adds them (see FINDINGS.md, Cloud).
#
#   docker build -f deploy/Dockerfile.wrenfield -t 123456789012.dkr.ecr.us-east-1.amazonaws.com/wrenfield-qaos:1 .
FROM quant-agent-os:local
COPY wrenfield_credit.py wrenfield_store.py wrenfield_server.py wrenfield_introspection_shim.py /home/app/
COPY wrenfield_sign_in.toml /etc/qaos/sign-in.toml
COPY access.toml /etc/qaos/access.toml  # from facility/
ENV WRENFIELD_HOST=0.0.0.0 \
    QUANT_AGENT_OS_RUNS_FILE=/data/runs/runs.db \
    QUANT_AGENT_OS_ACCESS_FILE=/etc/qaos/access.toml \
    QUANT_AGENT_OS_SIGN_IN_FILE=/etc/qaos/sign-in.toml
ENTRYPOINT ["python", "/home/app/wrenfield_server.py"]
CMD []
