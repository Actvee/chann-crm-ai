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
        # review C8 (6 Sep 2026): reading the catalogue is not managing
        # it — a salesperson picking a line for a deal needs the list, not
        # the right to change prices.
        "product.read",
        "product.manage",
        "team.manage",
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
        # Phase 14-B: who may change the approval flow itself (by typing a
        # policy in chat or on the config page). Owner/Admin by the
        # templates below; CS approves but does not redesign the flow.
        "approval.manage",
        # chat_session.claim / chat_session.transfer, assignment_rule.manage
        # and billing.* were catalogue entries nothing enforced (review C9,
        # 6 Sep 2026): a role built from them granted nothing, which read as
        # a broken roles page. Re-add a key only together with the route
        # that checks it.
        "chat_session.view",
        "chat_session.reply",
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
            "product.read",
            "chat_session.view",
            "chat_session.reply",
            "view_reports",
            "reassign_records",
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
            "product.read",
            "audit_log.view",
            "reassign_records",
        }
    ),
    # Master Spec section 6's Technician OA activity table: claim/assign/
    # close a ticket, and file the service report that closing one requires.
    # Not "cs" widened — a field technician has no business with approvals,
    # customer records, or chat-session handling, all of which are
    # Sales/CS-side. Mirrors application/chann_app/services/chat.py's
    # OA_ALLOWED_PERMISSION_KEYS["technician"] deliberately, not by import:
    # the Data tier has no dependency on the Application tier's Python
    # package, only its HTTP API.
    "technician": frozenset(
        {
            *(key for key in PERMISSION_KEYS if key.startswith("ticket.")),
            *(key for key in PERMISSION_KEYS if key.startswith("service_report.")),
            # The warranty picker on a job and the part a report names come
            # from the catalogue; reading it is part of the job.
            "product.read",
        }
    ),
}


def validate_permission_keys(keys: set[str] | frozenset[str]) -> None:
    unknown = sorted(set(keys) - PERMISSION_KEYS)
    if unknown:
        raise ValueError(f"unknown permission keys: {', '.join(unknown)}")


