"""Internal API consumed only by the Application Tier.

Nothing here is reachable by a browser or by LINE. Every route requires the
shared internal secret.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import (
    CacheFailureMode,
    CacheUnavailable,
    cache,
    k_admin_session,
    k_identity,
    k_last_customer_ref,
    k_member,
    k_pending_intent,
    k_permissions,
)
from ..config import settings
from ..db import get_session
from ..models import License
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
from ..repositories.audit import AuditRepository, diff_fields
from ..permissions import PERMISSION_DESCRIPTIONS, PERMISSION_KEYS
from ..repositories.phase7 import (
    MasterDataConflict,
    MasterDataNotFound,
    ProductRepository,
    SalesGroupRepository,
    TechnicianTeamRepository,
)
from ..repositories.phase9 import (
    CustomerRepository,
    DealRepository,
    Phase9Conflict,
    Phase9NotFound,
    StorefrontRepository,
)
from ..repositories.phase10 import (
    DocumentTemplateRepository,
    GeneratedDocumentRepository,
    Phase10Conflict,
    Phase10NotFound,
    QuoteRepository,
)
from ..repositories.profile import (
    ProfileConflict,
    ProfileNotFound,
    ProfileRepository,
)
from ..repositories.phase65 import (
    RegistrationConflict,
    RegistrationNotFound,
    RegistrationRepository,
)
from ..repositories.phase6 import (
    FollowUpRepository,
    MessageEntityMapRepository,
    NotificationRepository,
    Phase6Conflict,
    Phase6NotFound,
)
from ..schemas import (
    AuditLogOut,
    CustomerIn,
    CustomerOut,
    DealIn,
    DealOut,
    DealProductIn,
    DealProductOut,
    DealStageIn,
    DocumentTemplateIn,
    DocumentTemplateOut,
    DocumentTemplateVersionIn,
    DocumentTemplateVersionOut,
    GeneratedDocumentIn,
    GeneratedDocumentOut,
    QuoteIn,
    QuoteOut,
    QuoteStatusIn,
    StorefrontInterestIn,
    StorefrontProductOut,
    AuditLogWriteIn,
    FollowUpIn,
    FollowUpOut,
    FollowUpStatusIn,
    PendingIntentIn,
    PendingIntentOut,
    LastCustomerRefIn,
    LastCustomerRefOut,
    ProfileEditCheckOut,
    ProfileOut,
    ProfileUpdateIn,
    GroupIn,
    GroupMemberIn,
    GroupOut,
    ProductCsvIn,
    ProductCsvOut,
    ProductIn,
    ProductOut,
    TeamIn,
    TeamMemberIn,
    TeamMemberOut,
    TeamOut,
    CustomerLinkIn,
    CustomerLinkOut,
    InviteCreateIn,
    InviteOut,
    InviteRedeemIn,
    LicenseCreateIn,
    LicenseOut,
    LicenseStatusIn,
    MessageEntityMapIn,
    ShopOut,
    MessageEntityMapOut,
    NotificationIn,
    NotificationOut,
    UnreadCountOut,
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

log = logging.getLogger(__name__)

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
def list_memberships(
    chann_uid: str, oa: str | None = None, session: Session = Depends(get_session),
):
    """Which tenants this identity belongs to, for THIS OA's persona.

    Application-Tier-only: used to pick a tenant for an inbound message. It is
    never proxied to a tenant user, because that would reveal which other
    companies a person works with (Master Spec 1.7 privacy rule).

    `oa` narrows what "belongs to" means, because the three OAs are three
    different personas that happen to share one LINE userId (see
    MemberRepository.memberships_of for the full explanation):

    - oa="customer": staff membership at a company (Sales/CS/Owner/Admin)
      must NOT count as being that company's customer — a real end customer
      links via customer_license_links (Phase 6.5's company code), which
      grants no tenant permissions at all. Resolved from that table instead
      of license_members entirely.
    - oa="technician": only a license_members row whose role is literally
      "technician" counts — any other staff role at the same company must
      not grant Technician OA access just because a membership exists.
    - "sales" or omitted: the pre-existing behaviour, everyone except
      "technician".
    """
    if oa == "customer":
        shops = RegistrationRepository(session).my_shops(chann_uid)
        return [
            MembershipOut(
                license_id=shop.id,
                license_code=shop.license_code,
                company_name=shop.company_name,
                chann_uid=chann_uid,
                role="customer",
                status="active",
            )
            for shop in shops
        ]

    members = MemberRepository(session).memberships_of(chann_uid, oa=oa)
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
    read through the normal machinery and is refused by it. Every attempt —
    denied or not — gets an audit row per Master Spec 3.4/3.5: a refused
    attempt is exactly the kind of event this table exists to catch."""
    scope = TenantScope(license_id=license_id)
    try:
        scope.assert_owns(target_license_id)
    except CrossTenantAccessDenied as exc:
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license",
            entity_id=target_license_id,
            actor_type="system",
            action="cross_tenant_lookup",
            cross_tenant=True,
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    member = MemberRepository(session).get(scope, chann_uid)
    AuditRepository(session).write(
        license_id=license_id,
        entity_type="license",
        entity_id=target_license_id,
        actor_type="system",
        action="cross_tenant_lookup",
        cross_tenant=True,
    )
    session.commit()
    return {"found": member is not None}


@router.post("/audit-log", response_model=AuditLogOut, status_code=201)
def write_audit_log(
    payload: AuditLogWriteIn,
    session: Session = Depends(get_session),
):
    """Generic write path for callers (chiefly the Application Tier, which
    knows the acting principal) that already know exactly what happened and
    just need it recorded. Data-tier endpoints that mutate their own state
    directly (roles, members, settings, transfers) emit their own audit row
    inline instead of calling back through this endpoint."""
    try:
        row = AuditRepository(session).write(
            license_id=payload.license_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            action=payload.action,
            field_changes=payload.field_changes,
            ai_reasoning=payload.ai_reasoning,
            cross_tenant=payload.cross_tenant,
        )
        session.commit()
        return AuditLogOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.get("/licenses/{license_id}/audit-log", response_model=list[AuditLogOut])
def list_audit_log(
    license_id: uuid.UUID,
    entity_type: str | None = None,
    actor_type: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    rows = AuditRepository(session).list_for_license(
        license_id, entity_type=entity_type, actor_type=actor_type, limit=limit
    )
    return [AuditLogOut.model_validate(r, from_attributes=True) for r in rows]


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
    log.exception("unhandled phase2 error: %s", exc)
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
    license_id: uuid.UUID,
    payload: RoleWriteIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        role = RoleRepository(session).create(scope, payload.role_name, set(payload.permission_keys))
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="role",
            entity_id=role.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="create",
            field_changes=diff_fields(
                {}, {"role_name": role.role_name, "permission_keys": sorted(payload.permission_keys)}
            ),
        )
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
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        role_repo = RoleRepository(session)
        existing = role_repo.get(scope, role_name)
        before = (
            {"role_name": existing.role_name, "permission_keys": sorted(role_repo.permission_keys(scope, role_name))}
            if existing is not None
            else {}
        )
        role = role_repo.update(
            scope, role_name, payload.role_name, set(payload.permission_keys)
        )
        after = {"role_name": role.role_name, "permission_keys": sorted(payload.permission_keys)}
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="role",
            entity_id=role.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields(before, after),
        )
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return _role_out(session, scope, role)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete("/licenses/{license_id}/roles/{role_name}", status_code=204)
def delete_role(
    license_id: uuid.UUID,
    role_name: str,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        role_repo = RoleRepository(session)
        existing = role_repo.get(scope, role_name)
        role_repo.delete(scope, role_name)
        if existing is not None:
            AuditRepository(session).write(
                license_id=license_id,
                entity_type="role",
                entity_id=existing.id,
                actor_type="user",
                actor_id=x_actor_id or None,
                action="delete",
                field_changes=diff_fields({"role_name": existing.role_name}, {}),
            )
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
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        before_member = MemberRepository(session).get(scope, chann_uid)
        before = {"role": before_member.role} if before_member is not None else {}
        member = MemberRoleRepository(session).set_role(scope, chann_uid, payload.role_name)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license_member",
            entity_id=member.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields(before, {"role": member.role}),
        )
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
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        setting_repo = LicenseSettingRepository(session)
        existing = next(
            (r for r in setting_repo.list(scope) if r.setting_key == setting_key), None
        )
        before = {"setting_value": existing.setting_value} if existing is not None else {}
        row = setting_repo.upsert(scope, setting_key, payload.setting_value)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license_setting",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields(before, {"setting_value": row.setting_value}),
        )
        session.commit()
        return LicenseSettingOut(setting_key=row.setting_key, setting_value=row.setting_value)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete("/licenses/{license_id}/settings/{setting_key}", status_code=204)
