"""Internal API consumed only by the Application Tier.

Nothing here is reachable by a browser or by LINE. Every route requires the
shared internal secret.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import (
    CacheFailureMode,
    CacheUnavailable,
    cache,
    k_active_tenant,
    k_admin_session,
    k_identity,
    k_last_customer_ref,
    k_last_entity_ref,
    k_member,
    k_pending_intent,
    k_permissions,
    k_smartbrowz_token,
)
from ..config import settings
from ..db import get_session
from .. import assignment_engine
from ..models import ChannIdentity, License, LicenseMember
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
    Phase9Duplicate,
    Phase9NotFound,
    StorefrontRepository,
)
from ..repositories.phase11 import AssignmentRuleRepository
from ..repositories.phase15 import (
    ChatSessionConflict,
    ChatSessionNotFound,
    ChatSessionRepository,
)
from ..repositories.phase165 import PdpaConflict, PdpaNotFound, PdpaRepository
from ..repositories.phase18 import PlatformNotFound, PlatformRepository
from ..repositories.phase17 import ReportQueryRepository, ReportSpecInvalid
from ..repositories.phase16 import (
    DisplayPreferenceRepository,
    WarrantyConflict,
    WarrantyNotFound,
    WarrantyRepository,
)
from ..repositories.phase13 import (
    CheckoutBlocked,
    FieldServiceRepository,
    ReportConflict,
    ReportNotFound,
)
from ..repositories.phase12 import (
    DispatchBlocked,
    ServiceTicketRepository,
    TicketConflict,
    TicketNotFound,
)
from ..repositories.phase10 import (
    CompanyProfileRepository,
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
    NoteRepository,
    MessageEntityMapRepository,
    NotificationRepository,
    Phase6Conflict,
    Phase6NotFound,
)
from ..schemas import (
    ArchiveInactiveLeadsIn,
    ReportQueryIn,
    ReportResultOut,
    TenantMemberOut,
    TenantSummaryOut,
    ConsentIn,
    ConsentOut,
    DataSubjectProcessIn,
    DataSubjectRejectIn,
    DataSubjectRequestIn,
    DataSubjectRequestOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatSessionAssignIn,
    ChatSessionOpenIn,
    ChatSessionOut,
    ChatSweepOut,
    AuditLogOut,
    CompanyProfileIn,
    CompanyProfileOut,
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
    SmartBrowzTokenIn,
    SmartBrowzTokenOut,
    StorefrontInterestIn,
    StorefrontProductOut,
    AuditLogWriteIn,
    FollowUpIn,
    NoteBodyIn,
    NoteIn,
    NoteOut,
    FollowUpOut,
    FollowUpStatusIn,
    ActiveTenantIn,
    ActiveTenantOut,
    WarrantyClaimIn,
    PendingIntentIn,
    PendingIntentOut,
    LastCustomerRefIn,
    LastCustomerRefOut,
    LastEntityRefIn,
    LastEntityRefOut,
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
    TeamMemberDetailOut,
    TeamMemberIn,
    TeamMemberOut,
    TeamOut,
    CustomerLinkIn,
    CustomerLinkOut,
    InviteCreateIn,
    InviteOut,
    InviteRedeemIn,
    LicenseCreateIn,
    AssignmentRequestIn,
    AssignmentResultOut,
    AssignmentRuleIn,
    AssignmentRuleOut,
    LicenseOut,
    LineTargetOut,
    CheckInIn,
    CheckOutIn,
    ReportStatusIn,
    ServiceReportOut,
    QuoteProductIn,
    QuoteTermsIn,
    QuoteProductOut,
    QuoteProductPatchIn,
    TicketAssignIn,
    TicketIn,
    TicketMemberActionIn,
    TicketOut,
    TicketPhotoIn,
    TicketPhotoOut,
    TicketPatchIn,
    TicketStatusIn,
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
                license_status=shop.status,
            )
            for shop in shops
        ]

    members = MemberRepository(session).memberships_of(chann_uid, oa=oa)
    return [
        MembershipOut(
            member_id=m.id,
            license_id=m.license_id,
            license_code=m.license.license_code,
            company_name=m.license.company_name,
            chann_uid=m.chann_uid,
            role=m.role,
            status=m.status,
            license_status=m.license.status,
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
    return [
        MemberOut(id=m.id, chann_uid=m.chann_uid, role=m.role, status=m.status)
        for m in members
    ]


@router.get("/licenses/{license_id}/members/{chann_uid}", response_model=MemberOut)
def get_member(license_id: uuid.UUID, chann_uid: str, session: Session = Depends(get_session)):
    scope = TenantScope(license_id=license_id)

    def load():
        member = MemberRepository(session).get(scope, chann_uid)
        if member is None:
            return None
        return {
            "id": member.id,
            "chann_uid": member.chann_uid,
            "role": member.role,
            "status": member.status,
        }

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
        return MemberOut(id=member.id, chann_uid=member.chann_uid, role=member.role, status=member.status)
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
        return MemberOut(id=member.id, chann_uid=member.chann_uid, role=member.role, status=member.status)
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
        owner_member_id = payload.owner_member_id
        if owner_member_id is None and x_actor_id:
            # Default the owner to whoever set the reminder. Without this a
            # follow-up created through chat had no owner at all, so the
            # sweep found it, could not name anyone to tell, and skipped
            # it — a reminder that exists and reaches nobody.
            member = session.execute(
                select(LicenseMember).where(
                    LicenseMember.license_id == license_id,
                    LicenseMember.chann_uid == x_actor_id,
                )
            ).scalars().first()
            if member is not None:
                owner_member_id = member.id

        row = FollowUpRepository(session).create(
            scope,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            due_date=payload.due_date,
            due_time=payload.due_time,
            owner_member_id=owner_member_id,
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
                {}, {
                    "entity_type": row.entity_type,
                    "due_date": str(row.due_date),
                    "due_time": str(row.due_time) if row.due_time else None,
                }
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
    repo = FollowUpRepository(session)
    rows = repo.due_within(scope, days=days)
    # Resolved here, once for the batch: every consumer of this endpoint
    # wants to notify a person, and none of them can do anything with a
    # membership row id on its own.
    owners = repo.owner_chann_uids([r.owner_member_id for r in rows])
    out = []
    for row in rows:
        item = FollowUpOut.model_validate(row, from_attributes=True)
        item.owner_chann_uid = owners.get(row.owner_member_id)
        out.append(item)
    return out


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


@router.put("/chat/active-tenant/{oa}/{chann_uid}", status_code=204)
def set_active_tenant(oa: str, chann_uid: str, payload: ActiveTenantIn):
    """Remember which company this person acts in on this OA. See
    cache.k_active_tenant. The Application checks the id is one of the
    person's memberships before honouring it."""
    cache.set(k_active_tenant(chann_uid, oa), {"license_id": payload.license_id}, payload.ttl_seconds)


@router.get("/chat/active-tenant/{oa}/{chann_uid}", response_model=ActiveTenantOut)
def get_active_tenant(oa: str, chann_uid: str):
    raw = cache.get_or_load(k_active_tenant(chann_uid, oa), ttl_s=0, loader=lambda: None)
    if raw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no active tenant")
    return ActiveTenantOut(**raw)


@router.delete("/chat/active-tenant/{oa}/{chann_uid}", status_code=204)
def clear_active_tenant(oa: str, chann_uid: str):
    cache.invalidate(k_active_tenant(chann_uid, oa))


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


@router.put("/chat/last-entity/{oa}/{chann_uid}", status_code=204)
def set_last_entity_ref(oa: str, chann_uid: str, payload: LastEntityRefIn):
    """Generalises set_last_customer_ref to any entity, for notes and
    reminders — see cache.k_last_entity_ref for why this is a separate key
    from last_customer_ref rather than replacing it."""
    cache.set(
        k_last_entity_ref(chann_uid, oa),
        {
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
            "code": payload.code,
        },
        payload.ttl_seconds,
    )


@router.get("/chat/last-entity/{oa}/{chann_uid}", response_model=LastEntityRefOut)
def get_last_entity_ref(oa: str, chann_uid: str):
    raw = cache.get_or_load(k_last_entity_ref(chann_uid, oa), ttl_s=0, loader=lambda: None)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no recent entity reference"
        )
    return LastEntityRefOut(**raw)


