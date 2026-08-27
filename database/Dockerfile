# Runs Alembic migrations against Cloud SQL as a Cloud Run Job — deliberately
# NOT the same image as the "data" tier's API server (data/Dockerfile), which
# never bundles the migration files or a shell to run them from (Master Spec
# 03's rule: "Application startup must not silently run migrations").
#
# Built from the REPO ROOT, not this directory, because
# database/alembic/env.py resolves `data/chann_data` via a relative path
# (Path(__file__).resolve().parents[2] / "data") that only lines up correctly
# if this image's /srv mirrors the repo layout: /srv/database and
# /srv/data/chann_data as siblings.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY database/requirements.txt ./database/requirements.txt
RUN pip install --no-cache-dir -r database/requirements.txt

COPY database ./database
COPY data/chann_data ./data/chann_data

CMD ["sh", "-c", "cd database && python -m alembic upgrade head"]
