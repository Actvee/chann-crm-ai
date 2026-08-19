"""Internal API consumed only by the Application Tier.

Nothing here is reachable by a browser or by LINE. Every route requires the
shared internal secret.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import (
    CacheFailureMode,
    CacheUnavailable,
    cache,
    k_admin_session,
    k_identity,
    k_member,
    k_permissions,
)
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
from ..repositories.phase2 import (
    AuthorizationRepository,
    LicenseSettingRepository,
    MemberRoleRepository,
    OwnershipTransferRepository,
    Phase2Conflict,
    Phase2NotFound,
    RoleRepository,
)
from ..schemas import (
    AuthorizationContextOut,
    BreakGlassTransferIn,
    IdentityOut,
    IdentityResolveIn,
    LicenseSettingOut,
    LicenseSettingWriteIn,
    MemberRoleIn,
    MemberOut,
    MembershipOut,
    OwnershipTransferAcceptIn,
    OwnershipTransferOut,
    OwnershipTransferRequestIn,
    PlatformAdminAuthIn,
    PlatformAdminAuthOut,
    PlatformAdminSessionIn,
    PlatformAdminSessionOut,
    RoleOut,
    RoleWriteIn,
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


def _role_out(session: Session, scope: TenantScope, role) -> RoleOut:
    return RoleOut(
        id=role.id,
        license_id=role.license_id,
        role_name=role.role_name,
        is_owner=role.is_owner,
        permission_keys=sorted(RoleRepository(session).permission_keys(scope, role.role_name)),
    )


def _phase2_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, Phase2NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (Phase2Conflict, IntegrityError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="phase2 operation failed")


def _invalidate_authorization_for_license(session: Session, scope: TenantScope) -> None:
    # Invalidates BOTH permission and member-role cache for every member of
    # the tenant. Role rename, ownership transfer, and break-glass all
    # mutate LicenseMember.role directly (not through set_member_role, the
    # only call site that used to invalidate k_member on its own), so a
    # single call site here needs to cover both caches or GET /members/{uid}
    # can serve a stale role for up to cache_ttl_member_s after any of them.
    members = MemberRepository(session).list_for_license(scope)
    keys = [k_permissions(str(scope.license_id), m.chann_uid) for m in members]
    keys += [k_member(str(scope.license_id), m.chann_uid) for m in members]
    cache.invalidate(*keys)


@router.get(
    "/licenses/{license_id}/authorization/{chann_uid}",
    response_model=AuthorizationContextOut,
)
def get_authorization_context(
    license_id: uuid.UUID, chann_uid: str, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)

    def load():
        return AuthorizationRepository(session).context(scope, chann_uid)

    value = cache.get_or_load(
        k_permissions(str(license_id), chann_uid),
        settings.cache_ttl_permissions_s,
        load,
        CacheFailureMode.FALLBACK_DB,
    )
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="active member not found")
    return AuthorizationContextOut(**value)


@router.get("/licenses/{license_id}/roles", response_model=list[RoleOut])
def list_roles(license_id: uuid.UUID, session: Session = Depends(get_session)):
    scope = TenantScope(license_id=license_id)
    return [_role_out(session, scope, role) for role in RoleRepository(session).list(scope)]


@router.post("/licenses/{license_id}/roles", response_model=RoleOut, status_code=201)
def create_role(
    license_id: uuid.UUID, payload: RoleWriteIn, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        role = RoleRepository(session).create(scope, payload.role_name, set(payload.permission_keys))
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return _role_out(session, scope, role)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.patch("/licenses/{license_id}/roles/{role_name}", response_model=RoleOut)
def update_role(
    license_id: uuid.UUID,
    role_name: str,
    payload: RoleWriteIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        role = RoleRepository(session).update(
            scope, role_name, payload.role_name, set(payload.permission_keys)
        )
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return _role_out(session, scope, role)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete("/licenses/{license_id}/roles/{role_name}", status_code=204)
def delete_role(
    license_id: uuid.UUID, role_name: str, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        RoleRepository(session).delete(scope, role_name)
        session.commit()
        _invalidate_authorization_for_license(session, scope)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.patch("/licenses/{license_id}/members/{chann_uid}/role", response_model=MemberOut)
def set_member_role(
    license_id: uuid.UUID,
    chann_uid: str,
    payload: MemberRoleIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        member = MemberRoleRepository(session).set_role(scope, chann_uid, payload.role_name)
        session.commit()
        cache.invalidate(
            k_member(str(license_id), chann_uid),
            k_permissions(str(license_id), chann_uid),
        )
        return MemberOut(chann_uid=member.chann_uid, role=member.role, status=member.status)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.get("/licenses/{license_id}/settings", response_model=list[LicenseSettingOut])
def list_license_settings(license_id: uuid.UUID, session: Session = Depends(get_session)):
    scope = TenantScope(license_id=license_id)
    return [
        LicenseSettingOut(setting_key=row.setting_key, setting_value=row.setting_value)
        for row in LicenseSettingRepository(session).list(scope)
    ]


@router.put("/licenses/{license_id}/settings/{setting_key}", response_model=LicenseSettingOut)
def put_license_setting(
    license_id: uuid.UUID,
    setting_key: str,
    payload: LicenseSettingWriteIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = LicenseSettingRepository(session).upsert(scope, setting_key, payload.setting_value)
        session.commit()
        return LicenseSettingOut(setting_key=row.setting_key, setting_value=row.setting_value)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete("/licenses/{license_id}/settings/{setting_key}", status_code=204)
def delete_license_setting(
    license_id: uuid.UUID, setting_key: str, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        LicenseSettingRepository(session).delete(scope, setting_key)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.post(
    "/licenses/{license_id}/ownership-transfers",
    response_model=OwnershipTransferOut,
    status_code=201,
)
def request_ownership_transfer(
    license_id: uuid.UUID,
    payload: OwnershipTransferRequestIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        transfer = OwnershipTransferRepository(session).request(
            scope, payload.from_chann_uid, payload.to_chann_uid
        )
        session.commit()
        return OwnershipTransferOut.model_validate(transfer, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.post(
    "/licenses/{license_id}/ownership-transfers/{transfer_id}/accept",
    response_model=OwnershipTransferOut,
)
def accept_ownership_transfer(
    license_id: uuid.UUID,
    transfer_id: uuid.UUID,
    payload: OwnershipTransferAcceptIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        transfer = OwnershipTransferRepository(session).accept(
            scope, transfer_id, payload.accepting_chann_uid
        )
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return OwnershipTransferOut.model_validate(transfer, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.post("/platform/licenses/{license_id}/break-glass/transfer-owner", response_model=MemberOut)
def force_transfer_owner(
    license_id: uuid.UUID,
    payload: BreakGlassTransferIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        member = OwnershipTransferRepository(session).force(scope, payload.target_chann_uid)
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return MemberOut(chann_uid=member.chann_uid, role=member.role, status=member.status)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)
