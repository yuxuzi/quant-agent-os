# The firm's image: the platform's image plus the firm's code. Build the platform's image first
# (docker compose -f deploy/compose.yaml build). Its restrictions, user, and volumes stay the same.
#
#   docker build -t firm-qaos:1 examples/firm-tools
#
# If your tools need packages, such as a database driver, use the recipe in deploy/Dockerfile.firm:
# uv installs them in a stage, and a failed install fails the build (docs/deployment.md, "Drivers in
# your image").
FROM quant-agent-os:local
COPY firm_data.py firm_tools.py firm_server.py /home/app/
ENV FIRM_HOST=0.0.0.0 \
    QUANT_AGENT_OS_RUNS_FILE=/data/runs/runs.db \
    QUANT_AGENT_OS_STORE=/data/knowledge/kb.db
ENTRYPOINT ["python", "/home/app/firm_server.py"]
CMD []
