#!/bin/sh
set -e

echo "==> Running database migrations..."
alembic upgrade head

echo "==> Bootstrapping roles, menus, and initial admin..."
python -m scripts.bootstrap_admin

echo "==> Starting GPA ERP backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}"
