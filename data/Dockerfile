FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chann_data ./chann_data

# Cloud Run supplies PORT. Do not hardcode 8080 in the app.
ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn chann_data.main:app --host 0.0.0.0 --port \"${PORT}\""]
