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

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog, ChannIdentity, ChatMessage, ChatSession, Customer, CustomerLicenseLink, DataSubjectRequest,
    Deal, FollowUp, GeneratedDocument, License, LicenseMember, Note, Quote, ServiceReport, ServiceTicket,
    TicketPhoto, Warranty,
)
from .audit import AuditRepository

REQUEST_TYPES = ("erasure", "export", "consent_withdraw")
STATUSES = ("pending", "processing", "completed", "rejected")

ANON_NAME = "ผู้ใช้ที่ลบข้อมูลแล้ว"
ANON_CUSTOMER = "ลูกค้า (ลบข้อมูลแล้ว)"
ANON_TEXT = "(ลบข้อมูลแล้ว)"
ANON_PHOTO = "anonymized"

# The keys a frozen document snapshot (quote: `customer`, service report:
# `ticket`) prints about the person. Cleared by name, then the whole
# snapshot is swept for any remaining occurrence of their values — a
# tenant's own template may have copied them anywhere.
_SNAPSHOT_PII_KEYS = frozenset({
    "name", "first_name", "last_name", "phone", "email", "address",
    "customer_name", "customer_phone", "service_address", "display_name",
})


def export_object_path(chann_uid: str, request_id) -> str:
    """Where the Application tier keeps one PDPA export page. Named by the
    request so erasure can hand back every export it produced without the
    Data tier being able to list a bucket (it cannot)."""
    return f"pdpa/{chann_uid}/{request_id}.html"


def _pii_values(*rows: dict) -> set[str]:
    """The person's own words: every non-trivial string in the given
    rows. Two characters or fewer are skipped — replacing "ก" everywhere
    would maul unrelated text."""
    values: set[str] = set()
    for row in rows:
        for value in (row or {}).values():
            text = str(value or "").strip()
            if len(text) >= 3:
                values.add(text)
    return values


