"""Application Tier — business logic, LINE webhooks, AI orchestration.

Hard boundary: this tier must not import SQLAlchemy, psycopg, or redis.
All persistent state goes through DataClient over internal HTTP.
"""
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import settings
from .data_client import DataClient
from .line import webhook
from . import routers_admin, routers_phase2

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Chann CRM AI — Application Tier", version=settings.platform_version)
app.include_router(webhook.router)
app.include_router(routers_admin.router)
app.include_router(routers_phase2.router)


@app.get("/health")
def health():
    missing = required_configuration_missing()
    return {
        "status": "ok",
        "tier": "application",
        "app_env": settings.app_env,
        "platform_version": settings.platform_version,
        "git_commit": settings.git_commit,
        "required_not_configured": missing,
    }


def required_configuration_missing() -> list[str]:
    return [
        name
        for name in (
            "line_customer_channel_secret",
            "line_sales_channel_secret",
            "line_technician_channel_secret",
            "line_customer_channel_access_token",
            "line_sales_channel_access_token",
            "line_technician_channel_access_token",
            "line_login_channel_id",
            "admin_secret",
            "jwt_secret",
        )
        if not getattr(settings, name)
    ]


@app.get("/ready")
async def ready():
    missing = required_configuration_missing()
    data_state = "down"
    client = DataClient()
    try:
        data_health = await client.health()
        data_state = data_health.get("status", "unknown")
    except Exception:
        data_state = "down"
    finally:
        await client.aclose()
    ready_now = not missing and data_state == "ok"
    return JSONResponse(
        status_code=200 if ready_now else 503,
        content={
            "status": "ready" if ready_now else "not_ready",
            "tier": "application",
            "data": data_state,
            "required_not_configured": missing,
        },
    )
