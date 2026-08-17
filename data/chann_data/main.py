"""Data Tier — the only tier permitted to touch PostgreSQL or Redis."""
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .cache import cache
from .config import settings
from .db import engine
from .routers import internal
from .schemas import HealthOut

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Chann CRM AI — Data Tier", version=settings.platform_version)
app.include_router(internal.router)


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Readiness, not liveness.

    Note the cache is reported but does not make the tier unhealthy: by design
    a Redis outage degrades performance, not correctness (ADR-006).
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_state = "up"
    except Exception:
        db_state = "down"

    try:
        cache.client.ping()
        cache_state = "up"
    except Exception:
        cache_state = "down"

    return HealthOut(
        status="ok" if db_state == "up" else "degraded",
        tier="data",
        app_env=settings.app_env,
        platform_version=settings.platform_version,
        git_commit=settings.git_commit,
        database=db_state,
        cache=cache_state,
    )


@app.get("/ready")
def ready():
    state = health()
    # Redis is an optimisation for tenant reads, but it is authoritative for
    # revocable Platform Admin sessions. Full Phase 1 readiness therefore
    # requires both dependencies even though ordinary tenant reads can fall
    # back to PostgreSQL safely.
    is_ready = state.database == "up" and state.cache == "up"
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={**state.model_dump(), "status": "ready" if is_ready else "not_ready"},
    )