def delete_license_setting(
    license_id: uuid.UUID,
    setting_key: str,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        setting_repo = LicenseSettingRepository(session)
        existing = next(
            (r for r in setting_repo.list(scope) if r.setting_key == setting_key), None
        )
        setting_repo.delete(scope, setting_key)
        if existing is not None:
            AuditRepository(session).write(
                license_id=license_id,
                entity_type="license_setting",
                entity_id=existing.id,
                actor_type="user",
                actor_id=x_actor_id or None,
                action="delete",
                field_changes=diff_fields({"setting_value": existing.setting_value}, {}),
            )
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
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        transfer = OwnershipTransferRepository(session).accept(
            scope, transfer_id, payload.accepting_chann_uid
        )
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="ownership_transfer",
            entity_id=transfer.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="transfer",
            field_changes=diff_fields({"status": "pending"}, {"status": transfer.status}),
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
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        before_target = MemberRepository(session).get(scope, payload.target_chann_uid)
        before = {"role": before_target.role} if before_target is not None else {}
        member = OwnershipTransferRepository(session).force(scope, payload.target_chann_uid)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license_member",
            entity_id=member.id,
            actor_type="platform_admin",
            actor_id=x_actor_id or None,
            action="transfer",
            field_changes=diff_fields(before, {"role": member.role}),
            # Break-glass bypasses the normal two-party flow entirely — the
            # exact scenario Master Spec 3.4's example row calls out.
            cross_tenant=True,
        )
        session.commit()
        _invalidate_authorization_for_license(session, scope)
        return MemberOut(chann_uid=member.chann_uid, role=member.role, status=member.status)
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


# ---------------------------------------------------------------- Phase 6


def _phase6_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, Phase6NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, Phase6Conflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, CrossTenantAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    # Log the real exception before flattening it to "internal error".
    # Without this the cause is lost entirely: the client sees a generic 500
    # and the server keeps no trace, because a *handled* HTTPException is not
    # something FastAPI logs. That cost a full debugging round when a missing
    # migration surfaced only as "internal error" with nothing behind it.
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