@router.put("/chat/smartbrowz-token", status_code=204)
def set_smartbrowz_token(payload: SmartBrowzTokenIn):
    """Phase 10 — caches a freshly-refreshed SmartBrowz access token.

    Global (no chann_uid/oa in the key — see cache.k_smartbrowz_token for
    why), so every Application-tier instance shares the same still-valid
    token instead of each one refreshing independently against Zoho's own
    rate limit.
    """
    cache.set(
        k_smartbrowz_token(),
        {"access_token": payload.access_token, "api_domain": payload.api_domain},
        payload.ttl_seconds,
    )


@router.get("/chat/smartbrowz-token", response_model=SmartBrowzTokenOut)
def get_smartbrowz_token():
    raw = cache.get_or_load(k_smartbrowz_token(), ttl_s=0, loader=lambda: None)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no cached smartbrowz token"
        )
    return SmartBrowzTokenOut(**raw)


@router.delete("/chat/smartbrowz-token", status_code=204)
def clear_smartbrowz_token():
    cache.invalidate(k_smartbrowz_token())


# ---------------------------------------------------------------- Phase 9 CRM


def _phase9_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, Phase9NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, Phase9Duplicate):
        # Structured, so the caller can offer to open the existing record.
        # "Already exists" without saying WHICH leaves the person to go
        # and search for it themselves, which is most of the work.
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate",
                "message": str(exc),
                "existing_id": exc.existing_id,
                "existing_code": exc.existing_code,
                "field": getattr(exc, "field", "phone"),
            },
        )
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
        expected_close_date=deal.expected_close_date, amount=deal.amount,
        currency=getattr(deal, "currency", None) or "THB",
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
    license_id: uuid.UUID, stage: str | None = None, customer_chann_uid: str | None = None,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    repo = CustomerRepository(session)
    if customer_chann_uid:
        # The customer's own record in this shop (B5: purchase history).
        row = repo.find_by_chann_uid(scope, customer_chann_uid)
        rows = [row] if row is not None and row.archived_at is None else []
    else:
        rows = repo.list_for_license(scope, stage=stage)
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
            amount=payload.amount, currency=payload.currency, expected_close_date=payload.expected_close_date,
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
    license_id: uuid.UUID, stage: str | None = None, contact_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    repo = DealRepository(session)
    if contact_id is not None:
        rows = repo.list_for_contact(scope, contact_id)
    else:
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
            lost_reason=payload.lost_reason,
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
def storefront_search(q: str = "", limit: int = 10, session: Session = Depends(get_session)):
    """Public, cross-tenant, un-scoped — same reasoning as
    RegistrationRepository.find_shops: a customer browsing the storefront
    has no tenant yet, that is what this search is for. No term → the
    whole storefront ("สินค้าทั้งหมด")."""
    repo = StorefrontRepository(session)
    if (q or "").strip():
        results = repo.search_products(q, limit=limit)
    else:
        results = repo.browse_products(limit=limit)
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


# --------------------------------------------------------- Phase 10 company


def _company_profile_out(row) -> CompanyProfileOut:
    missing = CompanyProfileRepository.missing_for_documents(row)
    return CompanyProfileOut(
        legal_name=row.legal_name,
        company_name=row.company_name,
        tax_id=row.tax_id,
        company_address=row.company_address,
        company_phone=row.company_phone,
        company_email=row.company_email,
        vat_rate=row.vat_rate,
        is_document_ready=not missing,
        missing_for_documents=missing,
    )


@router.get("/licenses/{license_id}/company-profile", response_model=CompanyProfileOut)
def get_company_profile(license_id: uuid.UUID, session: Session = Depends(get_session)):
    row = CompanyProfileRepository(session).get(TenantScope(license_id=license_id))
    if row is None:
        raise HTTPException(status_code=404, detail="license not found")
    return _company_profile_out(row)


