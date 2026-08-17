"""Internal API consumed only by the Application Tier.

Nothing here is reachable by a browser or by LINE. Every route requires the
shared internal secret.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..cache import CacheFailureMode, CacheUnavailable, cache, k_admin_session, k_identity, k_member
from ..config import settings
from ..db import get_session
from ..repositories.tenant_scope import (
    CrossTenantAccessDenied,
    IdentityRepository,
    LicenseRepository,
    MemberRepository,
    PlatformAdminRepository,
    TenantScope,
)
from ..schemas import (
    IdentityOut,
    IdentityResolveIn,
    MemberOut,
    MembershipOut,
    PlatformAdminAuthIn,
    PlatformAdminAuthOut,
    PlatformAdminSessionIn,
    PlatformAdminSessionOut,
)
from ..security import require_internal_secret

router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_internal_secret)])


@router.post("/platform-admins/authenticate", response_model=PlatformAdminAuthOut)
def authenticate_platform_admin(
    payload: PlatformAdminAuthIn, session: Session = Depends(get_session)
):
    admin = PlatformAdminRepository(session).authenticate(payload.username, payload.password)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return PlatformAdminAuthOut(admin_id=admin.id, username=admin.username)


@router.post("/platform-admin-sessions", response_model=PlatformAdminSessionOut)
def create_platform_admin_session(payload: PlatformAdminSessionIn):
    ttl_s = max(1, min(payload.ttl_s, settings.cache_ttl_admin_session_s))
    try:
        cache.set_required(
            k_admin_session(payload.session_id),
            {"session_id": payload.session_id, "admin_id": str(payload.admin_id)},
            ttl_s,
        )
    except CacheUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin session cache unavailable",
        )
    return PlatformAdminSessionOut(session_id=payload.session_id, admin_id=payload.admin_id)


@router.get("/platform-admin-sessions/{session_id}", response_model=PlatformAdminSessionOut)
def get_platform_admin_session(session_id: str):
    try:
        stored = cache.get_or_load(
            k_admin_session(session_id),
            settings.cache_ttl_admin_session_s,
            lambda: None,
            CacheFailureMode.FAIL_CLOSED,
        )
    except CacheUnavailable:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin session invalid")
    return PlatformAdminSessionOut(**stored)


@router.delete("/platform-admin-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_platform_admin_session(session_id: str):
    try:
        cache.invalidate_required(k_admin_session(session_id))
    except CacheUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin session cache unavailable",
        )


@router.post("/identities/resolve", response_model=IdentityOut)
def resolve_identity(payload: IdentityResolveIn, session: Session = Depends(get_session)):
    """Find or create the Chann Identity for a LINE user.

    Called on every inbound webhook, so it is cache-aside with a DB fallback:
    a Redis outage slows this path down but never changes its answer.
    """
    repo = IdentityRepository(session)

    def load():
        identity = repo.get_by_line_user_id(payload.line_user_id)
        if identity is None:
            return None
        return {
            "chann_uid": identity.chann_uid,
            "line_user_id": identity.line_user_id,
            "primary_role": identity.primary_role,
            "display_name": identity.display_name,
        }

    cached = cache.get_or_load(
        k_identity(payload.line_user_id),
        settings.cache_ttl_identity_s,
        load,
        CacheFailureMode.FALLBACK_DB,
    )
    if cached is not None:
        return IdentityOut(**cached)

    identity = repo.create(
        chann_uid=repo.next_chann_uid(payload.primary_role),
        line_user_id=payload.line_user_id,
        primary_role=payload.primary_role,
        display_name=payload.display_name,
    )
    session.commit()
    out = IdentityOut(
        chann_uid=identity.chann_uid,
        line_user_id=identity.line_user_id,
        primary_role=identity.primary_role,
        display_name=identity.display_name,
    )
    cache.set(k_identity(payload.line_user_id), out.model_dump(), settings.cache_ttl_identity_s)
    return out


@router.get("/identities/{chann_uid}/memberships", response_model=list[MembershipOut])
def list_memberships(chann_uid: str, session: Session = Depends(get_session)):
    """Which tenants this identity belongs to.

    Application-Tier-only: used to pick a tenant for an inbound message. It is
    never proxied to a tenant user, because that would reveal which other
    companies a person works with (Master Spec 1.7 privacy rule).
    """
    members = MemberRepository(session).memberships_of(chann_uid)
    return [
        MembershipOut(
            license_id=m.license_id,
            license_code=m.license.license_code,
            company_name=m.license.company_name,
            chann_uid=m.chann_uid,
            role=m.role,
            status=m.status,
        )
        for m in members
    ]


@router.get("/licenses/{license_id}/members", response_model=list[MemberOut])
def list_members(license_id: uuid.UUID, session: Session = Depends(get_session)):
    """Tenant-scoped. The scope is derived from the path and enforced by the
    repository, so a caller cannot widen it by crafting a query."""
    scope = TenantScope(license_id=license_id)
    if LicenseRepository(session).get_scoped(scope) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="license not found")
    members = MemberRepository(session).list_for_license(scope)
    return [MemberOut(chann_uid=m.chann_uid, role=m.role, status=m.status) for m in members]


@router.get("/licenses/{license_id}/members/{chann_uid}", response_model=MemberOut)
def get_member(license_id: uuid.UUID, chann_uid: str, session: Session = Depends(get_session)):
    scope = TenantScope(license_id=license_id)

    def load():
        member = MemberRepository(session).get(scope, chann_uid)
        if member is None:
            return None
        return {"chann_uid": member.chann_uid, "role": member.role, "status": member.status}

    cached = cache.get_or_load(
        k_member(str(license_id), chann_uid),
        settings.cache_ttl_member_s,
        load,
        CacheFailureMode.FALLBACK_DB,
    )
    if cached is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
    return MemberOut(**cached)


@router.get("/licenses/{license_id}/members/{chann_uid}/cross-check")
def cross_tenant_probe(license_id: uuid.UUID, target_license_id: uuid.UUID,
                       chann_uid: str, session: Session = Depends(get_session)):
    """Exists so the isolation test has a route that *attempts* a cross-tenant
    read through the normal machinery and is refused by it."""
    scope = TenantScope(license_id=license_id)
    try:
        scope.assert_owns(target_license_id)
    except CrossTenantAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    member = MemberRepository(session).get(scope, chann_uid)
    return {"found": member is not None}
