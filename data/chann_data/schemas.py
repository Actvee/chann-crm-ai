from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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
    # Named schema_state, not schema: "schema" shadows a BaseModel attribute
    # and Pydantic warns about it. Optional so an older caller parsing this
    # response does not break.
    schema_state: str | None = None
    migration_head: str | None = None
    expected_migration_head: str | None = None


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


# ---------------------------------------------------------------- Phase 6.5


class LicenseCreateIn(BaseModel):
    company_name: str
    created_by_chann_uid: str
    display_name: str | None = None


class LicenseOut(BaseModel):
    id: uuid.UUID
    license_code: str
    company_name: str
    company_code: str | None
    status: str
    trial_expires_at: datetime | None
    created_by_chann_uid: str | None


class CompanyProfileOut(BaseModel):
    """Phase 10 — the issuing company's identity as it appears on a
    customer-facing document.

    Deliberately separate from LicenseOut: this is the tenant's own legal
    identity for documents, not the operational license record, and the two
    are read in completely different situations. Keeping them apart also
    means adding a document field later never widens what LicenseOut leaks
    into every caller that only wanted a status.
    """

    legal_name: str | None
    company_name: str
    tax_id: str | None
    company_address: str | None
    company_phone: str | None
    company_email: str | None
    # Fraction, e.g. 0.07 for 7%. None = not VAT-registered (no VAT line at
    # all on the document), which is not the same as 0.
    vat_rate: Decimal | None
    # True when everything a Thai tax document legally requires is present.
    # Computed here rather than left to each caller so "can we render?" has
    # exactly one answer in the codebase.
    is_document_ready: bool
    missing_for_documents: list[str]


class CompanyProfileIn(BaseModel):
    """All fields optional — this is a partial update. Sending a field
    explicitly as null clears it; omitting it leaves it unchanged. That
    distinction matters for vat_rate, where null ("we deregistered from
    VAT") is a real, intentional value rather than an absence."""

    legal_name: str | None = None
    tax_id: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    company_email: str | None = None
    vat_rate: Decimal | None = None


class ShopOut(BaseModel):
    """Public projection — company_name and company_code ONLY.

    Deliberately narrow: this is served to callers with no tenant at all, so
    anything else here (id, member counts, status) would leak the shape of
    someone else's business to a stranger.
    """

    company_name: str
    company_code: str


class InviteCreateIn(BaseModel):
    role: str
    max_uses: int = 1
    expires_in_days: int | None = 7
    created_by_member_id: uuid.UUID | None = None


class InviteOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    invite_code: str
    role: str
    max_uses: int
    used_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InviteRedeemIn(BaseModel):
    invite_code: str
    chann_uid: str
    display_name: str | None = None


class CustomerLinkIn(BaseModel):
    chann_uid: str
    company_code: str


class CustomerLinkOut(BaseModel):
    id: uuid.UUID
    chann_uid: str
    license_id: uuid.UUID
    linked_at: datetime


class LicenseStatusIn(BaseModel):
    status: Literal["trial", "active", "suspended"]


# ---------------------------------------------------------------- Phase 7


class ProductIn(BaseModel):
    product_id: str
    product_name: str
    sku: str | None = None
    category: str | None = None
    # str so "25,000" from a spreadsheet survives to the Decimal parser;
    # float would reintroduce the rounding this column exists to avoid.
    unit_price: str | float | int | None = None
    description: str | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    product_id: str
    product_name: str
    sku: str | None
    category: str | None
    unit_price: Decimal | None
    description: str | None
    archived_at: datetime | None
    created_at: datetime


class ProductCsvIn(BaseModel):
    content: str


class ProductCsvOut(BaseModel):
    imported: int
    errors: list[dict]


class GroupIn(BaseModel):
    group_name: str


class GroupOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    group_name: str
    created_at: datetime


class TeamIn(BaseModel):
    team_name: str


class TeamOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    team_name: str
    created_at: datetime


class GroupMemberIn(BaseModel):
    member_id: uuid.UUID


class TeamMemberIn(BaseModel):
    member_id: uuid.UUID
    is_lead: bool = False


class TeamMemberOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    member_id: uuid.UUID
    is_lead: bool
    created_at: datetime


# ---------------------------------------------------------------- Phase 8


class ProfileUpdateIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None


class ProfileOut(BaseModel):
    chann_uid: str
    first_name: str | None
    last_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    registered: bool
    registered_at: datetime | None


