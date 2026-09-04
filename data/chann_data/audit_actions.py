"""The verbs an audit entry may use.

Defined once, here, because the same list exists as a database CHECK
constraint. When the two disagree the write fails — and since an audit
entry shares a transaction with the change it records, the CHANGE fails
too. Issuing a quote broke exactly this way: the PDF was rendered, stored
and recorded, and then the audit row for linking it to the quote was
rejected and took all of it back out.

A test asserts this matches the constraint, so adding a verb in code
without adding it to a migration fails the build instead of production.
"""
from __future__ import annotations

AUDIT_ACTIONS = frozenset(
    {
        "create",
        "update",
        "delete",
        "assign",
        "transfer",
        "cross_tenant_lookup",
        # Phase 10
        "link_document",
        "upsert",
        "status",
        "remove_product",
        # Phase 12
        "claim",
        "reject",
        # Phase 13
        "check_in",
        "check_out",
        # Phase 16.5 — widened by migration 0023_pdpa
        "pdpa_erasure",
        "pdpa_export",
    }
)