def _scrub(value, pii: set[str], *, keys: frozenset[str] = frozenset()):
    """Return `value` with every string that contains one of the person's
    values (or sits under a PII key) replaced by ANON_TEXT. Structure is
    kept so a snapshot still renders — with blanks where the person was."""
    if isinstance(value, dict):
        return {
            k: (ANON_TEXT if (k in keys and isinstance(v, str) and v) else _scrub(v, pii, keys=keys))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v, pii, keys=keys) for v in value]
    if isinstance(value, str) and value and any(p in value for p in pii):
        return ANON_TEXT
    return value


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
        erased_licenses: list[str] = []
        touched = {
            "tenants": 0, "customers": 0, "tickets": 0, "photos": 0, "chat_messages": 0,
            "notes": 0, "follow_ups": 0, "deals": 0, "documents": 0, "audit_rows": 0,
        }
        # What the person's rows say about them, gathered BEFORE the rows
        # are blanked: it is what the frozen snapshots and audit rows are
        # swept for (review D10, 6 Sep 2026 — a name and phone lived on in
        # generated_documents.data_snapshot and audit_log.field_changes).
        pii = _pii_values({
            "display_name": identity.display_name, "first_name": identity.first_name,
            "last_name": identity.last_name, "phone": identity.phone, "email": identity.email,
            "address": identity.address,
            "full_name": " ".join(p for p in (identity.first_name, identity.last_name) if p),
        })
        for license_row in self.tenants_of(chann_uid):
            touched["tenants"] += 1
            counts = {
                "customers": 0, "tickets": 0, "photos": 0, "chat_messages": 0,
                "notes": 0, "follow_ups": 0, "deals": 0, "documents": 0, "audit_rows": 0,
            }
            entity_ids: set[uuid.UUID] = set()
            customer_ids: set[uuid.UUID] = set()
            for customer in self._s.execute(
                select(Customer).where(Customer.license_id == license_row.id, Customer.customer_chann_uid == chann_uid)
            ).scalars():
                pii |= _pii_values({
                    "first_name": customer.first_name, "last_name": customer.last_name,
                    "phone": customer.phone, "email": customer.email, "address": customer.address,
                    "full_name": " ".join(p for p in (customer.first_name, customer.last_name) if p),
                })
                customer.first_name = ANON_CUSTOMER
                customer.last_name = None
                customer.phone = None
                customer.email = None
                customer.address = None
                customer.notes = None
                customer_ids.add(customer.id)
                counts["customers"] += 1
            entity_ids |= customer_ids
            tickets = list(self._s.execute(
                select(ServiceTicket).where(
                    ServiceTicket.license_id == license_row.id, ServiceTicket.customer_chann_uid == chann_uid,
                )
            ).scalars())
            ticket_ids: set[uuid.UUID] = set()
            for ticket in tickets:
                pii |= _pii_values({
                    "customer_name": ticket.customer_name, "customer_phone": ticket.customer_phone,
                    "service_address": getattr(ticket, "service_address", None),
                })
                ticket.customer_name = ANON_CUSTOMER
                ticket.customer_phone = None
                if getattr(ticket, "service_address", None):
                    ticket.service_address = ANON_TEXT
                ticket_ids.add(ticket.id)
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
            entity_ids |= ticket_ids
            for message in self._s.execute(
                select(ChatMessage).where(
                    ChatMessage.license_id == license_row.id, ChatMessage.sender_chann_uid == chann_uid,
                )
            ).scalars():
                message.content = ANON_TEXT
                message.content_en = None
                counts["chat_messages"] += 1

            # What staff wrote ABOUT the person on their customer record.
            if customer_ids:
                for note in self._s.execute(
                    select(Note).where(
                        Note.license_id == license_row.id, Note.entity_type == "customer",
                        Note.entity_id.in_(customer_ids),
                    )
                ).scalars():
                    note.body = ANON_TEXT
                    counts["notes"] += 1
                for follow_up in self._s.execute(
                    select(FollowUp).where(
                        FollowUp.license_id == license_row.id, FollowUp.entity_type == "customer",
                        FollowUp.entity_id.in_(customer_ids),
                    )
                ).scalars():
                    if follow_up.notes:
                        follow_up.notes = ANON_TEXT
                    counts["follow_ups"] += 1
            deal_ids: set[uuid.UUID] = set()
            if customer_ids:
                for deal in self._s.execute(
                    select(Deal).where(Deal.license_id == license_row.id, Deal.contact_id.in_(customer_ids))
                ).scalars():
                    if deal.notes:
                        deal.notes = ANON_TEXT
                    deal_ids.add(deal.id)
                    counts["deals"] += 1
            entity_ids |= deal_ids

            # The frozen documents: a quote on one of their deals, a service
            # report on one of their jobs. The snapshot is what re-renders
            # the paper, so the person is blanked out of it; the PDF itself
            # is handed back for deletion (the object store is not this
            # tier's) and the row no longer points at it.
            source_ids: set[uuid.UUID] = set()
            if deal_ids:
                source_ids |= {
                    q.id for q in self._s.execute(
                        select(Quote).where(Quote.license_id == license_row.id, Quote.deal_id.in_(deal_ids))
                    ).scalars()
                }
            if ticket_ids:
                for report in self._s.execute(
                    select(ServiceReport).where(
                        ServiceReport.license_id == license_row.id, ServiceReport.ticket_id.in_(ticket_ids),
                    )
                ).scalars():
                    source_ids.add(report.id)
                    if report.pdf_path and report.pdf_path != ANON_PHOTO:
                        paths.append(report.pdf_path)
                        report.pdf_path = ANON_PHOTO
            entity_ids |= source_ids
            if source_ids:
                for document in self._s.execute(
                    select(GeneratedDocument).where(
                        GeneratedDocument.license_id == license_row.id,
                        GeneratedDocument.source_entity_id.in_(source_ids),
                    )
                ).scalars():
                    snapshot = dict(document.data_snapshot or {})
                    for block in ("customer", "ticket"):
                        if isinstance(snapshot.get(block), dict):
                            snapshot[block] = _scrub(snapshot[block], pii, keys=_SNAPSHOT_PII_KEYS)
                    document.data_snapshot = _scrub(snapshot, pii)
                    if document.output_path and document.output_path != ANON_PHOTO:
                        paths.append(document.output_path)
                    document.output_path = ANON_PHOTO
                    entity_ids.add(document.id)
                    counts["documents"] += 1

            # Audit rows keep their shape (who did what, when); the before/
            # after values that quoted the person do not.
            if pii:
                conditions = [cast(AuditLog.field_changes, String).contains(value) for value in pii]
                if entity_ids:
                    conditions.append(AuditLog.entity_id.in_(entity_ids))
                audit_query = select(AuditLog).where(
                    AuditLog.license_id == license_row.id, AuditLog.field_changes.isnot(None),
                    or_(*conditions),
                )
                for row in self._s.execute(audit_query).scalars():
                    scrubbed = _scrub(row.field_changes, pii)
                    if scrubbed != row.field_changes:
                        row.field_changes = scrubbed
                        counts["audit_rows"] += 1
            for key, value in counts.items():
                touched[key] += value
            # The caller clears this person's conversational cache for each
            # of these: erasure deletes rows and never touched Redis, so
            # "the customer we were just talking about" and a half-finished
            # request outlived the record they were about (10 ก.ย. 2569).
            erased_licenses.append(str(license_row.id))
            AuditRepository(self._s).write(
                license_id=license_row.id, entity_type="data_subject_request", entity_id=request.id,
                actor_type="system" if processed_by is None else "user",
                actor_id=str(processed_by) if processed_by else chann_uid,
                action="pdpa_erasure", field_changes={"chann_uid": chann_uid, **counts},
                cross_tenant=True,
            )

        if identity.signature_url:
            paths.append(identity.signature_url)
        # The copies they asked for earlier ("ขอข้อมูลของฉัน") are the
        # person's data too — every export page this platform produced for
        # them goes as well. Named by request id, so no bucket listing.
        for earlier in self._s.execute(
            select(DataSubjectRequest).where(
                DataSubjectRequest.chann_uid == chann_uid, DataSubjectRequest.request_type == "export",
                DataSubjectRequest.status == "completed",
            )
        ).scalars():
            paths.append(export_object_path(chann_uid, earlier.id))
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
        touched["licenses"] = erased_licenses
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
                "notes": [], "follow_ups": [], "documents": [],
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
                deal_ids: set[uuid.UUID] = set()
                for deal in self._s.execute(select(Deal).where(Deal.contact_id == customer.id)).scalars():
                    deal_ids.add(deal.id)
                    company["deals"].append({
                        "deal_id": deal.deal_id, "stage": deal.stage, "notes": deal.notes,
                        "created_at": _iso(deal.created_at),
                    })
                # The same tables erasure clears, so the copy shows the
                # person everything erasure would take (review D10).
                for note in self._s.execute(
                    select(Note).where(Note.entity_type == "customer", Note.entity_id == customer.id)
                    .order_by(Note.created_at)
                ).scalars():
                    company["notes"].append({"body": note.body, "created_at": _iso(note.created_at)})
                for follow_up in self._s.execute(
                    select(FollowUp).where(FollowUp.entity_type == "customer", FollowUp.entity_id == customer.id)
                    .order_by(FollowUp.due_date)
                ).scalars():
                    company["follow_ups"].append({
                        "due_date": _iso(follow_up.due_date), "status": follow_up.status, "notes": follow_up.notes,
                    })
                if deal_ids:
                    quote_ids = {
                        q.id for q in self._s.execute(select(Quote).where(Quote.deal_id.in_(deal_ids))).scalars()
                    }
                    if quote_ids:
                        self._export_documents(company, license_row.id, quote_ids)
            ticket_ids: set[uuid.UUID] = set()
            for ticket in self._s.execute(
                select(ServiceTicket).where(ServiceTicket.license_id == license_row.id, ServiceTicket.customer_chann_uid == chann_uid)
            ).scalars():
                ticket_ids.add(ticket.id)
                company["tickets"].append({
                    "ticket_number": ticket.ticket_number, "status": ticket.status,
                    "issue_description": ticket.issue_description,
                    "service_address": getattr(ticket, "service_address", None),
                    "created_at": _iso(ticket.created_at),
                })
            if ticket_ids:
                report_ids = {
                    r.id for r in self._s.execute(
                        select(ServiceReport).where(ServiceReport.ticket_id.in_(ticket_ids))
                    ).scalars()
                }
                if report_ids:
                    self._export_documents(company, license_row.id, report_ids)
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

    def _export_documents(self, company: dict, license_id: uuid.UUID, source_ids: set[uuid.UUID]) -> None:
        for document in self._s.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.license_id == license_id, GeneratedDocument.source_entity_id.in_(source_ids),
            )
        ).scalars():
            company["documents"].append({
                "document_type": document.document_type, "source_entity_type": document.source_entity_type,
                "generated_at": _iso(getattr(document, "generated_at", None) or getattr(document, "created_at", None)),
                "data_snapshot": document.data_snapshot,
            })
