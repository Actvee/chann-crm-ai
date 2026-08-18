"""Phase 2 permission catalogue and default tenant role templates.

Authorization checks use permission keys only. Role names are tenant-owned
labels and must never become policy in Application code.
"""
from __future__ import annotations


PERMISSION_KEYS = frozenset(
    {
        "customer.read",
        "customer.create",
        "customer.update",
        "customer.archive",
        "deal.read",
        "deal.create",
        "deal.update",
        "deal.archive",
        "deal.reopen",
        "note.read",
        "note.create",
        "note.update",
        "followup.read",
        "followup.create",
        "followup.update",
        "product.manage",
        "team.manage",
        "assignment_rule.manage",
        "ticket.read",
        "ticket.create",
        "ticket.update",
        "ticket.assign",
        "ticket.close",
        "quote.read",
        "quote.create",
        "quote.update",
        "service_report.read",
        "service_report.create",
        "service_report.update",
        "approval.view",
        "approval.approve",
        "approval.reject",
        "chat_session.view",
        "chat_session.claim",
        "chat_session.reply",
        "chat_session.transfer",
        "reassign_records",
        "view_reports",
        "role.manage",
        "member.manage",
        "setting.manage",
        "warranty.read",
        "warranty.create",
        "warranty.update",
        "audit_log.view",
        "platform.admin.access",
        "platform.admin.break_glass",
        "pdpa.request.view",
        "pdpa.request.process",
        "billing.view",
        "billing.manage",
    }
)


# The Master Spec's CS-vs-Sales acceptance test is the stricter rule when the
# final appendix's broad ``member ticket.*`` shorthand would let a Sales-like
# member assign tickets. Least privilege wins: ticket assignment stays with CS
# unless a tenant explicitly grants it through a custom role.
DEFAULT_ROLE_TEMPLATES: dict[str, frozenset[str] | None] = {
    "owner": None,  # is_owner=True means all catalogue permissions.
    "admin": frozenset(
        key for key in PERMISSION_KEYS if not key.startswith("platform.admin.")
    ),
    "member": frozenset(
        {
            *(key for key in PERMISSION_KEYS if key.startswith("customer.")),
            *(key for key in PERMISSION_KEYS if key.startswith("deal.")),
            *(key for key in PERMISSION_KEYS if key.startswith("note.")),
            *(key for key in PERMISSION_KEYS if key.startswith("followup.")),
            *(key for key in PERMISSION_KEYS if key.startswith("quote.")),
            *(key for key in PERMISSION_KEYS if key.startswith("warranty.")),
            "chat_session.view",
            "chat_session.claim",
            "chat_session.reply",
            "view_reports",
            "reassign_records",
            "billing.view",
        }
    ),
    "cs": frozenset(
        {
            *(key for key in PERMISSION_KEYS if key.startswith("ticket.")),
            *(key for key in PERMISSION_KEYS if key.startswith("service_report.")),
            *(key for key in PERMISSION_KEYS if key.startswith("approval.")),
            *(key for key in PERMISSION_KEYS if key.startswith("chat_session.")),
            "customer.read",
            "customer.update",
            "audit_log.view",
            "reassign_records",
        }
    ),
}


def validate_permission_keys(keys: set[str] | frozenset[str]) -> None:
    unknown = sorted(set(keys) - PERMISSION_KEYS)
    if unknown:
        raise ValueError(f"unknown permission keys: {', '.join(unknown)}")
