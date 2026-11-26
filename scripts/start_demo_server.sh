#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="${PROJECT_DIR}/.demo"
DEMO_PORT="${DEMO_PORT:-8766}"

mkdir -p "${DEMO_DIR}"

export DEBUG=True
export DEMO_DATA_ALLOWED=True
export DEMO_DATA_PASSWORD="${DEMO_DATA_PASSWORD:-Demo-OlhoCao-2026!}"
export DATABASE_URL="sqlite:///${DEMO_DIR}/db.sqlite3"
export ALLOWED_HOSTS="localhost,127.0.0.1"
export CSRF_TRUSTED_ORIGINS="http://localhost:${DEMO_PORT},http://127.0.0.1:${DEMO_PORT}"
export PUBLIC_BASE_URL="http://127.0.0.1:${DEMO_PORT}"

# A demo database must never contact payment, fiscal or email providers.
export EASYPAY_ACCOUNT_ID=""
export EASYPAY_API_KEY=""
export EASYPAY_WEBHOOK_USERNAME=""
export EASYPAY_WEBHOOK_PASSWORD=""
export TOCONLINE_OAUTH_CLIENT_ID=""
export TOCONLINE_OAUTH_CLIENT_SECRET=""
export TOCONLINE_BASE_URL="http://127.0.0.1:9"
export EMAIL_HOST="127.0.0.1"
export EMAIL_PORT="9"

cd "${PROJECT_DIR}"

poetry run python manage.py migrate --noinput
poetry run python manage.py seed_demo_data

printf '\nDemo local: http://127.0.0.1:%s/\n' "${DEMO_PORT}"
printf 'Utilizadores: demo_admin, demo_manager, demo_ana, demo_bruno\n'
printf 'Password: valor configurado em DEMO_DATA_PASSWORD (consulte a documentação).\n\n'

exec poetry run python manage.py runserver "127.0.0.1:${DEMO_PORT}"
