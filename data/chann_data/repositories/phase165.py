"""Phase 16.5 — PDPA data rights: consent, erasure (anonymise, never
delete), export — across every tenant the same Chann Identity touched.

Master Spec 16.5.4/16.5.5. The two cross-tenant walks here are the
deliberate exceptions to tenant scoping: a person's right to be
forgotten, or to see their data, is a right against the platform, not
against one shop. Every tenant touched gets its own audit row with
cross_tenant=true.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ChannIdentity, ChatMessage, ChatSession, Customer, CustomerLicenseLink, DataSubjectRequest,
    Deal, License, LicenseMember, ServiceTicket, TicketPhoto, Warranty,
)
from .audit import AuditRepository

REQUEST_TYPES = ("erasure", "export", "consent_withdraw")
STATUSES = ("pending", "processing", "completed", "rejected")

ANON_NAME = "ผู้ใช้ที่ลบข้อมูลแล้ว"
ANON_CUSTOMER = "ลูกค้า (ลบข้อมูลแล้ว)"
ANON_TEXT = "(ลบข้อมูลแล้ว)"
ANON_PHOTO = "anonymized"


class PdpaNotFound(Exception):
    pass


class PdpaConflict(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (value.isoformat() if hasattr(value, "isoformat") else value)


class PdpaRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ consent

    def identity(self, chann_uid: str) -> ChannIdentity:
        row = self._s.get(ChannIdentity, chann_uid)
        if row is None:
            raise PdpaNotFound("identity not found")
        return row

    def record_consent(self, chann_uid: str, *, version: str) -> ChannIdentity:
        row = self.identity(chann_uid)
        row.consent_accepted_at = _now()
        row.consent_version = version
        self._s.flush()
        return row

    def consent_of(self, chann_uid: str) -> dict:
        row = self.identity(chann_uid)
        return {
            "chann_uid": row.chann_uid,
            "consent_accepted_at": row.consent_accepted_at,
            "consent_version": row.consent_version,
            "anonymized_at": row.anonymized_at,
        }

    # ------------------------------------------------------------ requests

    def create_request(self, *, chann_uid: str, request_type: str, requested_via: str) -> DataSubjectRequest:
        if request_type not in REQUEST_TYPES:
            raise PdpaConflict(f"unknown request type: {request_type!r}")
        self.identity(chann_uid)
        row = DataSubjectRequest(chann_uid=chann_uid, request_type=request_type, requested_via=requested_via)
        self._s.add(row)
        self._s.flush()
        return row

    def get_request(self, request_id: uuid.UUID) -> DataSubjectRequest:
        row = self._s.get(DataSubjectRequest, request_id)
        if row is None:
            raise PdpaNotFound("request not found")
        return row

    def list_requests(self, *, status: str | None = None, chann_uid: str | None = None, limit: int = 200) -> list[DataSubjectRequest]:
        query = select(DataSubjectRequest)
        if status:
            query = query.where(DataSubjectRequest.status == status)
        if chann_uid:
            query = query.where(DataSubjectRequest.chann_uid == chann_uid)
        query = query.order_by(DataSubjectRequest.requested_at.desc()).limit(max(1, min(limit, 500)))
        return list(self._s.execute(query).scalars())

    def reject(self, request_id: uuid.UUID, *, reason: str, processed_by: uuid.UUID | None) -> DataSubjectRequest:
        row = self.get_request(request_id)
        if row.status == "completed":
            raise PdpaConflict("request already completed")
        row.status = "rejected"
        row.rejection_reason = (reason or "")[:512]
        row.processed_by = processed_by
        row.completed_at = _now()
        self._s.flush()
        return row

    # ------------------------------------------------------------ tenants of a person

    def tenants_of(self, chann_uid: str) -> list[License]:
        """Every shop this identity is a customer of or a member of."""
        ids: set[uuid.UUID] = set()
        for link in self._s.execute(
            select(CustomerLicenseLink).where(CustomerLicenseLink.chann_uid == chann_uid)
        ).scalars():
            ids.add(link.license_id)
        for member in self._s.execute(
            select(LicenseMember).where(LicenseMember.chann_uid == chann_uid)
        ).scalars():
            ids.add(member.license_id)
        for customer in self._s.execute(
            select(Customer).where(Customer.customer_chann_uid == chann_uid)
        ).scalars():
            ids.add(customer.license_id)
        if not ids:
            return []
        return list(self._s.execute(select(License).where(License.id.in_(ids))).scalars())

    # ------------------------------------------------------------ erasure

    def erase(self, request_id: uuid.UUID, *, processed_by: uuid.UUID | None = None) -> dict:
        """Anonymise everything that names this person, in every tenant.
        Rows stay (FKs from deals, tickets, audit rows still hold); the
        words are replaced. Returns what was touched and the storage
        paths the Application tier must delete (GCS is not this tier's)."""
        request = self.get_request(request_id)
        if request.request_type != "erasure":
            raise PdpaConflict("not an erasure request")
        if request.status == "completed":
            raise PdpaConflict("request already completed")
        chann_uid = request.chann_uid
        identity = self.identity(chann_uid)
        request.status = "processing"
        self._s.flush()

        paths: list[str] = []
        touched = {"tenants": 0, "customers": 0, "tickets": 0, "photos": 0, "chat_messages": 0}
        for license_row in self.tenants_of(chann_uid):
            touched["tenants"] += 1
            counts = {"customers": 0, "tickets": 0, "photos": 0, "chat_messages": 0}
            for customer in self._s.execute(
                select(Customer).where(Customer.license_id == license_row.id, Customer.customer_chann_uid == chann_uid)
            ).scalars():
                customer.first_name = ANON_CUSTOMER
                customer.last_name = None
                customer.phone = None
                customer.email = None
                customer.address = None
                customer.notes = None
                counts["customers"] += 1
            tickets = list(self._s.execute(
                select(ServiceTicket).where(
                    ServiceTicket.license_id == license_row.id, ServiceTicket.customer_chann_uid == chann_uid,
                )
            ).scalars())
            for ticket in tickets:
                ticket.customer_name = ANON_CUSTOMER
                ticket.customer_phone = None
                if getattr(ticket, "service_address", None):
                    ticket.service_address = ANON_TEXT
                counts["tickets"] += 1
                for photo in self._s.execute(
                    select(TicketPhoto).where(TicketPhoto.ticket_id == ticket.id)
                ).scalars():
                    if photo.photo_url and photo.photo_url != ANON_PHOTO:
                        paths.append(photo.photo_url)
                    photo.photo_url = ANON_PHOTO
                    photo.gps_lat = None
                    photo.gps_lng = None
                    counts["photos"] += 1
            for message in self._s.execute(
                select(ChatMessage).where(
                    ChatMessage.license_id == license_row.id, ChatMessage.sender_chann_uid == chann_uid,
                )
            ).scalars():
                message.content = ANON_TEXT
                message.content_en = None
                counts["chat_messages"] += 1
            for key, value in counts.items():
                touched[key] += value
            AuditRepository(self._s).write(
                license_id=license_row.id, entity_type="data_subject_request", entity_id=request.id,
                actor_type="system" if processed_by is None else "user",
                actor_id=str(processed_by) if processed_by else chann_uid,
                action="pdpa_erasure", field_changes={"chann_uid": chann_uid, **counts},
                cross_tenant=True,
            )

        if identity.signature_url:
            paths.append(identity.signature_url)
        identity.display_name = ANON_NAME
        identity.first_name = None
        identity.last_name = None
        identity.phone = None
        identity.email = None
        identity.address = None
        identity.signature_url = None
        identity.anonymized_at = _now()
        # Spec 16.5.4: the LINE id stays (dedup), but the person is a new
        # identity to the platform from here — consent is asked again.
        identity.consent_accepted_at = None
        identity.consent_version = None

        request.status = "completed"
        request.completed_at = _now()
        request.processed_by = processed_by
        request.result_json = {**touched, "storage_paths": len(paths)}
        self._s.flush()
        return {**touched, "storage_paths": paths, "request_id": str(request.id)}

    # ------------------------------------------------------------ export

    def export(self, request_id: uuid.UUID, *, processed_by: uuid.UUID | None = None) -> dict:
        """Everything the platform holds about this person, per tenant.
        Only their own rows: a customer's tickets are theirs, the shop's
        other customers are not."""
        request = self.get_request(request_id)
        if request.request_type != "export":
            raise PdpaConflict("not an export request")
        chann_uid = request.chann_uid
        identity = self.identity(chann_uid)
        bundle = {
            "request_id": str(request.id),
            "exported_at": _now().isoformat(),
            "identity": {
                "chann_uid": identity.chann_uid, "display_name": identity.display_name,
                "first_name": identity.first_name, "last_name": identity.last_name,
                "phone": identity.phone, "email": identity.email, "address": identity.address,
                "primary_role": identity.primary_role,
                "consent_accepted_at": _iso(identity.consent_accepted_at),
                "consent_version": identity.consent_version,
            },
            "companies": [],
        }
        for license_row in self.tenants_of(chann_uid):
            company: dict = {
                "license_id": str(license_row.id), "company_name": license_row.company_name,
                "roles": [], "customer": None, "tickets": [], "warranties": [], "deals": [], "chat_messages": [],
            }
            for member in self._s.execute(
                select(LicenseMember).where(LicenseMember.license_id == license_row.id, LicenseMember.chann_uid == chann_uid)
            ).scalars():
                company["roles"].append({"role": member.role, "status": member.status})
            customer = self._s.execute(
                select(Customer).where(Customer.license_id == license_row.id, Customer.customer_chann_uid == chann_uid)
            ).scalars().first()
            if customer is not None:
                company["customer"] = {
                    "customer_id": customer.customer_id, "first_name": customer.first_name,
                    "last_name": customer.last_name, "phone": customer.phone, "email": customer.email,
                    "address": customer.address, "stage": customer.stage,
                    "created_at": _iso(customer.created_at),
                }
                for deal in self._s.execute(select(Deal).where(Deal.contact_id == customer.id)).scalars():
                    company["deals"].append({"deal_id": deal.deal_id, "stage": deal.stage, "created_at": _iso(deal.created_at)})
            for ticket in self._s.execute(
                select(ServiceTicket).where(ServiceTicket.license_id == license_row.id, ServiceTicket.customer_chann_uid == chann_uid)
            ).scalars():
                company["tickets"].append({
                    "ticket_number": ticket.ticket_number, "status": ticket.status,
                    "issue_description": ticket.issue_description,
                    "service_address": getattr(ticket, "service_address", None),
                    "created_at": _iso(ticket.created_at),
                })
            for warranty in self._s.execute(
                select(Warranty).where(Warranty.license_id == license_row.id, Warranty.customer_chann_uid == chann_uid)
            ).scalars():
                company["warranties"].append({
                    "warranty_number": warranty.warranty_number, "serial_number": warranty.serial_number,
                    "product_name": warranty.product_name, "warranty_start": _iso(warranty.warranty_start),
                    "warranty_end": _iso(warranty.warranty_end), "status": warranty.status,
                })
            for message in self._s.execute(
                select(ChatMessage).where(ChatMessage.license_id == license_row.id, ChatMessage.sender_chann_uid == chann_uid)
                .order_by(ChatMessage.created_at)
            ).scalars():
                company["chat_messages"].append({"content": message.content, "created_at": _iso(message.created_at)})
            bundle["companies"].append(company)
            AuditRepository(self._s).write(
                license_id=license_row.id, entity_type="data_subject_request", entity_id=request.id,
                actor_type="system" if processed_by is None else "user",
                actor_id=str(processed_by) if processed_by else chann_uid,
                action="pdpa_export", field_changes={"chann_uid": chann_uid}, cross_tenant=True,
            )
        request.status = "completed"
        request.completed_at = _now()
        request.processed_by = processed_by
        request.result_json = {"companies": len(bundle["companies"])}
        self._s.flush()
        return bundle
