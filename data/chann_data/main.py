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
log = logging.getLogger(__name__)

# The alembic revision this build's models expect. Bump it in the same commit
# as each new migration.
#
# Exists because a deploy once shipped code that queried a column the database
# did not have yet: the service booted fine, /health said "ok", and the only
# symptom was a generic 500 the moment a user touched that table. A health
# check that cannot see a schema/code mismatch reports health it has not
# actually verified.
EXPECTED_MIGRATION_HEAD = "0020_crm_essentials"


def _schema_state() -> tuple[str, str | None]:
    """(state, actual_head). state is up-to-date | stale | unknown."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:
        return "unknown", None
    if row is None:
        return "unknown", None
    actual = row[0]
    return ("up-to-date" if actual == EXPECTED_MIGRATION_HEAD else "stale"), actual

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

    schema_state, actual_head = _schema_state()
    if schema_state == "stale":
        # Loud, because this is the state where every write path is one query
        # away from a 500 and nothing else would say so.
        log.error(
            "SCHEMA MISMATCH: database is at %s but this build expects %s — "
            "run the migration",
            actual_head, EXPECTED_MIGRATION_HEAD,
        )

    if db_state != "up":
        status_value = "degraded"
    elif schema_state == "stale":
        status_value = "degraded"
    else:
        status_value = "ok"

    return HealthOut(
        status=status_value,
        tier="data",
        app_env=settings.app_env,
        platform_version=settings.platform_version,
        git_commit=settings.git_commit,
        database=db_state,
        cache=cache_state,
        schema_state=schema_state,
        migration_head=actual_head,
        expected_migration_head=EXPECTED_MIGRATION_HEAD,
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
