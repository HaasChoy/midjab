#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${SCRIPT_DIR}"
docker compose -f docker-compose.yml up -d

cd "${PROJECT_ROOT}"
python db/init_db.py

echo "V3 stack started. PostgreSQL on :5432 and pgAdmin on :8080."
