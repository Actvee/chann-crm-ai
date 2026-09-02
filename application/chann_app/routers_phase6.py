"""Phase 6 Application-tier routes — notifications and follow-ups.

Every route resolves a TenantPrincipal first, so a member can only ever see
their own notifications inside a tenant they actually belong to. The target is
taken from the principal rather than the URL: accepting a chann_uid from the
caller would let anyone read or clear anyone else's notifications by editing
the path.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from .data_client import DataClient
from .services.authorization import TenantPrincipal, resolve_tenant_principal

router = APIRouter(prefix="/api/v1", tags=["phase6"])


async def get_data_client():
    client = DataClient()
    try:
        yield client
    finally:
        await client.aclose()


async def get_tenant_principal(
    x_liff_id_token: str = Header(default=""),
    x_liff_audience: str = Header(default="sales"),
    x_license_id: str = Header(default=""),
    client: DataClient = Depends(get_data_client),
) -> TenantPrincipal:
    return await resolve_tenant_principal(
        client,
        x_liff_id_token=x_liff_id_token,
        x_liff_audience=x_liff_audience,
        x_license_id=x_license_id,
    )


class FollowUpCreateIn(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    due_date: str
    # Chat has stored a time on every appointment since the 09:00 default
    # landed; this endpoint could not, so an appointment made from the
    # dashboard was the only kind with no time on it.
    due_time: str | None = None
    notes: str | None = None


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    return await client.list_notifications(
        principal.license_id, principal.chann_uid,
        unread_only=unread_only, limit=limit,
    )


@router.get("/notifications/unread_count")
async def unread_count(
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Polled by the dashboard badge (6.8)."""
    return {
        "unread_count": await client.notification_unread_count(
            principal.license_id, principal.chann_uid
        )
    }


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    # chann_uid comes from the verified principal, never from the request.
    return await client.mark_notification_read(
        principal.license_id, principal.chann_uid, str(notification_id)
    )


@router.get("/follow-ups")
async def list_follow_ups(
    days: int = 1,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    principal.require("followup.read")
    return await client.due_follow_ups(principal.license_id, days=days)


@router.post("/follow-ups", status_code=201)
async def create_follow_up(
    payload: FollowUpCreateIn,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    principal.require("followup.create")
    return await client.create_follow_up(
        principal.license_id,
        {
            "entity_type": payload.entity_type,
            "entity_id": str(payload.entity_id),
            "due_date": payload.due_date,
            "due_time": payload.due_time,
            "notes": payload.notes,
        },
        actor_id=principal.chann_uid,
    )


@router.patch("/follow-ups/{follow_up_id}/status")
async def set_follow_up_status(
    follow_up_id: uuid.UUID,
    status_value: str,   # query param: ?status_value=completed
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    principal.require("followup.update")
    if status_value not in {"pending", "completed", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid status"
        )
    return await client.set_follow_up_status(
        principal.license_id, str(follow_up_id), status_value,
        actor_id=principal.chann_uid,
    )