class ProfileEditCheckOut(BaseModel):
    allowed: bool


# ---------------------------------------------------------------- Chat state


class PendingIntentIn(BaseModel):
    action: str
    entity: str | None = None
    fields: dict = {}
    missing: list[str] = []
    ttl_seconds: int = 600


class PendingIntentOut(BaseModel):
    action: str
    entity: str | None
    fields: dict
    missing: list[str]


class LastCustomerRefIn(BaseModel):
    customer_id: str
    name: str
    ttl_seconds: int = 600


class LastCustomerRefOut(BaseModel):
    customer_id: str
    name: str


# ---------------------------------------------------------------- Phase 9 CRM


class CustomerIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    customer_chann_uid: str | None = None
    owner_member_id: uuid.UUID | None = None


class CustomerOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    customer_chann_uid: str | None
    stage: str
    owner_member_id: uuid.UUID | None
    first_name: str | None
    last_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DealProductIn(BaseModel):
    product_id: uuid.UUID | None = None
    product_name: str
    quoted_unit_price: str | float | int
    qty: int = 1
    notes: str | None = None


class DealProductOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    product_id: uuid.UUID | None
    product_name: str
    quoted_unit_price: Decimal
    qty: int
    notes: str | None
    created_at: datetime


class DealIn(BaseModel):
    contact_id: uuid.UUID
    notes: str | None = None
    owner_member_id: uuid.UUID | None = None
    products: list[DealProductIn] = []


class DealOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    deal_id: str
    contact_id: uuid.UUID
    stage: str
    owner_member_id: uuid.UUID | None
    notes: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    products: list[DealProductOut] = []


class DealStageIn(BaseModel):
    stage: str


class StorefrontProductOut(BaseModel):
    """Deliberately narrower than ProductOut — 9.4 requires storefront search
    to never reveal which shop a product belongs to until the customer picks
    one, only the product info itself."""
    product_id: str
    product_name: str
    sku: str | None
    category: str | None
    unit_price: Decimal | None
    license_id: uuid.UUID
    company_name: str


class StorefrontInterestIn(BaseModel):
    chann_uid: str
    license_id: uuid.UUID
    product_name: str


# ---------------------------------------------------------------- Phase 10


class QuoteIn(BaseModel):
    deal_id: uuid.UUID
    owner_member_id: uuid.UUID | None = None


class QuoteOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    quote_id: str
    deal_id: uuid.UUID
    status: str
    generated_document_id: uuid.UUID | None
    owner_member_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class QuoteStatusIn(BaseModel):
    status: str


class DocumentTemplateIn(BaseModel):
    document_type: str
    template_code: str
    template_name: str


class DocumentTemplateOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    document_type: str
    template_code: str
    template_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DocumentTemplateVersionIn(BaseModel):
    source_docx_path: str
    intermediate_model: dict
    mapping_schema: dict
    compiled_template_path: str
    renderer: str = "smartbrowz"
    renderer_mode: str = "html_convert"
    smartbrowz_template_id: str | None = None
    created_by: uuid.UUID | None = None


class DocumentTemplateVersionOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    version: int
    status: str
    source_docx_path: str
    intermediate_model: dict
    mapping_schema: dict
    compiled_template_path: str
    renderer: str
    renderer_mode: str
    smartbrowz_template_id: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    published_at: datetime | None


class GeneratedDocumentIn(BaseModel):
    document_type: str
    source_entity_type: str
    source_entity_id: uuid.UUID
    template_version_id: uuid.UUID
    data_snapshot: dict
    output_path: str
    sha256: str
    renderer: str = "smartbrowz"
    generated_by: uuid.UUID | None = None


class GeneratedDocumentOut(BaseModel):
    id: uuid.UUID
    license_id: uuid.UUID
    document_type: str
    source_entity_type: str
    source_entity_id: uuid.UUID
    template_version_id: uuid.UUID
    data_snapshot: dict
    output_path: str
    sha256: str
    renderer: str
    generated_by: uuid.UUID | None
    generated_at: datetime


class SmartBrowzTokenIn(BaseModel):
    access_token: str
    api_domain: str | None = None
    ttl_seconds: int = 3300  # slightly under Zoho's real 3600s expiry


class SmartBrowzTokenOut(BaseModel):
    access_token: str
    api_domain: str | None