@router.post(
    "/licenses/{license_id}/message-entity-map",
    response_model=MessageEntityMapOut,
    status_code=201,
)
def record_message_entity(
    license_id: uuid.UUID,
    payload: MessageEntityMapIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = MessageEntityMapRepository(session).record(
            scope,
            message_id=payload.message_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
        )
        session.commit()
        return MessageEntityMapOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase6_http_error(exc)


@router.get(
    "/licenses/{license_id}/message-entity-map/{message_id}",
    response_model=MessageEntityMapOut,
)
def get_message_entity(
    license_id: uuid.UUID, message_id: str, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    row = MessageEntityMapRepository(session).get(scope, message_id)
    if row is None:
        # Same 404 whether the mapping is absent or belongs to another tenant —
        # see the repository note; the caller's reply is identical either way.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="message mapping not found"
        )
    return MessageEntityMapOut.model_validate(row, from_attributes=True)


@router.post(
    "/licenses/{license_id}/notifications",
    response_model=NotificationOut,
    status_code=201,
)
def create_notification(
    license_id: uuid.UUID,
    payload: NotificationIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = NotificationRepository(session).create(
            scope,
            target_chann_uid=payload.target_chann_uid,
            type=payload.type,
            message=payload.message,
            message_en=payload.message_en,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            delivery_line=payload.delivery_line,
            delivery_dashboard=payload.delivery_dashboard,
        )
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="notification",
            entity_id=row.id,
            actor_type="system",
            actor_id=x_actor_id or None,
            action="create",
        )
        session.commit()
        return NotificationOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase6_http_error(exc)


@router.get(
    "/licenses/{license_id}/members/{chann_uid}/notifications",
    response_model=list[NotificationOut],
)
def list_notifications(
    license_id: uuid.UUID,
    chann_uid: str,
    unread_only: bool = False,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = NotificationRepository(session).list_for_member(
        scope, chann_uid, unread_only=unread_only, limit=limit
    )
    return [NotificationOut.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "/licenses/{license_id}/members/{chann_uid}/notifications/unread-count",
    response_model=UnreadCountOut,
)
def notification_unread_count(
    license_id: uuid.UUID, chann_uid: str, session: Session = Depends(get_session)
):
    """Polled by the dashboard badge (6.8), so it counts rather than lists."""
    scope = TenantScope(license_id=license_id)
    return UnreadCountOut(
        unread_count=NotificationRepository(session).unread_count(scope, chann_uid)
    )


@router.post(
    "/licenses/{license_id}/members/{chann_uid}/notifications/{notification_id}/read",
    response_model=NotificationOut,
)
def mark_notification_read(
    license_id: uuid.UUID,
    chann_uid: str,
    notification_id: uuid.UUID,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = NotificationRepository(session).mark_read(
            scope, notification_id, chann_uid
        )
        session.commit()
        session.refresh(row)
        return NotificationOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase6_http_error(exc)


@router.post(
    "/licenses/{license_id}/follow-ups", response_model=FollowUpOut, status_code=201
)
def create_follow_up(
    license_id: uuid.UUID,
    payload: FollowUpIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = FollowUpRepository(session).create(
            scope,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            due_date=payload.due_date,
            owner_member_id=payload.owner_member_id,
            notes=payload.notes,
        )
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="follow_up",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="create",
            field_changes=diff_fields(
                {}, {"entity_type": row.entity_type, "due_date": str(row.due_date)}
            ),
        )
        session.commit()
        return FollowUpOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase6_http_error(exc)


@router.get("/licenses/{license_id}/follow-ups", response_model=list[FollowUpOut])
def list_follow_ups(
    license_id: uuid.UUID,
    status_filter: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = FollowUpRepository(session).list_for_license(
        scope, status=status_filter, entity_type=entity_type, limit=limit
    )
    return [FollowUpOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/licenses/{license_id}/follow-ups/due", response_model=list[FollowUpOut])
def list_due_follow_ups(
    license_id: uuid.UUID, days: int = 1, session: Session = Depends(get_session)
):
    """Drives the 1-day-ahead reminder sweep (6.7). Includes overdue rows."""
    scope = TenantScope(license_id=license_id)
    rows = FollowUpRepository(session).due_within(scope, days=days)
    return [FollowUpOut.model_validate(r, from_attributes=True) for r in rows]


@router.patch(
    "/licenses/{license_id}/follow-ups/{follow_up_id}/status",
    response_model=FollowUpOut,
)
def set_follow_up_status(
    license_id: uuid.UUID,
    follow_up_id: uuid.UUID,
    payload: FollowUpStatusIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = FollowUpRepository(session)
        before = repo.get(scope, follow_up_id)
        before_status = before.status if before is not None else None
        row = repo.set_status(scope, follow_up_id, payload.status)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="follow_up",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields(
                {"status": before_status}, {"status": row.status}
            ),
        )
        session.commit()
        return FollowUpOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase6_http_error(exc)


@router.get("/permissions/catalog")
def permission_catalog():
    """The full permission catalogue with human labels.

    Platform-wide and tenant-independent, so it is not under /licenses/{id}.
    Serves both the bot's "what can I do" answer (6.6) and any UI that wants
    to offer permissions as a pick-list rather than free text.
    """
    return [
        {
            "key": key,
            "group": key.split(".", 1)[0] if "." in key else "general",
            "label": PERMISSION_DESCRIPTIONS.get(key, {}),
        }
        for key in sorted(PERMISSION_KEYS)
    ]


# ---------------------------------------------------------------- Phase 6.5


def _phase65_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RegistrationNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, RegistrationConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    # Log the real exception before flattening it to "internal error".
    # Without this the cause is lost entirely: the client sees a generic 500
    # and the server keeps no trace, because a *handled* HTTPException is not
    # something FastAPI logs. That cost a full debugging round when a missing
    # migration surfaced only as "internal error" with nothing behind it.
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


@router.post("/licenses", response_model=LicenseOut, status_code=201)
def create_license(
    payload: LicenseCreateIn, session: Session = Depends(get_session)
):
    """Self-service tenant creation — one per LINE identity."""
    try:
        row = RegistrationRepository(session).create_license(
            company_name=payload.company_name,
            created_by_chann_uid=payload.created_by_chann_uid,
            display_name=payload.display_name,
        )
        AuditRepository(session).write(
            license_id=row.id,
            entity_type="license",
            entity_id=row.id,
            actor_type="user",
            actor_id=payload.created_by_chann_uid,
            action="create",
            field_changes=diff_fields(
                {}, {"company_name": row.company_name, "status": row.status}
            ),
        )
        session.commit()
        return LicenseOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.get("/public/shops", response_model=list[ShopOut])
def public_shop_search(q: str, limit: int = 10, session: Session = Depends(get_session)):
    """The one deliberately un-tenant-scoped read (Master Spec 6.5.7).

    Projected through ShopOut so only company_name + company_code leave the
    tier — the repository returns full License rows and must never be
    serialised directly here.
    """
    rows = RegistrationRepository(session).find_shops(q, limit=limit)
    return [
        ShopOut(company_name=r.company_name, company_code=r.company_code or "")
        for r in rows
        if r.company_code
    ]


@router.post("/licenses/{license_id}/invites", response_model=InviteOut, status_code=201)
def create_invite(
    license_id: uuid.UUID,
    payload: InviteCreateIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    try:
        row = RegistrationRepository(session).create_invite(
            license_id,
            role=payload.role,
            max_uses=payload.max_uses,
            expires_in_days=payload.expires_in_days,
            created_by_member_id=payload.created_by_member_id,
        )
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license_invite",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="create",
            field_changes=diff_fields({}, {"role": row.role, "max_uses": row.max_uses}),
        )
        session.commit()
        return InviteOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.get("/licenses/{license_id}/invites", response_model=list[InviteOut])
def list_invites(license_id: uuid.UUID, session: Session = Depends(get_session)):
    rows = RegistrationRepository(session).list_invites(license_id)
    return [InviteOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/licenses/{license_id}/invites/{invite_id}/revoke", response_model=InviteOut)
def revoke_invite(
    license_id: uuid.UUID,
    invite_id: uuid.UUID,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    try:
        row = RegistrationRepository(session).revoke_invite(license_id, invite_id)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license_invite",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields({"revoked": False}, {"revoked": True}),
        )
        session.commit()
        return InviteOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.post("/invites/redeem", response_model=MemberOut, status_code=201)
def redeem_invite(payload: InviteRedeemIn, session: Session = Depends(get_session)):
    """Not under /licenses/{id} on purpose — the redeemer does not yet know
    which tenant the code belongs to; that is what the code tells us."""
    try:
        member = RegistrationRepository(session).redeem_invite(
            invite_code=payload.invite_code,
            chann_uid=payload.chann_uid,
            display_name=payload.display_name,
        )
        AuditRepository(session).write(
            license_id=member.license_id,
            entity_type="license_member",
            entity_id=member.id,
            actor_type="user",
            actor_id=payload.chann_uid,
            action="create",
            field_changes=diff_fields({}, {"role": member.role}),
        )
        session.commit()
        return MemberOut(
            chann_uid=member.chann_uid, role=member.role, status=member.status
        )
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.post("/customer-links", response_model=CustomerLinkOut, status_code=201)
def link_customer(payload: CustomerLinkIn, session: Session = Depends(get_session)):
    """Bind an end customer to a shop. Grants NO tenant permissions."""
    try:
        row = RegistrationRepository(session).link_customer(
            chann_uid=payload.chann_uid, company_code=payload.company_code
        )
        session.commit()
        return CustomerLinkOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.get("/customers/{chann_uid}/shops", response_model=list[ShopOut])
def my_shops(chann_uid: str, session: Session = Depends(get_session)):
    rows = RegistrationRepository(session).my_shops(chann_uid)
    return [
        ShopOut(company_name=r.company_name, company_code=r.company_code or "")
        for r in rows
    ]


@router.patch("/licenses/{license_id}/status", response_model=LicenseOut)
def set_license_status(
    license_id: uuid.UUID,
    payload: LicenseStatusIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    try:
        repo = RegistrationRepository(session)
        before = session.get(License, license_id)
        before_status = before.status if before is not None else None
        row = repo.set_status(license_id, payload.status)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="license",
            entity_id=license_id,
            actor_type="platform_admin",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields({"status": before_status}, {"status": row.status}),
        )
        session.commit()
        return LicenseOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


@router.post("/platform/trials/expire", response_model=list[LicenseOut])
def expire_due_trials(session: Session = Depends(get_session)):
    """Sweep for the trial deadline. Suspends; never deletes."""
    try:
        rows = RegistrationRepository(session).expire_due_trials()
        for row in rows:
            AuditRepository(session).write(
                license_id=row.id,
                entity_type="license",
                entity_id=row.id,
                actor_type="system",
                action="update",
                field_changes=diff_fields(
                    {"status": "trial"}, {"status": "suspended"}
                ),
            )
        session.commit()
        return [LicenseOut.model_validate(r, from_attributes=True) for r in rows]
    except Exception as exc:
        session.rollback()
        raise _phase65_http_error(exc)


# ---------------------------------------------------------------- Phase 7


def _phase7_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MasterDataNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MasterDataConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, CrossTenantAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    # Log the real exception before flattening it to "internal error".
    # Without this the cause is lost entirely: the client sees a generic 500
    # and the server keeps no trace, because a *handled* HTTPException is not
    # something FastAPI logs. That cost a full debugging round when a missing
    # migration surfaced only as "internal error" with nothing behind it.
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


@router.put("/licenses/{license_id}/products/{product_id}", response_model=ProductOut)
def upsert_product(
    license_id: uuid.UUID,
    product_id: str,
    payload: ProductIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """PUT because it is idempotent on the business key (7.5)."""
    scope = TenantScope(license_id=license_id)
    try:
        repo = ProductRepository(session)
        before = repo.get(scope, product_id)
        existed = before is not None
        before_name = before.product_name if before else None
        row = repo.upsert(
            scope,
            product_id=product_id,
            product_name=payload.product_name,
            sku=payload.sku,
            category=payload.category,
            unit_price=payload.unit_price,
            description=payload.description,
        )
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="product",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update" if existed else "create",
            field_changes=diff_fields(
                {"product_name": before_name}, {"product_name": row.product_name}
            ),
        )
        session.commit()
        return ProductOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.get("/licenses/{license_id}/products", response_model=list[ProductOut])
def list_products(
    license_id: uuid.UUID,
    category: str | None = None,
    include_archived: bool = False,
    limit: int = 200,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = ProductRepository(session).list(
        scope, category=category, include_archived=include_archived, limit=limit
    )
    return [ProductOut.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/licenses/{license_id}/products/csv", response_model=ProductCsvOut
)
def upload_products_csv(
    license_id: uuid.UUID,
    payload: ProductCsvIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Bulk upsert. Per-row errors are reported, not fatal — one bad row in a
    200-row file should not reject the other 199."""
    scope = TenantScope(license_id=license_id)
    try:
        result = ProductRepository(session).upsert_csv(scope, payload.content)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="product",
            entity_id=license_id,  # a bulk action has no single entity
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            field_changes=diff_fields(
                {}, {"imported": result["imported"], "errors": len(result["errors"])}
            ),
        )
        session.commit()
        return ProductCsvOut(**result)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.delete("/licenses/{license_id}/products/{product_id}", response_model=ProductOut)
def archive_product(
    license_id: uuid.UUID,
    product_id: str,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """DELETE archives — a product referenced by past deals must stay
    resolvable, or historical documents render blanks (7.5)."""
    scope = TenantScope(license_id=license_id)
    try:
        row = ProductRepository(session).archive(scope, product_id)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="product",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="delete",
            field_changes=diff_fields({"archived": False}, {"archived": True}),
        )
        session.commit()
        return ProductOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.post("/licenses/{license_id}/sales-groups", response_model=GroupOut, status_code=201)
def create_sales_group(
    license_id: uuid.UUID, payload: GroupIn, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        row = SalesGroupRepository(session).create(scope, payload.group_name)
        session.commit()
        return GroupOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.get("/licenses/{license_id}/sales-groups", response_model=list[GroupOut])
def list_sales_groups(license_id: uuid.UUID, session: Session = Depends(get_session)):
    rows = SalesGroupRepository(session).list(TenantScope(license_id=license_id))
    return [GroupOut.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/licenses/{license_id}/sales-groups/{group_id}", status_code=204)
def delete_sales_group(
    license_id: uuid.UUID, group_id: uuid.UUID, session: Session = Depends(get_session)
):
    """Removes the group and its membership rows — never the people."""
    scope = TenantScope(license_id=license_id)
    try:
        SalesGroupRepository(session).delete(scope, group_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.post("/licenses/{license_id}/sales-groups/{group_id}/members", status_code=201)
def add_sales_group_member(
    license_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: GroupMemberIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = SalesGroupRepository(session).add_member(scope, group_id, payload.member_id)
        session.commit()
        return {"id": str(row.id), "group_id": str(group_id), "member_id": str(payload.member_id)}
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.delete(
    "/licenses/{license_id}/sales-groups/{group_id}/members/{member_id}", status_code=204
)
def remove_sales_group_member(
    license_id: uuid.UUID,
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        SalesGroupRepository(session).remove_member(scope, group_id, member_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.post(
    "/licenses/{license_id}/technician-teams", response_model=TeamOut, status_code=201
)
def create_technician_team(
    license_id: uuid.UUID, payload: TeamIn, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        row = TechnicianTeamRepository(session).create(scope, payload.team_name)
        session.commit()
        return TeamOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.get("/licenses/{license_id}/technician-teams", response_model=list[TeamOut])
def list_technician_teams(license_id: uuid.UUID, session: Session = Depends(get_session)):
    rows = TechnicianTeamRepository(session).list(TenantScope(license_id=license_id))
    return [TeamOut.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/licenses/{license_id}/technician-teams/{team_id}", status_code=204)
def delete_technician_team(
    license_id: uuid.UUID, team_id: uuid.UUID, session: Session = Depends(get_session)
):
    scope = TenantScope(license_id=license_id)
    try:
        TechnicianTeamRepository(session).delete(scope, team_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.post(
    "/licenses/{license_id}/technician-teams/{team_id}/members",
    response_model=TeamMemberOut,
    status_code=201,
)
def add_technician_team_member(
    license_id: uuid.UUID,
    team_id: uuid.UUID,
    payload: TeamMemberIn,
    session: Session = Depends(get_session),
):
    """Idempotent; re-adding updates is_lead. A team may have several leads."""
    scope = TenantScope(license_id=license_id)
    try:
        row = TechnicianTeamRepository(session).add_member(
            scope, team_id, payload.member_id, is_lead=payload.is_lead
        )
        session.commit()
        return TeamMemberOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


@router.delete(
    "/licenses/{license_id}/technician-teams/{team_id}/members/{member_id}",
    status_code=204,
)
def remove_technician_team_member(
    license_id: uuid.UUID,
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        TechnicianTeamRepository(session).remove_member(scope, team_id, member_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase7_http_error(exc)


# ---------------------------------------------------------------- Phase 8


def _phase8_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProfileNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProfileConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


@router.get("/identities/{chann_uid}/profile", response_model=ProfileOut)
def get_profile(chann_uid: str, session: Session = Depends(get_session)):
    row = ProfileRepository(session).get(chann_uid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity not found")
    return ProfileOut.model_validate(row, from_attributes=True)


@router.patch("/identities/{chann_uid}/profile", response_model=ProfileOut)
def update_profile(
    chann_uid: str,
    payload: ProfileUpdateIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """The one domain function chat and LIFF both call (spec 8.5).

    Authorization (self vs on-behalf) is the Application tier's job — this
    endpoint only validates and writes. It still requires the shared-secret
    header like every internal endpoint, so it is not reachable from outside
    the platform at all, only from an Application tier that already decided
    the edit is allowed.
    """
    try:
        repo = ProfileRepository(session)
        before = repo.get(chann_uid)
        before_fields = (
            {k: getattr(before, k) for k in ("first_name", "last_name", "phone", "email", "address")}
            if before is not None else {}
        )
        fields = payload.model_dump(exclude_unset=True)
        row = repo.update_profile(chann_uid, fields)
        after_fields = {k: getattr(row, k) for k in fields}
        AuditRepository(session).write(
            license_id=None,
            entity_type="identity",
            # chann_identities is keyed by chann_uid (a string), not a UUID,
            # but audit_log.entity_id is UUID NOT NULL. Deriving a stable
            # UUID5 from the chann_uid keeps every audit row for the same
            # identity traceable to the same entity_id, rather than using a
            # meaningless placeholder that would make audit history for
            # profile edits useless.
            entity_id=uuid.uuid5(uuid.NAMESPACE_OID, chann_uid),
            actor_type="user",
            actor_id=x_actor_id or chann_uid,
            action="update",
            field_changes=diff_fields(
                {k: before_fields.get(k) for k in fields}, after_fields
            ),
        )
        session.commit()
        return ProfileOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase8_http_error(exc)


@router.get(
    "/licenses/{license_id}/profile-edit-check/{actor_chann_uid}/{target_chann_uid}",
    response_model=ProfileEditCheckOut,
)
def check_profile_edit(
    license_id: uuid.UUID,
    actor_chann_uid: str,
    target_chann_uid: str,
    session: Session = Depends(get_session),
):
    """Does a real tenant relationship justify actor editing target's profile?

    Self-edit is always allowed and doesn't need this call; this exists for
    the on-behalf case, so the Application tier never has to trust a bare
    name from the AI parser — it checks an actual row (customer_license_links
    or license_members) before letting the edit through.
    """
    allowed = ProfileRepository(session).may_edit_on_behalf(
        actor_chann_uid=actor_chann_uid,
        target_chann_uid=target_chann_uid,
        license_id=license_id,
    )
    return ProfileEditCheckOut(allowed=allowed)


# ---------------------------------------------------------------- Chat state


@router.put("/chat/pending-intent/{oa}/{chann_uid}", status_code=204)
def set_pending_intent(oa: str, chann_uid: str, payload: PendingIntentIn):
    """Store the in-progress slot-filling state that this identity's NEXT
    message should be merged with.

    Spec 6.4's pattern describes parsing one message in isolation and never
    addressed what happens across turns — so a bare "0812345678" answering
    the bot's own question about a phone number was parsed from nothing.
    Keyed by (chann_uid, oa); see cache.k_pending_intent for why the OA has
    to be part of the key rather than trusting chann_uid alone.
    """
    cache.set(
        k_pending_intent(chann_uid, oa),
        {
            "action": payload.action,
            "entity": payload.entity,
            "fields": payload.fields,
            "missing": payload.missing,
        },
        payload.ttl_seconds,
    )


@router.get("/chat/pending-intent/{oa}/{chann_uid}", response_model=PendingIntentOut)
def get_pending_intent(oa: str, chann_uid: str):
    raw = cache.get_or_load(k_pending_intent(chann_uid, oa), ttl_s=0, loader=lambda: None)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no pending intent"
        )
    return PendingIntentOut(**raw)


@router.delete("/chat/pending-intent/{oa}/{chann_uid}", status_code=204)
def clear_pending_intent(oa: str, chann_uid: str):
    cache.invalidate(k_pending_intent(chann_uid, oa))


@router.put("/chat/last-customer/{oa}/{chann_uid}", status_code=204)
def set_last_customer_ref(oa: str, chann_uid: str, payload: LastCustomerRefIn):
    """9.7 follow-up, reported live: "บันทึกสมชายเป็น Contact แล้ว" followed
    immediately by "สร้างดีล" with no name — a completely natural way to
    talk once a customer has already been named once. See
    cache.k_last_customer_ref for why this is a separate key from
    pending_intent rather than reusing it."""
    cache.set(
        k_last_customer_ref(chann_uid, oa),
        {"customer_id": payload.customer_id, "name": payload.name},
        payload.ttl_seconds,
    )


@router.get("/chat/last-customer/{oa}/{chann_uid}", response_model=LastCustomerRefOut)
def get_last_customer_ref(oa: str, chann_uid: str):
    raw = cache.get_or_load(k_last_customer_ref(chann_uid, oa), ttl_s=0, loader=lambda: None)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no recent customer reference"
        )
    return LastCustomerRefOut(**raw)


# ---------------------------------------------------------------- Phase 9 CRM


def _phase9_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, Phase9NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, Phase9Conflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


def _deal_out(deal, products) -> DealOut:
    return DealOut(
        id=deal.id, license_id=deal.license_id, deal_id=deal.deal_id,
        contact_id=deal.contact_id, stage=deal.stage,
        owner_member_id=deal.owner_member_id, notes=deal.notes,
        archived_at=deal.archived_at, created_at=deal.created_at,
        updated_at=deal.updated_at,
        products=[
            DealProductOut(
                id=p.id, deal_id=p.deal_id, product_id=p.product_id,
                product_name=p.product_name, quoted_unit_price=p.quoted_unit_price,
                qty=p.qty, notes=p.notes, created_at=p.created_at,
            )
            for p in products
        ],
    )


@router.post("/licenses/{license_id}/customers", response_model=CustomerOut, status_code=201)
def create_customer(
    license_id: uuid.UUID, payload: CustomerIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = CustomerRepository(session).create(
            scope,
            first_name=payload.first_name, last_name=payload.last_name,
            phone=payload.phone, email=payload.email, address=payload.address,
            notes=payload.notes, customer_chann_uid=payload.customer_chann_uid,
            owner_member_id=payload.owner_member_id,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="customer", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"stage": row.stage}),
        )
        session.commit()
        return CustomerOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.get("/licenses/{license_id}/customers/{customer_id}", response_model=CustomerOut)
def get_customer(
    license_id: uuid.UUID, customer_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    row = CustomerRepository(session).get(scope, customer_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="customer not found")
    return CustomerOut.model_validate(row, from_attributes=True)


@router.get("/licenses/{license_id}/customers", response_model=list[CustomerOut])
def list_customers(
    license_id: uuid.UUID, stage: str | None = None, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = CustomerRepository(session).list_for_license(scope, stage=stage)
    return [CustomerOut.model_validate(r, from_attributes=True) for r in rows]


@router.patch("/licenses/{license_id}/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    license_id: uuid.UUID, customer_id: uuid.UUID, payload: CustomerIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    fields = payload.model_dump(exclude_unset=True, exclude={"customer_chann_uid", "owner_member_id"})
    try:
        before = CustomerRepository(session).get(scope, customer_id)
        before_snapshot = {
            k: getattr(before, k) for k in fields
        } if before else {}
        row = CustomerRepository(session).update(scope, customer_id, fields)
        AuditRepository(session).write(
            license_id=license_id, entity_type="customer", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields(before_snapshot, fields),
        )
        session.commit()
        return CustomerOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post(
    "/licenses/{license_id}/customers/{customer_id}/promote", response_model=CustomerOut,
)
def promote_customer(
    license_id: uuid.UUID, customer_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """9.5 — Lead -> Contact."""
    scope = TenantScope(license_id=license_id)
    try:
        row = CustomerRepository(session).promote_to_contact(scope, customer_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="customer", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"stage": "lead"}, {"stage": "contact"}),
        )
        session.commit()
        return CustomerOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post(
    "/licenses/{license_id}/customers/{customer_id}/archive", response_model=CustomerOut,
)
def archive_customer(
    license_id: uuid.UUID, customer_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = CustomerRepository(session).archive(scope, customer_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="customer", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"archived": False}, {"archived": True}),
        )
        session.commit()
        return CustomerOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post("/licenses/{license_id}/deals", response_model=DealOut, status_code=201)
def create_deal(
    license_id: uuid.UUID, payload: DealIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = DealRepository(session).create(
            scope, contact_id=payload.contact_id, notes=payload.notes,
            owner_member_id=payload.owner_member_id,
            products=[p.model_dump() for p in payload.products],
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"deal_id": row.deal_id, "stage": row.stage}),
        )
        session.commit()
        products = DealRepository(session).products_of(row.id)
        return _deal_out(row, products)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.get("/licenses/{license_id}/deals/{deal_id}", response_model=DealOut)
def get_deal(
    license_id: uuid.UUID, deal_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    repo = DealRepository(session)
    row = repo.get(scope, deal_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deal not found")
    return _deal_out(row, repo.products_of(row.id))


@router.get("/licenses/{license_id}/deals", response_model=list[DealOut])
def list_deals(
    license_id: uuid.UUID, stage: str | None = None, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    repo = DealRepository(session)
    rows = repo.list_for_license(scope, stage=stage)
    return [_deal_out(r, repo.products_of(r.id)) for r in rows]


@router.post(
    "/licenses/{license_id}/deals/{deal_id}/products", response_model=DealProductOut,
    status_code=201,
)
def add_deal_product(
    license_id: uuid.UUID, deal_id: uuid.UUID, payload: DealProductIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = DealRepository(session).add_product(
            scope, deal_id,
            product_id=payload.product_id, product_name=payload.product_name,
            quoted_unit_price=payload.quoted_unit_price, qty=payload.qty,
            notes=payload.notes,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal_product", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"product_name": row.product_name, "qty": row.qty}),
        )
        session.commit()
        return DealProductOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post(
    "/licenses/{license_id}/deals/{deal_id}/stage", response_model=DealOut,
)
def transition_deal_stage(
    license_id: uuid.UUID, deal_id: uuid.UUID, payload: DealStageIn,
    allow_reopen: bool = False,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """`allow_reopen` is a query param the Application tier sets after
    checking the actor holds deal.reopen — this endpoint trusts it rather
    than re-deriving permission_keys itself, the same division of
    responsibility as every other permission-gated action in this project
    (the tenant-permission gate lives in chat.py, not the Data tier)."""
    scope = TenantScope(license_id=license_id)
    try:
        repo = DealRepository(session)
        before_stage = repo.get(scope, deal_id)
        before = before_stage.stage if before_stage else None
        row = repo.transition_stage(
            scope, deal_id, to_stage=payload.stage, allow_reopen=allow_reopen,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"stage": before}, {"stage": row.stage}),
        )
        session.commit()
        return _deal_out(row, repo.products_of(row.id))
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post("/licenses/{license_id}/deals/{deal_id}/archive", response_model=DealOut)
def archive_deal(
    license_id: uuid.UUID, deal_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = DealRepository(session)
        row = repo.archive(scope, deal_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"archived": False}, {"archived": True}),
        )
        session.commit()
        return _deal_out(row, repo.products_of(row.id))
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.get("/public/storefront/products", response_model=list[StorefrontProductOut])
def storefront_search(q: str, limit: int = 10, session: Session = Depends(get_session)):
    """Public, cross-tenant, un-scoped — same reasoning as
    RegistrationRepository.find_shops: a customer browsing the storefront
    has no tenant yet, that is what this search is for."""
    results = StorefrontRepository(session).search_products(q, limit=limit)
    return [StorefrontProductOut(**r) for r in results]


@router.post("/public/storefront/interest", response_model=CustomerOut, status_code=201)
def storefront_record_interest(
    payload: StorefrontInterestIn, session: Session = Depends(get_session),
):
    """9.4's "กดสนใจ" step — creates or updates a Lead in the ONE tenant the
    customer picked. Every call is cross-tenant by nature (the customer
    reached this from a cross-tenant search), so the audit row is marked
    accordingly even though the resulting write itself lands in a single
    tenant."""
    try:
        row = StorefrontRepository(session).record_interest(
            chann_uid=payload.chann_uid, license_id=payload.license_id,
            product_name=payload.product_name,
        )
        AuditRepository(session).write(
            license_id=payload.license_id, entity_type="customer", entity_id=row.id,
            actor_type="user", actor_id=payload.chann_uid, action="create",
            field_changes=diff_fields({}, {"stage": row.stage, "source": "storefront"}),
            cross_tenant=True,
        )
        session.commit()
        return CustomerOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


# ---------------------------------------------------------------- Phase 10


def _phase10_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, Phase10NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, Phase10Conflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    log.exception("unhandled data-tier error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="internal error"
    )


@router.post("/licenses/{license_id}/quotes", response_model=QuoteOut, status_code=201)
def create_quote(
    license_id: uuid.UUID, payload: QuoteIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = QuoteRepository(session).create(
            scope, deal_id=payload.deal_id, owner_member_id=payload.owner_member_id,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"quote_id": row.quote_id, "status": row.status}),
        )
        session.commit()
        return QuoteOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.get("/licenses/{license_id}/quotes/{quote_id}", response_model=QuoteOut)
def get_quote(
    license_id: uuid.UUID, quote_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    row = QuoteRepository(session).get(scope, quote_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="quote not found")
    return QuoteOut.model_validate(row, from_attributes=True)


@router.get("/licenses/{license_id}/quotes", response_model=list[QuoteOut])
def list_quotes(
    license_id: uuid.UUID, status_: str | None = None, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = QuoteRepository(session).list_for_license(scope, status=status_)
    return [QuoteOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/licenses/{license_id}/quotes/{quote_id}/status", response_model=QuoteOut)
def transition_quote_status(
    license_id: uuid.UUID, quote_id: uuid.UUID, payload: QuoteStatusIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = QuoteRepository(session)
        before = repo.get(scope, quote_id)
        before_status = before.status if before else None
        row = repo.transition_status(scope, quote_id, to_status=payload.status)
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"status": before_status}, {"status": row.status}),
        )
        session.commit()
        return QuoteOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.post(
    "/licenses/{license_id}/document-templates", response_model=DocumentTemplateOut,
    status_code=201,
)
def create_document_template(
    license_id: uuid.UUID, payload: DocumentTemplateIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = DocumentTemplateRepository(session).create_template(
            scope, document_type=payload.document_type,
            template_code=payload.template_code, template_name=payload.template_name,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="document_template", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"template_code": row.template_code}),
        )
        session.commit()
        return DocumentTemplateOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.get(
    "/licenses/{license_id}/document-templates", response_model=list[DocumentTemplateOut],
)
def list_document_templates(
    license_id: uuid.UUID, document_type: str | None = None,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = DocumentTemplateRepository(session).list_templates(scope, document_type=document_type)
    return [DocumentTemplateOut.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "/licenses/{license_id}/document-templates/{template_id}",
    response_model=DocumentTemplateOut,
)
def get_document_template(
    license_id: uuid.UUID, template_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    row = DocumentTemplateRepository(session).get_template(scope, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    return DocumentTemplateOut.model_validate(row, from_attributes=True)


@router.post(
    "/licenses/{license_id}/document-templates/{template_id}/versions",
    response_model=DocumentTemplateVersionOut, status_code=201,
)
def create_document_template_version(
    license_id: uuid.UUID, template_id: uuid.UUID, payload: DocumentTemplateVersionIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = DocumentTemplateRepository(session).create_draft_version(
            scope, template_id,
            source_docx_path=payload.source_docx_path,
            intermediate_model=payload.intermediate_model,
            mapping_schema=payload.mapping_schema,
            compiled_template_path=payload.compiled_template_path,
            renderer=payload.renderer, renderer_mode=payload.renderer_mode,
            smartbrowz_template_id=payload.smartbrowz_template_id,
            created_by=payload.created_by,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="document_template_version", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"version": row.version, "status": row.status}),
        )
        session.commit()
        return DocumentTemplateVersionOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.get(
    "/licenses/{license_id}/document-templates/{template_id}/versions",
    response_model=list[DocumentTemplateVersionOut],
)
def list_document_template_versions(
    license_id: uuid.UUID, template_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        rows = DocumentTemplateRepository(session).list_versions(scope, template_id)
    except Exception as exc:
        raise _phase10_http_error(exc)
    return [DocumentTemplateVersionOut.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "/licenses/{license_id}/document-template-versions/{version_id}",
    response_model=DocumentTemplateVersionOut,
)
def get_document_template_version(
    license_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    row = DocumentTemplateRepository(session).get_version(scope, version_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="template version not found"
        )
    return DocumentTemplateVersionOut.model_validate(row, from_attributes=True)


@router.post(
    "/licenses/{license_id}/document-template-versions/{version_id}/preview",
    response_model=DocumentTemplateVersionOut,
)
def preview_document_template_version(
    license_id: uuid.UUID, version_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """Marks the version "previewed" (10.7: "preview does not publish").
    Does NOT render anything yet — the actual SmartBrowz preview render is
    not built in this patch (see phase10.py's module docstring)."""
    scope = TenantScope(license_id=license_id)
    try:
        repo = DocumentTemplateRepository(session)
        before = repo.get_version(scope, version_id)
        before_status = before.status if before else None
        row = repo.mark_previewed(scope, version_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="document_template_version", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"status": before_status}, {"status": row.status}),
        )
        session.commit()
        return DocumentTemplateVersionOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.post(
    "/licenses/{license_id}/document-template-versions/{version_id}/publish",
    response_model=DocumentTemplateVersionOut,
)
def publish_document_template_version(
    license_id: uuid.UUID, version_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = DocumentTemplateRepository(session)
        before = repo.get_version(scope, version_id)
        before_status = before.status if before else None
        row = repo.publish_version(scope, version_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="document_template_version", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"status": before_status}, {"status": row.status}),
        )
        session.commit()
        return DocumentTemplateVersionOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.post(
    "/licenses/{license_id}/document-template-versions/{version_id}/archive",
    response_model=DocumentTemplateVersionOut,
)
def archive_document_template_version(
    license_id: uuid.UUID, version_id: uuid.UUID,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = DocumentTemplateRepository(session)
        before = repo.get_version(scope, version_id)
        before_status = before.status if before else None
        row = repo.archive_version(scope, version_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="document_template_version", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"status": before_status}, {"status": row.status}),
        )
        session.commit()
        return DocumentTemplateVersionOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.post(
    "/licenses/{license_id}/generated-documents", response_model=GeneratedDocumentOut,
    status_code=201,
)
def record_generated_document(
    license_id: uuid.UUID, payload: GeneratedDocumentIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """Records that a render already happened somewhere else — this
    endpoint does not perform any rendering itself (see phase10.py's
    module docstring for why)."""
    scope = TenantScope(license_id=license_id)
    try:
        row = GeneratedDocumentRepository(session).record(
            scope, document_type=payload.document_type,
            source_entity_type=payload.source_entity_type,
            source_entity_id=payload.source_entity_id,
            template_version_id=payload.template_version_id,
            data_snapshot=payload.data_snapshot, output_path=payload.output_path,
            sha256=payload.sha256, renderer=payload.renderer,
            generated_by=payload.generated_by,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="generated_document", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"output_path": row.output_path}),
        )
        session.commit()
        return GeneratedDocumentOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.get(
    "/licenses/{license_id}/generated-documents/{document_id}",
    response_model=GeneratedDocumentOut,
)
def get_generated_document(
    license_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    row = GeneratedDocumentRepository(session).get(scope, document_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="generated document not found"
        )
    return GeneratedDocumentOut.model_validate(row, from_attributes=True)