@router.patch("/licenses/{license_id}/company-profile", response_model=CompanyProfileOut)
def patch_company_profile(
    license_id: uuid.UUID,
    payload: CompanyProfileIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Partial update — only fields actually sent are changed.

    `exclude_unset` (not `exclude_none`) is what makes "clear this field"
    expressible: an explicitly-sent null clears, an omitted key is left
    alone. For vat_rate those are genuinely different intentions.
    """
    scope = TenantScope(license_id=license_id)
    repo = CompanyProfileRepository(session)
    try:
        existing = repo.get(scope)
        if existing is None:
            raise HTTPException(status_code=404, detail="license not found")
        tracked = (
            "legal_name", "tax_id", "company_address",
            "company_phone", "company_email", "vat_rate",
        )
        before = {f: getattr(existing, f) for f in tracked}
        row = repo.update(scope, payload.model_dump(exclude_unset=True))
        after = {f: getattr(row, f) for f in tracked}
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="company_profile",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="update",
            # Decimal/None don't serialise into the audit JSON cleanly, and a
            # tax ID does not belong in a diff blob any more than it has to.
            field_changes=diff_fields(
                {k: (str(v) if v is not None else None) for k, v in before.items()},
                {k: (str(v) if v is not None else None) for k, v in after.items()},
            ),
        )
        session.commit()
        return _company_profile_out(row)
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        session.rollback()
        raise HTTPException(status_code=404, detail="license not found")
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.post(
    "/licenses/{license_id}/quotes/{quote_id}/document", response_model=QuoteOut,
)
def link_quote_document(
    license_id: uuid.UUID,
    quote_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Phase 10 — record which generated document belongs to this quote."""
    scope = TenantScope(license_id=license_id)
    try:
        row = QuoteRepository(session).link_document(scope, quote_id, document_id)
        AuditRepository(session).write(
            license_id=license_id,
            entity_type="quote",
            entity_id=row.id,
            actor_type="user",
            actor_id=x_actor_id or None,
            action="link_document",
            field_changes=diff_fields(
                {"generated_document_id": None},
                {"generated_document_id": str(document_id)},
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except Phase10NotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.patch("/licenses/{license_id}/deals/{deal_id}", response_model=DealOut)
def update_deal(
    license_id: uuid.UUID,
    deal_id: uuid.UUID,
    payload: dict,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Phase 9/10 — edit a deal's own attributes.

    Stage changes go through the dedicated /stage endpoint, which owns the
    state machine and the reopen permission; this must not become a way
    around it.
    """
    scope = TenantScope(license_id=license_id)
    repo = DealRepository(session)
    try:
        existing = repo.get(scope, deal_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="deal not found")
        tracked = ("notes", "owner_member_id")
        before = {f: getattr(existing, f) for f in tracked}
        row = repo.update(scope, deal_id, payload)
        after = {f: getattr(row, f) for f in tracked}
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields(
                {k: (str(v) if v is not None else None) for k, v in before.items()},
                {k: (str(v) if v is not None else None) for k, v in after.items()},
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except HTTPException:
        session.rollback()
        raise
    except Phase9NotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete(
    "/licenses/{license_id}/deals/{deal_id}/products/{deal_product_id}",
    status_code=204,
)
def remove_deal_product(
    license_id: uuid.UUID,
    deal_id: uuid.UUID,
    deal_product_id: uuid.UUID,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Phase 9 — take a line item off a deal.

    Audited with the product's name rather than only its id: a deleted row
    cannot be looked up afterwards, so the audit entry is the only place
    that will ever say what was removed.
    """
    scope = TenantScope(license_id=license_id)
    try:
        removed = DealRepository(session).remove_product(scope, deal_id, deal_product_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=deal_id,
            actor_type="user", actor_id=x_actor_id or None, action="remove_product",
            field_changes=diff_fields(
                {"product_name": removed.product_name, "qty": str(removed.qty)},
                {"product_name": None, "qty": None},
            ),
        )
        session.commit()
        return None
    except Phase9NotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


# ------------------------------------------------------------------ notes


@router.post("/licenses/{license_id}/notes", response_model=NoteOut, status_code=201)
def create_note(
    license_id: uuid.UUID,
    payload: NoteIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Master Spec 6.3 — an append-only note against any entity."""
    scope = TenantScope(license_id=license_id)
    try:
        row = NoteRepository(session).create(
            scope,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            body=payload.body,
            author_chann_uid=x_actor_id or None,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="note", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields(
                {}, {"entity_type": row.entity_type, "entity_id": str(row.entity_id)}
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.get("/licenses/{license_id}/notes", response_model=list[NoteOut])
def list_notes(
    license_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    return NoteRepository(session).list_for_entity(
        TenantScope(license_id=license_id),
        entity_type=entity_type, entity_id=entity_id, limit=limit,
    )


@router.patch("/licenses/{license_id}/notes/{note_id}", response_model=NoteOut)
def update_note(
    license_id: uuid.UUID,
    note_id: uuid.UUID,
    payload: NoteBodyIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        before = NoteRepository(session).get(scope, note_id)
        previous = before.body if before else None
        row = NoteRepository(session).update(scope, note_id, body=payload.body)
        AuditRepository(session).write(
            license_id=license_id, entity_type="note", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            # The old text is kept in the audit entry: editing a note
            # rewrites a record other people may already have acted on.
            field_changes=diff_fields({"body": previous}, {"body": row.body}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.delete("/licenses/{license_id}/notes/{note_id}", status_code=204)
def delete_note(
    license_id: uuid.UUID,
    note_id: uuid.UUID,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        removed = NoteRepository(session).delete(scope, note_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="note", entity_id=note_id,
            actor_type="user", actor_id=x_actor_id or None, action="delete",
            # The body goes into the audit entry because the row is gone:
            # this is the only remaining record of what was deleted.
            field_changes=diff_fields({"body": removed.body}, {"body": None}),
        )
        session.commit()
        return None
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.get("/licenses", response_model=list[LicenseOut])
def list_licenses(
    status: str | None = None,
    exclude_status: str | None = None,
    session: Session = Depends(get_session),
):
    """Every tenant, for platform-wide scheduled work.

    Deliberately not tenant-scoped, because the caller is a scheduler asking
    "which tenants have work due", not a tenant asking about itself. It sits
    on the internal API, which is only reachable from the Application tier —
    the same trust boundary every other endpoint here relies on.
    """
    query = select(License)
    if status:
        query = query.where(License.status == status)
    if exclude_status:
        # Excluding is usually what a caller means: a tenant's license
        # defaults to "trial", so an include-filter on "active" quietly
        # matches nothing, and any status added later would be excluded by
        # an include-list without anyone noticing.
        query = query.where(License.status != exclude_status)
    return list(session.execute(query.order_by(License.created_at)).scalars())


@router.get("/identities/{chann_uid}/line-target", response_model=LineTargetOut)
def get_line_target(chann_uid: str, session: Session = Depends(get_session)):
    """The LINE user id to push to, for a person known only by chann_uid.

    Anything that notifies needs this. Without it the reminder sweep passed
    target_line_user_id=None, and send_notification treats a missing LINE
    target as "record it for the dashboard and stop" — so reminders were
    stored, counted as sent, and never reached anyone.
    """
    row = session.execute(
        select(ChannIdentity).where(ChannIdentity.chann_uid == chann_uid)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="identity not found")
    return LineTargetOut(chann_uid=row.chann_uid, line_user_id=row.line_user_id)


@router.get("/licenses/{license_id}/notifications/announced-today")
def announced_today(
    license_id: uuid.UUID,
    type: str,
    on_day: date | None = None,
    session: Session = Depends(get_session),
):
    """Which entity_ids already got a notification of this type today.

    Lets a scheduled job be safely retried: Cloud Scheduler retries a failed
    run, a run can fail partway through sending, and re-sending everything
    that already went out teaches people to ignore the notifications.
    """
    scope = TenantScope(license_id=license_id)
    ids = NotificationRepository(session).announced_entity_ids_today(
        scope, type=type, on_day=on_day or date.today(),
    )
    return {"entity_ids": [str(i) for i in ids]}


# ------------------------------------------------------ Phase 11 assignment


@router.get(
    "/licenses/{license_id}/assignment-rules", response_model=list[AssignmentRuleOut],
)
def list_assignment_rules(
    license_id: uuid.UUID, session: Session = Depends(get_session),
):
    return AssignmentRuleRepository(session).list_for_license(
        TenantScope(license_id=license_id)
    )


@router.put(
    "/licenses/{license_id}/assignment-rules", response_model=AssignmentRuleOut,
)
def upsert_assignment_rule(
    license_id: uuid.UUID,
    payload: AssignmentRuleIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Replace the active rule for a scope.

    The old rule is deactivated rather than overwritten — it explains how
    existing records came to be assigned, and the audit trail points at it.
    """
    scope = TenantScope(license_id=license_id)
    try:
        row = AssignmentRuleRepository(session).upsert_active(
            scope, rule_scope=payload.scope, rules_json=payload.rules_json,
            updated_by=payload.updated_by,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="assignment_rule", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="upsert",
            field_changes=diff_fields({}, {"scope": row.scope}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.post(
    "/licenses/{license_id}/assignment-rules/execute",
    response_model=AssignmentResultOut,
)
def execute_assignment(
    license_id: uuid.UUID,
    payload: AssignmentRequestIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Assign one record, under a lock, and record why.

    The lock covers reading current loads AND writing the assignment. Ten
    tickets arriving at once must produce five assignments and five
    overflows against a five-a-day cap — not ten members each reading
    "load is 4" and all concluding they have room.

    The engine itself lives in the Application tier and is pure; this
    endpoint supplies it with candidates and loads read inside the lock,
    then persists what it decided.
    """
    scope = TenantScope(license_id=license_id)
    repo = AssignmentRuleRepository(session)
    try:
        rule_row = repo.get_active(scope, rule_scope=payload.scope)
        rule = rule_row.rules_json if rule_row else {}

        # Everything from here to commit is serialised per tenant.
        repo.lock_license(scope)

        matched_team = assignment_engine.match_team(rule, payload.context)
        if matched_team:
            candidates = repo.team_members(scope, team_name=matched_team)
        else:
            # No criterion matched: fall back to everyone who could do this
            # kind of work, rather than refusing. A rule that does not
            # mention a case is a gap in the policy, not a reason to leave
            # the job unowned.
            candidates = repo.active_members(scope)

        loads = repo.current_loads(
            scope, [c["id"] for c in candidates], on_day=date.today(),
        )
        outcome = assignment_engine.choose(
            rule, candidates, loads,
            matched_team=matched_team,
            owner_candidates=repo.owner_members(scope),
        )

        if outcome.member_id and payload.entity_type == "deal":
            repo.assign_deal(scope, payload.entity_id, uuid.UUID(outcome.member_id))

        AuditRepository(session).write(
            license_id=license_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            # actor_type=ai per 11.5: the rule was authored by the model,
            # and the reasoning is stored so an assignment can be explained
            # later without re-running the engine against changed data.
            actor_type="ai",
            actor_id=x_actor_id or None,
            action="assign",
            ai_reasoning=outcome.reason,
            field_changes=diff_fields(
                {"owner_member_id": None}, {"owner_member_id": outcome.member_id},
            ),
        )
        session.commit()
        return AssignmentResultOut(
            member_id=uuid.UUID(outcome.member_id) if outcome.member_id else None,
            reason=outcome.reason,
            matched_team=outcome.matched_team,
            strategy=outcome.strategy,
            warnings=outcome.warnings,
            used_fallback=outcome.used_fallback,
        )
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


# --------------------------------------------------------- Phase 12 tickets


def _ticket_error(exc: Exception):
    """Turn a repository refusal into the right status code.

    DispatchBlocked is a 409 carrying WHICH fields are missing: the caller
    shows that list to a person who then fills them in, and a bare "cannot
    dispatch" would make them guess between five possibilities.
    """
    if isinstance(exc, DispatchBlocked):
        return HTTPException(
            status_code=409,
            detail={"error": "dispatch_blocked", "missing": exc.missing},
        )
    if isinstance(exc, TicketNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TicketConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return _phase2_http_error(exc)


@router.post("/licenses/{license_id}/tickets", response_model=TicketOut, status_code=201)
def create_ticket(
    license_id: uuid.UUID,
    payload: TicketIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ServiceTicketRepository(session).create(scope, **payload.model_dump())
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"ticket_number": row.ticket_number}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


@router.get("/licenses/{license_id}/tickets", response_model=list[TicketOut])
def list_tickets(
    license_id: uuid.UUID,
    status: str | None = None,
    visible_to: uuid.UUID | None = None,
    session: Session = Depends(get_session),
):
    """Tickets, optionally filtered to what one technician may see.

    visible_to is not a convenience: a technician browsing without it would
    read the address and phone number of every private job in the tenant.
    """
    scope = TenantScope(license_id=license_id)
    repo = ServiceTicketRepository(session)
    if visible_to:
        return repo.list_visible_to(scope, member_id=visible_to)
    return repo.list_for_license(scope, status=status)


@router.get("/licenses/{license_id}/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(
    license_id: uuid.UUID, ticket_id: uuid.UUID, session: Session = Depends(get_session),
):
    row = ServiceTicketRepository(session).get(TenantScope(license_id=license_id), ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return row


@router.patch("/licenses/{license_id}/tickets/{ticket_id}", response_model=TicketOut)
def update_ticket(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketPatchIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ServiceTicketRepository(session).update(
            scope, ticket_id, payload.model_dump(exclude_unset=True),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


@router.get("/licenses/{license_id}/tickets/{ticket_id}/dispatch-check")
def dispatch_check(
    license_id: uuid.UUID, ticket_id: uuid.UUID, session: Session = Depends(get_session),
):
    """What is still missing before this ticket can be dispatched.

    Readable without attempting an assignment, so a dashboard can show the
    gaps while someone is still filling the form rather than only after
    they press assign.
    """
    scope = TenantScope(license_id=license_id)
    repo = ServiceTicketRepository(session)
    row = repo.get(scope, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    missing = repo.dispatch_blockers(row)
    return {"ready": not missing, "missing": missing}


@router.post("/licenses/{license_id}/tickets/{ticket_id}/assign", response_model=TicketOut)
def assign_ticket(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketAssignIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ServiceTicketRepository(session).assign(
            scope, ticket_id,
            target_type=payload.target_type, target_ref=payload.target_ref,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="assign",
            field_changes=diff_fields(
                {}, {"target_type": payload.target_type, "target_ref": str(payload.target_ref)},
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/claim", response_model=TicketOut)
def claim_ticket(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketMemberActionIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ServiceTicketRepository(session).claim(
            scope, ticket_id, member_id=payload.member_id,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="claim",
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


@router.post("/licenses/{license_id}/tickets/{ticket_id}/reject", response_model=TicketOut)
def reject_ticket(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketMemberActionIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ServiceTicketRepository(session).reject(
            scope, ticket_id, member_id=payload.member_id,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="reject",
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


@router.patch(
    "/licenses/{license_id}/tickets/{ticket_id}/status", response_model=TicketOut,
)
def set_ticket_status(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketStatusIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        before = ServiceTicketRepository(session).get(scope, ticket_id)
        previous = before.status if before else None
        row = ServiceTicketRepository(session).set_status(
            scope, ticket_id, status=payload.status,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="status",
            field_changes=diff_fields({"status": previous}, {"status": row.status}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _ticket_error(exc)


# --------------------------------------------------- Phase 13 field service


def _field_service_error(exc: Exception):
    """The check-out gate's refusal names WHICH fields are missing.

    Same reasoning as the dispatch gate: a technician told only "cannot
    check out" has to guess, and they are standing in a customer's house
    while they do it.
    """
    if isinstance(exc, CheckoutBlocked):
        return HTTPException(
            status_code=409,
            detail={"error": "checkout_blocked", "missing": exc.missing},
        )
    if isinstance(exc, ReportNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReportConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return _phase2_http_error(exc)


@router.post(
    "/licenses/{license_id}/tickets/{ticket_id}/check-in", response_model=TicketOut,
)
def check_in(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: CheckInIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = FieldServiceRepository(session).check_in(
            scope, ticket_id,
            member_id=payload.member_id,
            gps_lat=payload.gps_lat, gps_lng=payload.gps_lng,
            photo_url=payload.photo_url,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_ticket", entity_id=ticket_id,
            actor_type="user", actor_id=x_actor_id or None, action="check_in",
            field_changes=diff_fields(
                {}, {"gps": f"{payload.gps_lat},{payload.gps_lng}"},
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _field_service_error(exc)


@router.post(
    "/licenses/{license_id}/tickets/{ticket_id}/check-out",
    response_model=ServiceReportOut,
)
def check_out(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: CheckOutIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Close a visit, creating its report in the same call.

    One endpoint rather than two, because a check-out without a report is
    exactly what the gate exists to prevent and two endpoints would mean
    two orders they could happen in.
    """
    scope = TenantScope(license_id=license_id)
    try:
        row = FieldServiceRepository(session).check_out(
            scope, ticket_id,
            member_id=payload.member_id, report_data=payload.report_data,
            gps_lat=payload.gps_lat, gps_lng=payload.gps_lng,
            photo_url=payload.photo_url,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_report", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="check_out",
            field_changes=diff_fields({}, {"report_id": row.report_id}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _field_service_error(exc)


@router.get("/licenses/{license_id}/tickets/{ticket_id}/checkout-check")
def checkout_check(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    found_issue: str = "",
    work_done: str = "",
    session: Session = Depends(get_session),
):
    """What the report still has to say, without attempting a check-out.

    So a form can show the gaps while the technician is still typing,
    rather than only when they try to leave.
    """
    missing = FieldServiceRepository(session).report_blockers(
        {"found_issue": found_issue, "work_done": work_done}
    )
    return {"ready": not missing, "missing": missing}


@router.post(
    "/licenses/{license_id}/tickets/{ticket_id}/photos",
    response_model=TicketPhotoOut, status_code=201,
)
def add_ticket_photo(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    payload: TicketPhotoIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = FieldServiceRepository(session).add_photo(
            scope, ticket_id=ticket_id, **payload.model_dump(),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _field_service_error(exc)


@router.get(
    "/licenses/{license_id}/tickets/{ticket_id}/photos",
    response_model=list[TicketPhotoOut],
)
def list_ticket_photos(
    license_id: uuid.UUID,
    ticket_id: uuid.UUID,
    photo_type: str | None = None,
    session: Session = Depends(get_session),
):
    return FieldServiceRepository(session).list_photos(
        TenantScope(license_id=license_id), ticket_id, photo_type=photo_type,
    )


@router.post(
    "/licenses/{license_id}/service-reports/{report_id}/document",
    response_model=ServiceReportOut,
)
def attach_service_report_document(
    license_id: uuid.UUID,
    report_id: uuid.UUID,
    document_id: uuid.UUID,
    pdf_path: str,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Phase 13.4 — record which generated document is this report's PDF.
    Same shape as the quote's link_document: the Application stores and
    records first, then links, so a failure here leaves a findable
    document rather than a report pointing at nothing."""
    scope = TenantScope(license_id=license_id)
    try:
        row = FieldServiceRepository(session).attach_document(
            scope, report_id, document_id=document_id, pdf_path=pdf_path,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_report", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="link_document",
            field_changes=diff_fields(
                {"generated_document_id": None},
                {"generated_document_id": str(document_id)},
            ),
        )
        session.commit()
        session.refresh(row)
        return row
    except ReportNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


@router.get("/licenses/{license_id}/service-reports", response_model=list[ServiceReportOut])
def list_service_reports(
    license_id: uuid.UUID,
    status: str | None = None,
    session: Session = Depends(get_session),
):
    return FieldServiceRepository(session).list_reports(
        TenantScope(license_id=license_id), status=status,
    )


@router.get(
    "/licenses/{license_id}/service-reports/{report_id}", response_model=ServiceReportOut,
)
def get_service_report(
    license_id: uuid.UUID, report_id: uuid.UUID, session: Session = Depends(get_session),
):
    row = FieldServiceRepository(session).get_report(
        TenantScope(license_id=license_id), report_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="service report not found")
    return row


@router.patch(
    "/licenses/{license_id}/service-reports/{report_id}/status",
    response_model=ServiceReportOut,
)
def set_service_report_status(
    license_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: ReportStatusIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = FieldServiceRepository(session).set_report_status(
            scope, report_id, status=payload.status,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_report", entity_id=report_id,
            actor_type="user", actor_id=x_actor_id or None, action="status",
            field_changes=diff_fields({}, {"status": row.status}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _field_service_error(exc)


@router.get(
    "/licenses/{license_id}/technician-teams/{team_id}/members",
    response_model=list[TeamMemberDetailOut],
)
def list_technician_team_members(
    license_id: uuid.UUID, team_id: uuid.UUID, session: Session = Depends(get_session),
):
    """Who is on a team.

    Needed to notify a team about a ticket: 12.4's team flow is that any
    member may take it, so telling only the lead would make the others'
    ability to claim it useless.
    """
    rows = AssignmentRuleRepository(session).team_members_with_lead(
        TenantScope(license_id=license_id), team_id=team_id,
    )
    return [
        TeamMemberDetailOut(
            id=member.id, chann_uid=member.chann_uid, role=member.role,
            status=member.status, is_lead=is_lead,
        )
        for member, is_lead in rows
    ]


# --------------------------------------------------------- Phase 14 approvals


def _approval_error(exc: Exception):
    from ..repositories.phase14 import ApprovalConflict, ApprovalNotFound
    from ..repositories.tenant_scope import CrossTenantAccessDenied

    if isinstance(exc, CrossTenantAccessDenied):
        return HTTPException(status_code=404, detail="not found")
    if isinstance(exc, ApprovalNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ApprovalConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, HTTPException):
        return exc
    raise exc


def _step_out(row) -> dict:
    return {
        "id": str(row.id), "entity_type": row.entity_type, "entity_id": str(row.entity_id),
        "workflow_id": str(row.workflow_id) if row.workflow_id else None,
        "step_order": row.step_order, "approver_type": row.approver_type,
        "approver_ref": row.approver_ref, "status": row.status,
        "acted_by": str(row.acted_by) if row.acted_by else None,
        "acted_at": row.acted_at.isoformat() if row.acted_at else None,
        "reason": row.reason, "created_at": row.created_at.isoformat(),
    }


def _survey_out(row) -> dict:
    return {
        "id": str(row.id), "ticket_id": str(row.ticket_id),
        "scale_config_json": row.scale_config_json, "score": row.score,
        "comment": row.comment,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
    }


def _workflow_out(row) -> dict:
    return {
        "id": str(row.id), "entity_type": row.entity_type, "rules_json": row.rules_json,
        "is_active": row.is_active,
        "updated_by": str(row.updated_by) if row.updated_by else None,
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/licenses/{license_id}/approval-workflows/{entity_type}")
def get_approval_workflow(
    license_id: uuid.UUID, entity_type: str, session: Session = Depends(get_session),
):
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    try:
        row = ApprovalRepository(session).active_workflow(scope, entity_type)
        session.commit()
        return _workflow_out(row)
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


@router.put("/licenses/{license_id}/approval-workflows/{entity_type}")
def replace_approval_workflow(
    license_id: uuid.UUID, entity_type: str, payload: dict,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    try:
        updated_by = payload.get("updated_by")
        row = ApprovalRepository(session).replace_workflow(
            scope, entity_type, payload.get("rules_json") or {},
            updated_by=uuid.UUID(updated_by) if updated_by else None,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="approval_workflow", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, {"rules_json": row.rules_json}),
        )
        session.commit()
        return _workflow_out(row)
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


@router.post(
    "/licenses/{license_id}/service-reports/{report_id}/approval-steps", status_code=201,
)
def open_approval_steps(
    license_id: uuid.UUID, report_id: uuid.UUID, session: Session = Depends(get_session),
):
    """Start (or restart, after a reject) the approval flow for a report."""
    from ..models import ServiceReport
    from ..repositories.phase14 import ApprovalNotFound, ApprovalRepository

    scope = TenantScope(license_id=license_id)
    try:
        report = session.get(ServiceReport, report_id)
        if report is None:
            raise ApprovalNotFound("service report not found")
        steps = ApprovalRepository(session).open_steps_for_report(scope, report)
        session.commit()
        return [_step_out(s) for s in steps]
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


@router.get("/licenses/{license_id}/approval-steps/pending")
def pending_approval_steps(
    license_id: uuid.UUID, member_id: uuid.UUID | None = None, roles: str = "",
    session: Session = Depends(get_session),
):
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    role_names = [r for r in roles.split(",") if r]
    rows = ApprovalRepository(session).pending_for(scope, member_id=member_id, role_names=role_names)
    return [_step_out(s) for s in rows]


@router.get("/licenses/{license_id}/approval-steps/for/{entity_type}/{entity_id}")
def approval_steps_for_entity(
    license_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID,
    session: Session = Depends(get_session),
):
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    rows = ApprovalRepository(session).steps_for_entity(scope, entity_type, entity_id)
    return [_step_out(s) for s in rows]


@router.post("/licenses/{license_id}/approval-steps/{step_id}/act")
def act_on_approval_step(
    license_id: uuid.UUID, step_id: uuid.UUID, payload: dict,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """Approve or reject. Response carries the report's resulting status
    and, when this was the last approval, the survey to send — so the
    caller acts on the same facts this transaction committed."""
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    try:
        member_id = payload.get("member_id")
        step, report_status, survey = ApprovalRepository(session).act(
            scope, step_id, approve=bool(payload.get("approve")),
            member_id=uuid.UUID(member_id) if member_id else None,
            role_names=list(payload.get("roles") or []),
            reason=payload.get("reason"),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="service_report", entity_id=step.entity_id,
            actor_type="user", actor_id=x_actor_id or None,
            action="status" if payload.get("approve") else "reject",
            field_changes=diff_fields({}, {"status": report_status, "step": step.step_order}),
        )
        session.commit()
        return {
            "step": _step_out(step), "report_status": report_status,
            "survey": _survey_out(survey) if survey else None,
        }
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


@router.get("/licenses/{license_id}/surveys/pending-for-ticket/{ticket_id}")
def pending_survey(
    license_id: uuid.UUID, ticket_id: uuid.UUID, session: Session = Depends(get_session),
):
    from ..repositories.phase14 import ApprovalRepository

    row = ApprovalRepository(session).pending_survey_for_ticket(
        TenantScope(license_id=license_id), ticket_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no pending survey")
    return _survey_out(row)


@router.post("/licenses/{license_id}/surveys/{survey_id}/sent")
def mark_survey_sent(
    license_id: uuid.UUID, survey_id: uuid.UUID, session: Session = Depends(get_session),
):
    from ..repositories.phase14 import ApprovalRepository

    try:
        row = ApprovalRepository(session).mark_survey_sent(
            TenantScope(license_id=license_id), survey_id,
        )
        session.commit()
        return _survey_out(row)
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


@router.post("/licenses/{license_id}/surveys/{survey_id}/answer")
def answer_survey(
    license_id: uuid.UUID, survey_id: uuid.UUID, payload: dict,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    from ..repositories.phase14 import ApprovalRepository

    scope = TenantScope(license_id=license_id)
    try:
        row = ApprovalRepository(session).submit_survey(
            scope, survey_id, score=int(payload.get("score")), comment=payload.get("comment"),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="satisfaction_survey", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, {"score": row.score}),
        )
        session.commit()
        return _survey_out(row)
    except Exception as exc:
        session.rollback()
        raise _approval_error(exc)


# ------------------------------------------- Phase 7.5 / 16 warranties


def _warranty_error(exc: Exception):
    if isinstance(exc, WarrantyNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, WarrantyConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return _phase2_http_error(exc)


@router.post("/licenses/{license_id}/warranties", status_code=201)
def register_warranty(
    license_id: uuid.UUID,
    payload: dict,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = WarrantyRepository(session).register(
            scope,
            serial_number=str(payload.get("serial_number") or ""),
            product_id=(
                uuid.UUID(payload["product_id"]) if payload.get("product_id") else None
            ),
            product_name=payload.get("product_name"),
            customer_chann_uid=payload.get("customer_chann_uid"),
            contact_id=(
                uuid.UUID(payload["contact_id"]) if payload.get("contact_id") else None
            ),
            warranty_start=(
                date.fromisoformat(payload["warranty_start"])
                if payload.get("warranty_start") else None
            ),
            warranty_months=payload.get("warranty_months"),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="warranty", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="create",
            field_changes=diff_fields({}, {"serial_number": row.serial_number}),
        )
        session.commit()
        session.refresh(row)
        return {
            "id": str(row.id), "warranty_number": row.warranty_number,
            "serial_number": row.serial_number, "product_name": row.product_name,
            "warranty_start": row.warranty_start.isoformat(),
            "warranty_end": row.warranty_end.isoformat(), "status": row.status,
        }
    except Exception as exc:
        session.rollback()
        raise _warranty_error(exc)


@router.get("/licenses/{license_id}/warranties")
def list_warranties(
    license_id: uuid.UUID,
    serial_number: str | None = None,
    customer_chann_uid: str | None = None,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    repo = WarrantyRepository(session)
    if serial_number:
        row = repo.by_serial(scope, serial_number)
        rows = [row] if row else []
    elif customer_chann_uid:
        rows = repo.for_customer(scope, customer_chann_uid)
    else:
        rows = repo.list_for_license(scope)
    return [
        {
            "id": str(r.id), "warranty_number": r.warranty_number,
            "serial_number": r.serial_number, "product_name": r.product_name,
            "customer_chann_uid": r.customer_chann_uid,
            "warranty_start": r.warranty_start.isoformat(),
            "warranty_end": r.warranty_end.isoformat(), "status": r.status,
        }
        for r in rows
    ]


@router.post("/licenses/{license_id}/warranties/claim")
def claim_warranty(
    license_id: uuid.UUID,
    payload: WarrantyClaimIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """The customer's side of registration (owner rule, 3 Sep): match the
    sticker to a unit the shop registered. 404 when the shop has no such
    unit, 409 when another customer holds it."""
    scope = TenantScope(license_id=license_id)
    try:
        row = WarrantyRepository(session).claim(
            scope, serial_number=payload.serial_number,
            customer_chann_uid=payload.customer_chann_uid,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="warranty", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, {"customer_chann_uid": row.customer_chann_uid}),
        )
        session.commit()
        session.refresh(row)
        return {
            "id": str(row.id), "warranty_number": row.warranty_number,
            "serial_number": row.serial_number, "product_name": row.product_name,
            "customer_chann_uid": row.customer_chann_uid,
            "warranty_start": row.warranty_start.isoformat(),
            "warranty_end": row.warranty_end.isoformat(), "status": row.status,
        }
    except Exception as exc:
        session.rollback()
        raise _warranty_error(exc)


@router.get("/warranties/lookup")
def lookup_serial_across_tenants(
    serial_number: str,
    actor_chann_uid: str = "",
    session: Session = Depends(get_session),
):
    """16.4 — which shops have registered this serial.

    The one query in this system that deliberately crosses the tenant
    boundary, because "my thing is broken, who do I talk to" cannot be
    answered inside a single tenant.

    Returns only what identifies a shop. Audited with cross_tenant=true on
    every call, which is what that column exists for.
    """
    repo = WarrantyRepository(session)
    matches = repo.find_shops_by_serial(serial_number)

    # Audited whether or not anything was found: a search that returns
    # nothing is still someone asking about a serial, and an audit trail
    # with only the hits in it cannot answer "who went looking".
    AuditRepository(session).write(
        license_id=None,
        entity_type="warranty_lookup",
        entity_id=uuid.uuid4(),
        actor_type="user",
        actor_id=actor_chann_uid or None,
        action="cross_tenant_lookup",
        cross_tenant=True,
        field_changes=diff_fields(
            {}, {"serial_number": serial_number, "matches": len(matches)},
        ),
    )
    session.commit()
    return {"serial_number": serial_number, "matches": matches}


@router.get("/identities/{chann_uid}/signature")
def get_identity_signature(chann_uid: str, session: Session = Depends(get_session)):
    """13.5 — where this person's signature image is stored (an object
    path, or null). Read by the report renderer; uploading one is the
    profile's job (not built yet — the report leaves a labelled line)."""
    row = session.execute(
        select(ChannIdentity).where(ChannIdentity.chann_uid == chann_uid)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity not found")
    return {"chann_uid": chann_uid, "signature_url": row.signature_url}


@router.put("/identities/{chann_uid}/signature")
def set_identity_signature(chann_uid: str, payload: dict, session: Session = Depends(get_session)):
    """13.5 — record where this person's signature image now lives (the
    Application stored the bytes first)."""
    row = session.execute(
        select(ChannIdentity).where(ChannIdentity.chann_uid == chann_uid)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="identity not found")
    row.signature_url = str(payload.get("signature_url") or "") or None
    session.commit()
    return {"chann_uid": chann_uid, "signature_url": row.signature_url}


@router.get("/identities/{chann_uid}/display-preferences")
def get_display_preferences(chann_uid: str, session: Session = Depends(get_session)):
    return DisplayPreferenceRepository(session).get(chann_uid)


@router.put("/identities/{chann_uid}/display-preferences")
def set_display_preferences(
    chann_uid: str, payload: dict, session: Session = Depends(get_session),
):
    try:
        result = DisplayPreferenceRepository(session).upsert(chann_uid, payload)
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        raise _phase2_http_error(exc)


# ------------------------------------------------- Phase 10 quote lines
#
# A quote owns its lines from creation, copied from the deal. Editing them
# changes THIS offer only — the deal, and every other quote made from it,
# are untouched. That is what makes "ลดให้เหลือ 1,400" a safe thing to say.


@router.get(
    "/licenses/{license_id}/quotes/{quote_id}/products",
    response_model=list[QuoteProductOut],
)
def list_quote_products(
    license_id: uuid.UUID, quote_id: uuid.UUID, session: Session = Depends(get_session),
):
    return QuoteRepository(session).list_products(
        TenantScope(license_id=license_id), quote_id,
    )


@router.post(
    "/licenses/{license_id}/quotes/{quote_id}/products",
    response_model=QuoteProductOut, status_code=201,
)
def add_quote_product(
    license_id: uuid.UUID,
    quote_id: uuid.UUID,
    payload: QuoteProductIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = QuoteRepository(session).add_product(
            scope, quote_id, **payload.model_dump(),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=quote_id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, {"added_line": row.product_name}),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.patch(
    "/licenses/{license_id}/quotes/{quote_id}/products/{line_id}",
    response_model=QuoteProductOut,
)
def update_quote_product(
    license_id: uuid.UUID,
    quote_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: QuoteProductPatchIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = QuoteRepository(session).update_product(
            scope, quote_id, line_id, payload.model_dump(exclude_unset=True),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=quote_id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, payload.model_dump(exclude_unset=True)),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.delete(
    "/licenses/{license_id}/quotes/{quote_id}/products/{line_id}", status_code=204,
)
def remove_quote_product(
    license_id: uuid.UUID,
    quote_id: uuid.UUID,
    line_id: uuid.UUID,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        QuoteRepository(session).remove_product(scope, quote_id, line_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=quote_id,
            actor_type="user", actor_id=x_actor_id or None, action="remove_product",
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.patch(
    "/licenses/{license_id}/deals/{deal_id}/products/{deal_product_id}",
    response_model=DealProductOut,
)
def update_deal_product(
    license_id: uuid.UUID,
    deal_id: uuid.UUID,
    deal_product_id: uuid.UUID,
    payload: dict,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """Correct a line without deleting and retyping it.

    Delete-and-retype loses the line's position and, on a deal with
    several similar products, is easy to do to the wrong one.
    """
    scope = TenantScope(license_id=license_id)
    try:
        row = DealRepository(session).update_product(
            scope, deal_id, deal_product_id, payload,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="deal", entity_id=deal_id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, payload),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)


@router.post("/licenses/{license_id}/quotes/expire-overdue")
def expire_overdue_quotes(
    license_id: uuid.UUID, session: Session = Depends(get_session),
):
    """Mark sent quotes whose validity has passed.

    The "expired" status has existed since Phase 10 with nothing able to
    set it. A quote that says it is valid until last month, still sitting
    in "sent", tells a salesperson the offer stands when it does not.
    """
    scope = TenantScope(license_id=license_id)
    try:
        count = QuoteRepository(session).expire_overdue(scope)
        session.commit()
        return {"expired": count}
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)


@router.get("/licenses/{license_id}/pipeline")
def pipeline_summary(license_id: uuid.UUID, session: Session = Depends(get_session)):
    """Counts and value by stage, plus what is closing this month."""
    return DealRepository(session).pipeline_summary(TenantScope(license_id=license_id))


@router.patch(
    "/licenses/{license_id}/quotes/{quote_id}/terms", response_model=QuoteOut,
)
def set_quote_terms(
    license_id: uuid.UUID,
    quote_id: uuid.UUID,
    payload: QuoteTermsIn,
    session: Session = Depends(get_session),
    x_actor_id: str = Header(default=""),
):
    """The expiry date and the discount.

    Written since migration 0020 and unreachable until now: the
    repository method existed with no endpoint above it, so a quote's
    validity could be defaulted but never changed, and a discount could
    be stored but never set.
    """
    scope = TenantScope(license_id=license_id)
    try:
        row = QuoteRepository(session).set_terms(
            scope, quote_id, **payload.model_dump(exclude_unset=True),
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="quote", entity_id=quote_id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({}, payload.model_dump(exclude_unset=True)),
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        session.rollback()
        raise _phase10_http_error(exc)



# ==================================================================== Phase 15
# Live chat — Master Spec 15. Tenant-scoped reads and writes, plus the
# platform sweep that is the clock ticking over every shop at once.

def _chat_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatSessionNotFound):
        return HTTPException(status_code=404, detail={"error": "chat_session_not_found"})
    if isinstance(exc, ChatSessionConflict):
        return HTTPException(status_code=409, detail={"error": "chat_session_conflict", "message": str(exc)})
    if isinstance(exc, HTTPException):
        return exc
    log.exception("chat session operation failed")
    return HTTPException(status_code=500, detail={"error": "chat_session_failed"})


def _chat_sessions_out(session: Session, scope: TenantScope, rows: list) -> list[ChatSessionOut]:
    repo = ChatSessionRepository(session)
    summaries = repo.summaries(scope, [r.id for r in rows])
    uids = sorted({r.customer_chann_uid for r in rows})
    names: dict[str, str | None] = {}
    if uids:
        for identity in session.execute(
            select(ChannIdentity).where(ChannIdentity.chann_uid.in_(uids))
        ).scalars():
            names[identity.chann_uid] = identity.display_name
    out = []
    for r in rows:
        summary = summaries.get(r.id, {})
        out.append(ChatSessionOut(
            id=r.id, license_id=r.license_id, customer_chann_uid=r.customer_chann_uid,
            customer_name=names.get(r.customer_chann_uid), status=r.status,
            assigned_to=r.assigned_to, product_id=r.product_id, sla_deadline=r.sla_deadline,
            timeout_at=r.timeout_at, escalated_at=r.escalated_at, closed_at=r.closed_at,
            created_at=r.created_at, updated_at=r.updated_at,
            last_message=summary.get("last_message"),
            last_sender_type=summary.get("last_sender_type"),
            last_message_at=summary.get("last_message_at"),
            unread_from_customer=int(summary.get("unread_from_customer") or 0),
        ))
    return out


@router.post("/licenses/{license_id}/chat-sessions", response_model=ChatSessionOut)
def open_chat_session(
    license_id: uuid.UUID, payload: ChatSessionOpenIn, response: Response,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """The customer's live conversation with this shop: 201 when it was
    just opened, 200 when they were already in one."""
    scope = TenantScope(license_id=license_id)
    try:
        row, created = ChatSessionRepository(session).open_session(
            scope, customer_chann_uid=payload.customer_chann_uid, product_id=payload.product_id,
            sla_minutes=payload.sla_minutes, timeout_minutes=payload.timeout_minutes,
        )
        if created:
            AuditRepository(session).write(
                license_id=license_id, entity_type="chat_session", entity_id=row.id,
                actor_type="user", actor_id=x_actor_id or payload.customer_chann_uid,
                action="create", field_changes=diff_fields({}, {"status": row.status}),
            )
        session.commit()
        session.refresh(row)
        response.status_code = 201 if created else 200
        return _chat_sessions_out(session, scope, [row])[0]
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)


@router.get("/licenses/{license_id}/chat-sessions", response_model=list[ChatSessionOut])
def list_chat_sessions(
    license_id: uuid.UUID, status: str | None = None, customer_chann_uid: str | None = None,
    limit: int = 100, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    rows = ChatSessionRepository(session).list_for_license(
        scope, status=status, customer_chann_uid=customer_chann_uid, limit=limit,
    )
    return _chat_sessions_out(session, scope, rows)


@router.get("/licenses/{license_id}/chat-sessions/{session_id}", response_model=ChatSessionOut)
def get_chat_session(
    license_id: uuid.UUID, session_id: uuid.UUID, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ChatSessionRepository(session).require(scope, session_id)
    except Exception as exc:
        raise _chat_http_error(exc)
    return _chat_sessions_out(session, scope, [row])[0]


@router.get(
    "/licenses/{license_id}/chat-sessions/{session_id}/messages",
    response_model=list[ChatMessageOut],
)
def list_chat_messages(
    license_id: uuid.UUID, session_id: uuid.UUID, since: datetime | None = None,
    limit: int = 200, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        rows = ChatSessionRepository(session).list_messages(scope, session_id, since=since, limit=limit)
    except Exception as exc:
        raise _chat_http_error(exc)
    return [ChatMessageOut.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/licenses/{license_id}/chat-sessions/{session_id}/messages",
    response_model=ChatMessageOut, status_code=201,
)
def add_chat_message(
    license_id: uuid.UUID, session_id: uuid.UUID, payload: ChatMessageIn,
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        row = ChatSessionRepository(session).add_message(
            scope, session_id, sender_type=payload.sender_type, content=payload.content,
            sender_chann_uid=payload.sender_chann_uid, content_en=payload.content_en,
            sla_minutes=payload.sla_minutes, timeout_minutes=payload.timeout_minutes,
        )
        session.commit()
        session.refresh(row)
        return ChatMessageOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/assign", response_model=ChatSessionOut)
def assign_chat_session(
    license_id: uuid.UUID, session_id: uuid.UUID, payload: ChatSessionAssignIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = ChatSessionRepository(session)
        before = repo.require(scope, session_id)
        previous = {"status": before.status, "assigned_to": str(before.assigned_to or "")}
        row = repo.assign(scope, session_id, payload.member_id)
        AuditRepository(session).write(
            license_id=license_id, entity_type="chat_session", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields(previous, {"status": row.status, "assigned_to": str(row.assigned_to)}),
        )
        session.commit()
        session.refresh(row)
        return _chat_sessions_out(session, scope, [row])[0]
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/close", response_model=ChatSessionOut)
def close_chat_session(
    license_id: uuid.UUID, session_id: uuid.UUID, status: str = "closed",
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """`status` = closed (someone ended it) | unanswered (parked by the
    SLA sweep: the shop did not answer in time) | timeout (quiet)."""
    if status not in ("closed", "unanswered", "timeout"):
        raise HTTPException(status_code=422, detail={"error": "bad_status"})
    scope = TenantScope(license_id=license_id)
    try:
        repo = ChatSessionRepository(session)
        before = repo.require(scope, session_id)
        previous = {"status": before.status}
        row = repo.close(scope, session_id, status=status)
        if previous["status"] != row.status:
            AuditRepository(session).write(
                license_id=license_id, entity_type="chat_session", entity_id=row.id,
                actor_type="user", actor_id=x_actor_id or None, action="update",
                field_changes=diff_fields(previous, {"status": row.status}),
            )
        session.commit()
        session.refresh(row)
        return _chat_sessions_out(session, scope, [row])[0]
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)


@router.post("/licenses/{license_id}/chat-sessions/{session_id}/read")
def mark_chat_read(
    license_id: uuid.UUID, session_id: uuid.UUID, reader: str = "agent",
    session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        count = ChatSessionRepository(session).mark_read(scope, session_id, reader=reader)
        session.commit()
        return {"marked": count}
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)


@router.post("/platform/chat-sessions/sweep", response_model=ChatSweepOut)
def sweep_chat_sessions(session: Session = Depends(get_session)):
    """Cross-tenant by nature: the platform's clock. Returns what changed
    so the Application tier can tell the right people; the rows are
    marked here so the next run does not repeat them."""
    repo = ChatSessionRepository(session)
    try:
        overdue = repo.sla_overdue()
        for row in overdue:
            repo.mark_escalated(row)
        timed_out = repo.time_out()
        session.commit()
        escalated_out = []
        for row in overdue:
            session.refresh(row)
            escalated_out.extend(_chat_sessions_out(session, TenantScope(license_id=row.license_id), [row]))
        timed_out_out = []
        for row in timed_out:
            session.refresh(row)
            timed_out_out.extend(_chat_sessions_out(session, TenantScope(license_id=row.license_id), [row]))
        return ChatSweepOut(escalated=escalated_out, timed_out=timed_out_out)
    except Exception as exc:
        session.rollback()
        raise _chat_http_error(exc)



# ==================================================================== Phase 16.5
# PDPA — consent on the identity, requests, and the two cross-tenant walks.

def _pdpa_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PdpaNotFound):
        return HTTPException(status_code=404, detail={"error": "pdpa_not_found"})
    if isinstance(exc, PdpaConflict):
        return HTTPException(status_code=409, detail={"error": "pdpa_conflict", "message": str(exc)})
    if isinstance(exc, HTTPException):
        return exc
    logging.getLogger(__name__).exception("pdpa operation failed")
    return HTTPException(status_code=500, detail={"error": "pdpa_failed"})


@router.get("/identities/{chann_uid}/consent", response_model=ConsentOut)
def get_consent(chann_uid: str, session: Session = Depends(get_session)):
    try:
        return ConsentOut(**PdpaRepository(session).consent_of(chann_uid))
    except Exception as exc:
        raise _pdpa_http_error(exc)


@router.put("/identities/{chann_uid}/consent", response_model=ConsentOut)
def put_consent(chann_uid: str, payload: ConsentIn, session: Session = Depends(get_session)):
    try:
        repo = PdpaRepository(session)
        row = repo.record_consent(chann_uid, version=payload.version)
        session.commit()
        cache.invalidate(k_identity(row.line_user_id))
        return ConsentOut(**repo.consent_of(chann_uid))
    except Exception as exc:
        session.rollback()
        raise _pdpa_http_error(exc)


@router.post("/platform/pdpa/requests", response_model=DataSubjectRequestOut, status_code=201)
def create_pdpa_request(payload: DataSubjectRequestIn, session: Session = Depends(get_session)):
    try:
        row = PdpaRepository(session).create_request(
            chann_uid=payload.chann_uid, request_type=payload.request_type, requested_via=payload.requested_via,
        )
        session.commit()
        session.refresh(row)
        return DataSubjectRequestOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _pdpa_http_error(exc)


@router.get("/platform/pdpa/requests", response_model=list[DataSubjectRequestOut])
def list_pdpa_requests(
    status: str | None = None, chann_uid: str | None = None, limit: int = 200,
    session: Session = Depends(get_session),
):
    rows = PdpaRepository(session).list_requests(status=status, chann_uid=chann_uid, limit=limit)
    return [DataSubjectRequestOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/platform/pdpa/requests/{request_id}", response_model=DataSubjectRequestOut)
def get_pdpa_request(request_id: uuid.UUID, session: Session = Depends(get_session)):
    try:
        return DataSubjectRequestOut.model_validate(PdpaRepository(session).get_request(request_id), from_attributes=True)
    except Exception as exc:
        raise _pdpa_http_error(exc)


@router.post("/platform/pdpa/requests/{request_id}/process")
def process_pdpa_request(
    request_id: uuid.UUID, payload: DataSubjectProcessIn, session: Session = Depends(get_session),
):
    """Run the request: erasure anonymises across tenants and returns the
    storage paths to delete; export returns the bundle. Both audit every
    tenant touched with cross_tenant=true."""
    try:
        repo = PdpaRepository(session)
        request = repo.get_request(request_id)
        if request.request_type == "erasure":
            identity = repo.identity(request.chann_uid)
            line_user_id = identity.line_user_id
            result = repo.erase(request_id, processed_by=payload.processed_by)
            session.commit()
            cache.invalidate(k_identity(line_user_id))
            return {"request_type": "erasure", **result}
        if request.request_type == "export":
            bundle = repo.export(request_id, processed_by=payload.processed_by)
            session.commit()
            return {"request_type": "export", "bundle": bundle}
        raise PdpaConflict("consent withdrawal is recorded, not processed")
    except Exception as exc:
        session.rollback()
        raise _pdpa_http_error(exc)


@router.post("/platform/pdpa/requests/{request_id}/reject", response_model=DataSubjectRequestOut)
def reject_pdpa_request(
    request_id: uuid.UUID, payload: DataSubjectRejectIn, session: Session = Depends(get_session),
):
    try:
        row = PdpaRepository(session).reject(request_id, reason=payload.reason, processed_by=payload.processed_by)
        session.commit()
        session.refresh(row)
        return DataSubjectRequestOut.model_validate(row, from_attributes=True)
    except Exception as exc:
        session.rollback()
        raise _pdpa_http_error(exc)



# ======================================================================= Phase 18
# Platform admin reads: every tenant with its size, one tenant in detail,
# and the audit trail across tenants. Writes reuse PATCH /licenses/{id}/status
# and the break-glass route above.

@router.get("/platform/tenants", response_model=list[TenantSummaryOut])
def platform_tenants(
    q: str | None = None, status: str | None = None, limit: int = 200,
    session: Session = Depends(get_session),
):
    return [TenantSummaryOut(**row) for row in PlatformRepository(session).tenants(q=q, status=status, limit=limit)]


@router.get("/platform/tenants/{license_id}")
def platform_tenant(license_id: uuid.UUID, session: Session = Depends(get_session)):
    """The summary plus the legal details and every member (with the
    person's display name, which only the platform may see side by side
    with the tenant — it never leaves the admin dashboard)."""
    try:
        data = PlatformRepository(session).tenant(license_id)
    except PlatformNotFound:
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})
    members = data.pop("members")
    payload = TenantSummaryOut(**{k: v for k, v in data.items() if k in TenantSummaryOut.model_fields}).model_dump()
    payload.update({
        "legal_name": data.get("legal_name"), "company_phone": data.get("company_phone"),
        "company_email": data.get("company_email"),
        "members_detail": [TenantMemberOut(**m).model_dump() for m in members],
    })
    return payload


@router.get("/platform/audit", response_model=list[AuditLogOut])
def platform_audit(
    cross_tenant: bool | None = None, license_id: uuid.UUID | None = None,
    actor_type: str | None = None, action: str | None = None, limit: int = 100,
    session: Session = Depends(get_session),
):
    rows = AuditRepository(session).list_platform(
        cross_tenant=cross_tenant, license_id=license_id, actor_type=actor_type, action=action, limit=limit,
    )
    return [AuditLogOut.model_validate(r, from_attributes=True) for r in rows]



# ======================================================================= Phase 17
# Ad-hoc reports: the Application tier sends a whitelisted spec, never SQL.

@router.post("/licenses/{license_id}/reports/query", response_model=ReportResultOut)
def run_report_query(
    license_id: uuid.UUID, payload: ReportQueryIn, session: Session = Depends(get_session),
):
    scope = TenantScope(license_id=license_id)
    try:
        result = ReportQueryRepository(session).run(scope, payload.model_dump())
    except ReportSpecInvalid as exc:
        raise HTTPException(status_code=422, detail={"error": "report_spec_invalid", "message": str(exc)})
    return ReportResultOut(**result)



# ------------------------------------------------ user review fixes (4 Sep 2026)

@router.post("/licenses/{license_id}/customers/archive-inactive-leads", response_model=list[CustomerOut])
def archive_inactive_leads(
    license_id: uuid.UUID, payload: ArchiveInactiveLeadsIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """The daily lead cleanup for one tenant (setting lead_auto_archive_days).
    Soft delete only; one audit row per lead so the trail says why it left."""
    scope = TenantScope(license_id=license_id)
    try:
        rows = CustomerRepository(session).archive_inactive_leads(scope, days=payload.days)
        for row in rows:
            AuditRepository(session).write(
                license_id=license_id, entity_type="customer", entity_id=row.id,
                actor_type="system", actor_id=x_actor_id or "lead_cleanup", action="update",
                field_changes={**diff_fields({"archived": False}, {"archived": True}),
                               "reason": f"inactive lead > {payload.days} days"},
            )
        session.commit()
        return [CustomerOut.model_validate(r, from_attributes=True) for r in rows]
    except Exception as exc:
        session.rollback()
        raise _phase9_http_error(exc)
