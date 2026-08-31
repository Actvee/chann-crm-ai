"""Phase 10 — quotes + generic document template engine (Master Spec
10.1-10.7).

Scope of what this file actually does, deliberately: quote CRUD (real,
usable now — a quote can exist and move through its own status lifecycle
without ever having a rendered PDF, per 10.4's "generated_document_id is
nullable" design) and the *template version workflow* (draft -> published
-> archived, immutability once published, versioning on edit).

What this file does NOT do, on purpose: DOCX parsing, the AI-assisted
field/mapping proposal, Intermediate Template Model generation, HTML
compilation, or the actual SmartBrowz render call. Building those requires
real Zoho Catalyst SmartBrowz credentials this environment does not have
configured (`docs/RUNTIME_CONFIG_CONTRACT.md` still lists every
`SMARTBROWZ_*` variable as `REQUIRED_BY_PHASE_10`, not yet set) — writing
an untested adapter against a real external API would be exactly the kind
of code this project's own standards (validate everything against the
real thing before calling it done) argue against. `GeneratedDocumentRepository`
below only records the audit trail of a render that already happened
elsewhere; it does not perform one.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Deal,
    DealProduct,
    QuoteProduct,
    DocumentTemplate,
    DocumentTemplateVersion,
    GeneratedDocument,
    License,
    Quote,
)
from .tenant_scope import TenantScope

QUOTE_STATUSES = frozenset({"draft", "sent", "accepted", "rejected", "expired"})
_QUOTE_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"sent"}),
    "sent": frozenset({"accepted", "rejected", "expired"}),
    # Terminal once accepted/rejected/expired — 10.1's spec gives quotes no
    # reopen concept the way 9.6 gives deals one; a rejected/expired quote
    # is superseded by creating a new quote, not resurrected.
}

TEMPLATE_VERSION_STATUSES = frozenset({"draft", "previewed", "published", "archived"})


class Phase10Conflict(RuntimeError):
    """Well-formed but not allowed in the current state."""


class Phase10NotFound(LookupError):
    pass


class QuoteRepository:
    def __init__(self, session: Session):
        self._s = session

    def create(
        self, scope: TenantScope, *, deal_id: uuid.UUID,
        owner_member_id: uuid.UUID | None = None,
    ) -> Quote:
        deal = self._s.execute(
            select(Deal).where(Deal.id == deal_id, Deal.license_id == scope.license_id)
        ).scalars().first()
        if deal is None:
            raise Phase10NotFound("deal not found in this tenant")

        # A quote with nothing on it is a document that says a customer
        # owes zero. It renders, it gets sent, and the first person to
        # notice is the customer — so the deal has to have at least one
        # line item before a quote can exist for it.
        line_items = self._s.execute(
            select(DealProduct.id).where(DealProduct.deal_id == deal_id).limit(1)
        ).first()
        if line_items is None:
            raise Phase10Conflict(
                "this deal has no products yet — add at least one before quoting"
            )

        row = Quote(
            id=uuid.uuid4(), license_id=scope.license_id,
            quote_id=self._unique_quote_id(scope.license_id), deal_id=deal_id,
            status="draft", owner_member_id=owner_member_id,
        )
        self._s.add(row)
        self._s.flush()

        # Copy, don't reference. From here the quote is its own document:
        # editing the deal will not rewrite a quote already under
        # discussion, and two quotes on one deal can differ — which is
        # what happens the first time a customer changes their mind.
        source = self._s.execute(
            select(DealProduct)
            .where(DealProduct.deal_id == deal_id)
            .order_by(DealProduct.position.asc(), DealProduct.created_at.asc())
        ).scalars().all()
        for position, item in enumerate(source):
            self._s.add(QuoteProduct(
                id=uuid.uuid4(),
                license_id=scope.license_id,
                quote_id=row.id,
                product_id=item.product_id,
                product_name=item.product_name,
                quoted_unit_price=item.quoted_unit_price,
                qty=item.qty,
                notes=item.notes,
                position=position,
            ))
        self._s.flush()
        return row

    # ------------------------------------------------------- quote lines

    def list_products(self, scope: TenantScope, quote_id: uuid.UUID) -> list[QuoteProduct]:
        return list(
            self._s.execute(
                select(QuoteProduct)
                .where(
                    QuoteProduct.license_id == scope.license_id,
                    QuoteProduct.quote_id == quote_id,
                )
                .order_by(QuoteProduct.position, QuoteProduct.created_at)
            ).scalars()
        )

    def _editable(self, scope: TenantScope, quote_id: uuid.UUID) -> Quote:
        row = self.get(scope, quote_id)
        if row is None:
            raise Phase10NotFound("quote not found in this tenant")
        if row.status != "draft":
            # An issued quote is a document a customer is holding. Changing
            # what it says after the fact, without a new version, is how
            # two people end up quoting different numbers from the same
            # reference.
            raise Phase10Conflict(
                f"quote {row.quote_id} is {row.status} and can no longer be edited"
            )
        return row

    def add_product(
        self, scope: TenantScope, quote_id: uuid.UUID, *,
        product_name: str, quoted_unit_price, qty: int = 1,
        product_id: uuid.UUID | None = None, notes: str | None = None,
    ) -> QuoteProduct:
        self._editable(scope, quote_id)
        name = (product_name or "").strip()
        if not name:
            raise Phase10Conflict("a line needs a product name")
        try:
            price = Decimal(str(quoted_unit_price))
        except (InvalidOperation, TypeError, ValueError):
            raise Phase10Conflict(f"invalid price: {quoted_unit_price!r}")
        if price < 0:
            raise Phase10Conflict("a price cannot be negative")

        last = self._s.execute(
            select(func.max(QuoteProduct.position)).where(
                QuoteProduct.quote_id == quote_id
            )
        ).scalar()
        row = QuoteProduct(
            id=uuid.uuid4(), license_id=scope.license_id, quote_id=quote_id,
            product_id=product_id, product_name=name, quoted_unit_price=price,
            qty=max(1, int(qty)), notes=notes,
            position=(last + 1) if last is not None else 0,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def update_product(
        self, scope: TenantScope, quote_id: uuid.UUID, line_id: uuid.UUID, fields: dict,
    ) -> QuoteProduct:
        """Change a line — the price a customer negotiated, or the count.

        This is the whole point of quotes owning their lines: "ลดให้
        เหลือ 1,400" applies to THIS offer, not to the deal and every
        other quote made from it.
        """
        self._editable(scope, quote_id)
        row = self._s.execute(
            select(QuoteProduct).where(
                QuoteProduct.id == line_id,
                QuoteProduct.quote_id == quote_id,
                QuoteProduct.license_id == scope.license_id,
            )
        ).scalars().first()
        if row is None:
            raise Phase10NotFound("line not found on this quote")

        if "quoted_unit_price" in fields and fields["quoted_unit_price"] is not None:
            try:
                price = Decimal(str(fields["quoted_unit_price"]))
            except (InvalidOperation, TypeError, ValueError):
                raise Phase10Conflict(f"invalid price: {fields['quoted_unit_price']!r}")
            if price < 0:
                raise Phase10Conflict("a price cannot be negative")
            row.quoted_unit_price = price
        if "qty" in fields and fields["qty"] is not None:
            row.qty = max(1, int(fields["qty"]))
        if "product_name" in fields and str(fields["product_name"] or "").strip():
            row.product_name = str(fields["product_name"]).strip()
        if "notes" in fields:
            row.notes = fields["notes"]
        self._s.flush()
        return row

    def remove_product(
        self, scope: TenantScope, quote_id: uuid.UUID, line_id: uuid.UUID,
    ) -> None:
        self._editable(scope, quote_id)
        row = self._s.execute(
            select(QuoteProduct).where(
                QuoteProduct.id == line_id,
                QuoteProduct.quote_id == quote_id,
                QuoteProduct.license_id == scope.license_id,
            )
        ).scalars().first()
        if row is None:
            raise Phase10NotFound("line not found on this quote")
        self._s.delete(row)
        self._s.flush()

    def _unique_quote_id(self, license_id: uuid.UUID) -> str:
        """Per-tenant, unlike Deal.deal_id — see Quote's docstring in
        models.py for why. Retry-on-collision, same pattern as
        DealRepository._unique_deal_id and phase65.py's invite/license
        codes."""
        year = datetime.now(timezone.utc).year
        for _ in range(50):
            existing = self._s.execute(
                select(Quote.quote_id).where(
                    Quote.license_id == license_id,
                    Quote.quote_id.like(f"Q-{year}-%"),
                )
            ).scalars().all()
            used = {
                int(code.rsplit("-", 1)[1]) for code in existing
                if code.rsplit("-", 1)[1].isdigit()
            }
            next_n = (max(used) + 1) if used else 1
            candidate = f"Q-{year}-{next_n:04d}"
            clash = self._s.execute(
                select(Quote.id).where(
                    Quote.license_id == license_id, Quote.quote_id == candidate,
                )
            ).first()
            if clash is None:
                return candidate
        raise Phase10Conflict("could not allocate a unique quote_id")

    def get(self, scope: TenantScope, quote_id: uuid.UUID) -> Quote | None:
        return self._s.execute(
            select(Quote).where(Quote.id == quote_id, Quote.license_id == scope.license_id)
        ).scalars().first()

    def link_document(
        self, scope: TenantScope, quote_id: uuid.UUID, document_id: uuid.UUID,
    ) -> Quote:
        """Point a quote at the document that was generated for it.

        Tenant-scoped on both sides: the quote must belong to this license,
        and so must the document, so a caller cannot attach another
        tenant's document to its own quote.
        """
        row = self.get(scope, quote_id)
        if row is None:
            raise Phase10NotFound("quote not found")
        document = self._s.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.license_id == scope.license_id,
            )
        ).scalars().first()
        if document is None:
            raise Phase10NotFound("generated document not found")
        row.generated_document_id = document.id
        self._s.flush()
        return row

    def list_for_license(self, scope: TenantScope, *, status: str | None = None) -> list[Quote]:
        query = select(Quote).where(Quote.license_id == scope.license_id)
        if status:
            query = query.where(Quote.status == status)
        return list(self._s.execute(query.order_by(Quote.created_at.desc())).scalars())

    def transition_status(
        self, scope: TenantScope, quote_id: uuid.UUID, *, to_status: str,
    ) -> Quote:
        if to_status not in QUOTE_STATUSES:
            raise Phase10Conflict(f"unknown quote status: {to_status!r}")
        quote = self.get(scope, quote_id)
        if quote is None:
            raise Phase10NotFound("quote not found in this tenant")
        allowed = _QUOTE_ALLOWED_TRANSITIONS.get(quote.status, frozenset())
        if to_status not in allowed:
            raise Phase10Conflict(
                f"cannot move a quote from {quote.status!r} to {to_status!r}"
            )
        quote.status = to_status
        self._s.flush()
        return quote


class DocumentTemplateRepository:
    """10.3/10.4 — the template-slot + version workflow, independent of how
    a version's content actually got compiled (see module docstring)."""

    def __init__(self, session: Session):
        self._s = session

    def create_template(
        self, scope: TenantScope, *, document_type: str, template_code: str,
        template_name: str,
    ) -> DocumentTemplate:
        template_code = (template_code or "").strip()
        template_name = (template_name or "").strip()
        if not template_code or not template_name:
            raise Phase10Conflict("template_code and template_name are both required")
        existing = self._s.execute(
            select(DocumentTemplate).where(
                DocumentTemplate.license_id == scope.license_id,
                DocumentTemplate.template_code == template_code,
            )
        ).scalars().first()
        if existing is not None:
            raise Phase10Conflict(f"template_code {template_code!r} already exists")
        row = DocumentTemplate(
            id=uuid.uuid4(), license_id=scope.license_id, document_type=document_type,
            template_code=template_code, template_name=template_name, is_active=True,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get_template(self, scope: TenantScope, template_id: uuid.UUID) -> DocumentTemplate | None:
        return self._s.execute(
            select(DocumentTemplate).where(
                DocumentTemplate.id == template_id,
                DocumentTemplate.license_id == scope.license_id,
            )
        ).scalars().first()

    def list_templates(
        self, scope: TenantScope, *, document_type: str | None = None,
    ) -> list[DocumentTemplate]:
        query = select(DocumentTemplate).where(DocumentTemplate.license_id == scope.license_id)
        if document_type:
            query = query.where(DocumentTemplate.document_type == document_type)
        return list(self._s.execute(query.order_by(DocumentTemplate.created_at.desc())).scalars())

    def create_draft_version(
        self, scope: TenantScope, template_id: uuid.UUID, *,
        source_docx_path: str, intermediate_model: dict, mapping_schema: dict,
        compiled_template_path: str, renderer: str = "smartbrowz",
        renderer_mode: str = "html_convert", smartbrowz_template_id: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> DocumentTemplateVersion:
        """A new draft — either the first version of a template, or 10.4's
        "editing a published version creates N+1", never an in-place edit
        of an existing row (see publish_version's immutability note)."""
        template = self.get_template(scope, template_id)
        if template is None:
            raise Phase10NotFound("template not found in this tenant")
        existing_versions = self._s.execute(
            select(DocumentTemplateVersion.version).where(
                DocumentTemplateVersion.template_id == template_id,
            )
        ).scalars().all()
        next_version = (max(existing_versions) + 1) if existing_versions else 1
        row = DocumentTemplateVersion(
            id=uuid.uuid4(), template_id=template_id, version=next_version,
            status="draft", source_docx_path=source_docx_path,
            intermediate_model=intermediate_model, mapping_schema=mapping_schema,
            compiled_template_path=compiled_template_path, renderer=renderer,
            renderer_mode=renderer_mode, smartbrowz_template_id=smartbrowz_template_id,
            created_by=created_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get_version(
        self, scope: TenantScope, version_id: uuid.UUID,
    ) -> DocumentTemplateVersion | None:
        row = self._s.execute(
            select(DocumentTemplateVersion, DocumentTemplate)
            .join(DocumentTemplate, DocumentTemplate.id == DocumentTemplateVersion.template_id)
            .where(
                DocumentTemplateVersion.id == version_id,
                DocumentTemplate.license_id == scope.license_id,
            )
        ).first()
        return row[0] if row else None

    def list_versions(
        self, scope: TenantScope, template_id: uuid.UUID,
    ) -> list[DocumentTemplateVersion]:
        template = self.get_template(scope, template_id)
        if template is None:
            raise Phase10NotFound("template not found in this tenant")
        return list(
            self._s.execute(
                select(DocumentTemplateVersion)
                .where(DocumentTemplateVersion.template_id == template_id)
                .order_by(DocumentTemplateVersion.version.asc())
            ).scalars()
        )

    def mark_previewed(
        self, scope: TenantScope, version_id: uuid.UUID,
    ) -> DocumentTemplateVersion:
        """10.7's "preview does not publish" — a distinct, reversible state
        from an explicit publish approval. Only legal from draft: a
        published or archived version has nothing left to preview as."""
        version = self.get_version(scope, version_id)
        if version is None:
            raise Phase10NotFound("template version not found in this tenant")
        if version.status != "draft":
            raise Phase10Conflict(
                f"can only preview a draft version, this one is {version.status!r}"
            )
        version.status = "previewed"
        self._s.flush()
        return version

    def publish_version(
        self, scope: TenantScope, version_id: uuid.UUID,
    ) -> DocumentTemplateVersion:
        """10.4's explicit approval step. Legal from draft or previewed;
        once published, this exact row is never mutated again — any further
        change must go through create_draft_version to make a new N+1
        version instead (enforced by there being no "update" method on a
        published version at all, not by a runtime check here)."""
        version = self.get_version(scope, version_id)
        if version is None:
            raise Phase10NotFound("template version not found in this tenant")
        if version.status not in ("draft", "previewed"):
            raise Phase10Conflict(
                f"cannot publish a version that is already {version.status!r}"
            )
        version.status = "published"
        version.published_at = datetime.now(timezone.utc)
        self._s.flush()
        return version

    def archive_version(
        self, scope: TenantScope, version_id: uuid.UUID,
    ) -> DocumentTemplateVersion:
        version = self.get_version(scope, version_id)
        if version is None:
            raise Phase10NotFound("template version not found in this tenant")
        version.status = "archived"
        self._s.flush()
        return version


class GeneratedDocumentRepository:
    """Records the audit trail of a render — see module docstring for why
    this repository does not perform the render itself."""

    def __init__(self, session: Session):
        self._s = session

    def record(
        self, scope: TenantScope, *, document_type: str, source_entity_type: str,
        source_entity_id: uuid.UUID, template_version_id: uuid.UUID,
        data_snapshot: dict, output_path: str, sha256: str,
        renderer: str = "smartbrowz", generated_by: uuid.UUID | None = None,
    ) -> GeneratedDocument:
        row = GeneratedDocument(
            id=uuid.uuid4(), license_id=scope.license_id, document_type=document_type,
            source_entity_type=source_entity_type, source_entity_id=source_entity_id,
            template_version_id=template_version_id, data_snapshot=data_snapshot,
            output_path=output_path, sha256=sha256, renderer=renderer,
            generated_by=generated_by,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def get(self, scope: TenantScope, document_id: uuid.UUID) -> GeneratedDocument | None:
        return self._s.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.license_id == scope.license_id,
            )
        ).scalars().first()

    def list_for_source(
        self, scope: TenantScope, *, source_entity_type: str, source_entity_id: uuid.UUID,
    ) -> list[GeneratedDocument]:
        return list(
            self._s.execute(
                select(GeneratedDocument).where(
                    GeneratedDocument.license_id == scope.license_id,
                    GeneratedDocument.source_entity_type == source_entity_type,
                    GeneratedDocument.source_entity_id == source_entity_id,
                ).order_by(GeneratedDocument.generated_at.desc())
            ).scalars()
        )


# Everything a Thai tax document legally has to carry about the issuing
# company. vat_rate is deliberately NOT here: a tenant that is not
# VAT-registered is a legitimate, complete state, not a missing field.
REQUIRED_DOCUMENT_FIELDS = ("tax_id", "company_address")


class CompanyProfileRepository:
    """Phase 10 — the issuing company's identity as it appears on
    customer-facing documents.

    Split out from the Phase 6.5 registration repository on purpose: that
    one owns creating a tenant, this one owns the much later, much rarer
    act of a tenant filling in its legal details before its first real
    document goes out. They change for different reasons.
    """

    def __init__(self, session: Session):
        self._s = session

    def get(self, scope: TenantScope) -> License | None:
        return self._s.execute(
            select(License).where(License.id == scope.license_id)
        ).scalars().first()

    @staticmethod
    def missing_for_documents(row: License) -> list[str]:
        """Which legally-required fields are still blank.

        Whitespace counts as blank: a tax_id of " " is not a tax ID, and
        letting one through would put a visually-empty field on a document
        that claims to be complete.
        """
        missing = []
        for field in REQUIRED_DOCUMENT_FIELDS:
            value = getattr(row, field, None)
            if value is None or not str(value).strip():
                missing.append(field)
        return missing

    @classmethod
    def is_document_ready(cls, row: License) -> bool:
        return not cls.missing_for_documents(row)

    def update(self, scope: TenantScope, fields: dict) -> License:
        """Partial update. Only keys actually present in `fields` are
        touched, so an explicit None clears a value while an omitted key
        leaves it alone — the distinction matters for vat_rate, where
        "cleared" means "no longer VAT-registered" rather than "unknown".
        """
        row = self.get(scope)
        if row is None:
            raise LookupError("license not found")

        allowed = {
            "legal_name", "tax_id", "company_address",
            "company_phone", "company_email", "vat_rate",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, key, value)

        if row.tax_id is not None:
            digits = row.tax_id.strip()
            # Validated here rather than only at the API edge so no caller
            # can write a malformed one. 13 digits exactly, per Thai TIN.
            if not (digits.isdigit() and len(digits) == 13):
                raise ValueError("tax_id must be exactly 13 digits")
            row.tax_id = digits

        if row.vat_rate is not None and not (0 <= row.vat_rate <= 1):
            # A fraction, not a percentage — 7% is 0.07. Someone passing 7
            # would otherwise silently produce a 700% VAT line.
            raise ValueError("vat_rate must be a fraction between 0 and 1 (0.07 = 7%)")

        self._s.flush()
        return row