# Human-readable label per permission key — Master Spec 6.6 needs these so the
# bot can answer "what can I do?" in the user's own words instead of reciting
# dotted keys. The same catalogue is what a checkbox permission editor needs,
# so it lives here next to PERMISSION_KEYS rather than in the UI layer.
PERMISSION_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "customer.read": {"th": "ดูข้อมูลลูกค้า", "en": "View customers"},
    "customer.create": {"th": "เพิ่มลูกค้าใหม่", "en": "Add customers"},
    "customer.update": {"th": "แก้ไขข้อมูลลูกค้า", "en": "Edit customers"},
    "customer.archive": {"th": "เก็บลูกค้าเข้าคลัง", "en": "Archive customers"},
    "deal.read": {"th": "ดูดีล", "en": "View deals"},
    "deal.create": {"th": "สร้างดีล", "en": "Create deals"},
    "deal.update": {"th": "แก้ไขดีล", "en": "Edit deals"},
    "deal.archive": {"th": "เก็บดีลเข้าคลัง", "en": "Archive deals"},
    "deal.reopen": {"th": "เปิดดีลที่ปิดแล้วใหม่", "en": "Reopen closed deals"},
    "note.read": {"th": "ดูบันทึก", "en": "View notes"},
    "note.create": {"th": "เพิ่มบันทึก", "en": "Add notes"},
    "note.update": {"th": "แก้ไขบันทึก", "en": "Edit notes"},
    "followup.read": {"th": "ดูรายการติดตาม", "en": "View follow-ups"},
    "followup.create": {"th": "ตั้งรายการติดตาม", "en": "Create follow-ups"},
    "followup.update": {"th": "แก้ไขรายการติดตาม", "en": "Edit follow-ups"},
    "product.read": {"th": "ดูรายการสินค้า", "en": "View products"},
    "product.manage": {"th": "จัดการสินค้า", "en": "Manage products"},
    "team.manage": {"th": "จัดการทีม", "en": "Manage teams"},
    "ticket.read": {"th": "ดูใบงาน", "en": "View tickets"},
    "ticket.create": {"th": "เปิดใบงาน", "en": "Create tickets"},
    "ticket.update": {"th": "แก้ไขใบงาน", "en": "Edit tickets"},
    "ticket.assign": {"th": "มอบหมายใบงาน", "en": "Assign tickets"},
    "ticket.close": {"th": "ปิดใบงาน", "en": "Close tickets"},
    "quote.read": {"th": "ดูใบเสนอราคา", "en": "View quotes"},
    "quote.create": {"th": "สร้างใบเสนอราคา", "en": "Create quotes"},
    "quote.update": {"th": "แก้ไขใบเสนอราคา", "en": "Edit quotes"},
    "service_report.read": {"th": "ดูใบรายงานบริการ", "en": "View service reports"},
    "service_report.create": {"th": "สร้างใบรายงานบริการ", "en": "Create service reports"},
    "service_report.update": {"th": "แก้ไขใบรายงานบริการ", "en": "Edit service reports"},
    "approval.view": {"th": "ดูรายการรออนุมัติ", "en": "View approvals"},
    "approval.approve": {"th": "อนุมัติ", "en": "Approve"},
    "approval.reject": {"th": "ไม่อนุมัติ", "en": "Reject"},
    "approval.manage": {"th": "ตั้งค่าขั้นตอนอนุมัติ", "en": "Manage approval flows"},
    "chat_session.view": {"th": "ดูห้องแชท", "en": "View chat sessions"},
    "chat_session.reply": {"th": "ตอบแชทลูกค้า", "en": "Reply to chats"},
    "reassign_records": {"th": "โอนงานให้ผู้รับผิดชอบคนอื่น", "en": "Reassign records"},
    "view_reports": {"th": "ดูรายงาน", "en": "View reports"},
    "role.manage": {"th": "จัดการบทบาทและสิทธิ์", "en": "Manage roles and permissions"},
    "member.manage": {"th": "จัดการสมาชิก", "en": "Manage members"},
    "setting.manage": {"th": "จัดการการตั้งค่าบริษัท", "en": "Manage company settings"},
    "warranty.read": {"th": "ดูใบรับประกัน", "en": "View warranties"},
    "warranty.create": {"th": "ออกใบรับประกัน", "en": "Create warranties"},
    "warranty.update": {"th": "แก้ไขใบรับประกัน", "en": "Edit warranties"},
    "audit_log.view": {"th": "ดูประวัติการใช้งาน", "en": "View audit log"},
    "platform.admin.access": {"th": "เข้าถึงระบบผู้ดูแลแพลตฟอร์ม", "en": "Platform admin access"},
    "platform.admin.break_glass": {"th": "ใช้สิทธิ์ฉุกเฉินของแพลตฟอร์ม", "en": "Platform break-glass"},
    "pdpa.request.view": {"th": "ดูคำขอ PDPA", "en": "View PDPA requests"},
    "pdpa.request.process": {"th": "ดำเนินการคำขอ PDPA", "en": "Process PDPA requests"},
}


def describe(permission_key: str, language: str = "th") -> str:
    """Label for one key, falling back to the key itself.

    Returning the raw key for an undescribed permission is deliberate: a
    missing label should look obviously unfinished rather than silently drop
    the permission out of a "what can I do" list, which would read as though
    the user lacked it.
    """
    entry = PERMISSION_DESCRIPTIONS.get(permission_key)
    if entry is None:
        return permission_key
    return entry.get(language) or entry.get("th") or permission_key


# The two staff channels a license_members row can be for (owner, 8 Sep
# 2026). The Customer OA has no members row at all — a customer links
# through customer_license_links.
MEMBER_CHANNELS = ("sales", "technician")
TECHNICIAN_ROLE_NAME = "technician"


def channel_for_role(role: str) -> str:
    """Which OA a membership with this role is for. The technician
    persona is its own registration on the Technician OA; every other
    role — owner, admin, cs, member, a tenant's custom roles — works on
    the Sales/CS OA. An invite carries its role, so this is also which OA
    the invite may be redeemed on."""
    return "technician" if (role or "").strip().lower() == TECHNICIAN_ROLE_NAME else "sales"
