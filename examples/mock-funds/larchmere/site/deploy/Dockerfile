# Larchmere's image: the platform's image plus the fund's code (docs/deployment.md, "your own image").
# Built in Azure Container Registry: az acr build -r larchmereacr -t larchmere-qaos:1 .
# The ODBC driver is NOT in the platform's image; production needs msodbcsql18 and pyodbc (or
# mssql-python) installed here, which is why the SQL source is still a stand-in. See FINDINGS.
FROM larchmereacr.azurecr.io/quant-agent-os:0.7.0
COPY larchmere_clo.py larchmere_store.py larchmere_server.py /home/app/
ENV LARCHMERE_HOST=0.0.0.0 \
    QUANT_AGENT_OS_RUNS_FILE=/data/runs/runs.db \
    QUANT_AGENT_OS_ACCESS_FILE=/config/access.toml \
    QUANT_AGENT_OS_SIGN_IN_FILE=/config/sign-in.toml
ENTRYPOINT ["python", "/home/app/larchmere_server.py"]
CMD []
