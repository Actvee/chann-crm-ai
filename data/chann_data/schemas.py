from __future__ import annotations

import uuid
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
