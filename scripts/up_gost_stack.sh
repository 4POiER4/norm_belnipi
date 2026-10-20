#!/usr/bin/env bash
# GOST stack: web + PostgreSQL, tile OCR, Стройпрофи + NormaCS. No GPU / Ollama required.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — add PDF_STROIPROFI_LOGIN/PDF_STROIPROFI_PASSWORD for Стройпрофи checks."
fi

mkdir -p scan data/tmp

docker compose -f docker-compose.yml -f docker-compose.fast.yml up -d --build "$@"

echo ""
echo "=== Ready ==="
echo "Web: http://localhost:${WEB_PORT:-8090}"
echo "Logs: docker compose logs -f web"
echo "Tests: docker compose exec web python -m pytest tests/ -q"
