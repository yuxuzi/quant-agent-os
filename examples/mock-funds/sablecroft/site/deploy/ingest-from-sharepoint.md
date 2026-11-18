How an operator ingests the fund's documents (ingestion is an operator command, never a tool).

Due-diligence memos, ODD notes, IC minutes and the quarterly letter are written in SharePoint and
exported nightly by a Power Automate flow to `abfss://raw@sablecroftlake.dfs.core.windows.net/research/`
as Markdown and PDF. The platform's `qaos ingest` takes a local folder (or a file), not an abfss URI, so:

    azcopy sync "https://sablecroftlake.blob.core.windows.net/raw/research" ./corpus --delete-destination=true
    qaos ingest ./corpus --store "postgresql://qaos_ingest@sablecroft-pg.postgres.database.azure.com:5432/qaos?sslmode=require"

run as a Container Apps job on a schedule, authenticated by the job's managed identity (azcopy login
--identity) and with the ingest role's password from Key Vault. Not run in tests.
