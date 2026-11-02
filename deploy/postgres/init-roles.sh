#!/bin/sh
# Runs once, when the PostgreSQL volume is first created (docker-entrypoint-initdb.d).
# qaos_ingest owns the knowledge store's schema; qaos_reader, which the server uses, can only
# read it, in read-only transactions. pgvector is created here, by the superuser, so neither
# role needs that privilege.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v db="$POSTGRES_DB" \
    -v ingest_password="$QAOS_INGEST_PASSWORD" \
    -v reader_password="$QAOS_READER_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE ROLE qaos_ingest LOGIN PASSWORD :'ingest_password';
CREATE ROLE qaos_reader LOGIN PASSWORD :'reader_password';
GRANT CREATE ON DATABASE :"db" TO qaos_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE qaos_ingest GRANT USAGE ON SCHEMAS TO qaos_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE qaos_ingest GRANT SELECT ON TABLES TO qaos_reader;
ALTER ROLE qaos_reader SET default_transaction_read_only = on;
SQL
