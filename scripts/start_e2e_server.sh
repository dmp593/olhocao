#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
e2e_directory="${project_root}/.e2e"
e2e_database="${e2e_directory}/db.sqlite3"

if [[ -n "${E2E_PYTHON:-}" ]]; then
    python_executable="${E2E_PYTHON}"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    python_executable="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${project_root}/.venv/bin/python" ]]; then
    python_executable="${project_root}/.venv/bin/python"
else
    python_executable="$(command -v python3)"
fi

if [[ ! -x "${python_executable}" ]]; then
    echo "A Python environment with the project dependencies is required." >&2
    exit 1
fi

mkdir -p "${e2e_directory}"
rm -f "${e2e_database}"

export DATABASE_URL="sqlite:///${e2e_database}"
export DEBUG="True"
export E2E_TESTING="True"
export PUBLIC_BASE_URL="http://127.0.0.1:8765"
export SECRET_KEY="e2e-only-secret-key-that-is-never-used-outside-the-test-server"
export PYTHONPATH="${project_root}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${project_root}"
"${python_executable}" manage.py migrate --noinput
"${python_executable}" tests/e2e/seed_data.py
exec "${python_executable}" manage.py runserver --noreload 127.0.0.1:8765
