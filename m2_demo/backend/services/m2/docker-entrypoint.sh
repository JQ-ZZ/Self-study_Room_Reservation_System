#!/bin/sh
set -eu

echo "[m2] running database migrations..."
alembic upgrade head

echo "[m2] starting api..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
