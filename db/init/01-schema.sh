#!/bin/bash
# Runs once on first Postgres container start (docker-entrypoint-initdb.d).
# Only enables extensions: the backend creates and migrates all tables at startup (backend/app/schema.py and the
# MIGRATIONS lists), so Docker and the native Windows setup share a single schema definition.
# EMBEDDING_DIM (used by the backend) must match the embedding model you configure (1536 = OpenAI
# text-embedding-3-small, 768 = Ollama nomic-embed-text).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
SQL
