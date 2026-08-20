from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class IdentityOut(BaseModel):
    chann_uid: str
    line_user_id: str
    primary_role: str
    display_name: str | None = None


class IdentityResolveIn(BaseModel):
    line_user_id: str
    primary_role: Literal["customer", "sales", "technician"]
    display_name: str | None = None


class MembershipOut(BaseModel):
    license_id: uuid.UUID
    license_code: str
    company_name: str
    chann_uid: str
    role: str
    status: str


class MemberOut(BaseModel):
    chann_uid: str
    role: str
    status: str


class PlatformAdminAuthIn(BaseModel):
    username: str
    password: str


class PlatformAdminAuthOut(BaseModel):
    admin_id: uuid.UUID
    username: str


class PlatformAdminSessionIn(BaseModel):
    session_id: str
    admin_id: uuid.UUID
    ttl_s: int


class PlatformAdminSessionOut(BaseModel):
    session_id: str
    admin_id: uuid.UUID


class HealthOut(BaseModel):
    status: str
    tier: str
    app_env: str
    platform_version: str
    git_commit: str
    database: str
    cache: str


class AuthorizationContextOut(BaseModel):
    member_id: uuid.UUID
    chann_uid: str
    role: str
    is_owner: bool
    permission_keys: list[str]


class RoleWriteIn(BaseModel):
    role_name: str
    permission_keys: list[str]


class RoleOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    role_name: str
    is_owner: bool
    permission_keys: list[str]


class MemberRoleIn(BaseModel):
    role_name: str


class LicenseSettingWriteIn(BaseModel):
    setting_value: dict | list | str | int | float | bool | None


class LicenseSettingOut(BaseModel):
    setting_key: str
    setting_value: dict | list | str | int | float | bool | None


class OwnershipTransferRequestIn(BaseModel):
    from_chann_uid: str
    to_chann_uid: str


class OwnershipTransferAcceptIn(BaseModel):
    accepting_chann_uid: str


class OwnershipTransferOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    from_member_id: uuid.UUID
    to_member_id: uuid.UUID
    status: str
    accepted_at: datetime | None = None


class BreakGlassTransferIn(BaseModel):
    target_chann_uid: str


class AuditLogOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID
    actor_type: str
    actor_id: str | None
    action: str
    field_changes: dict | None
    ai_reasoning: str | None
    cross_tenant: bool
    created_at: datetime


class AuditLogWriteIn(BaseModel):
    license_id: uuid.UUID | None = None
    entity_type: str
    entity_id: uuid.UUID
    actor_type: str
    action: str
    actor_id: str | None = None
    field_changes: dict | None = None
    ai_reasoning: str | None = None
    cross_tenant: bool = False


# ---------------------------------------------------------------- Phase 6


class MessageEntityMapIn(BaseModel):
    message_id: str
    entity_type: str
    entity_id: uuid.UUID


class MessageEntityMapOut(BaseModel):
    id: uuid.UUID
    message_id: str
    entity_type: str
    entity_id: uuid.UUID
    license_id: uuid.UUID
    created_at: datetime


class NotificationIn(BaseModel):
    target_chann_uid: str
    type: str
    message: str
    message_en: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    delivery_line: bool = True
    delivery_dashboard: bool = True


class NotificationOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID | None
    target_chann_uid: str
    type: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    message: str
    message_en: str | None
    delivery_line: bool
    delivery_dashboard: bool
    read_at: datetime | None
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread_count: int


class FollowUpIn(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    due_date: date
    owner_member_id: uuid.UUID | None = None
    notes: str | None = None


class FollowUpStatusIn(BaseModel):
    status: Literal["pending", "completed", "cancelled"]


class FollowUpOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    due_date: date
    status: str
    owner_member_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
