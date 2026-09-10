"""Field names must agree across the three tiers.

Three tier-seam bugs of the same shape have reached production: the
dashboard read a field the Data tier never sent (`name` for
`product_name`, `purchase_date` for `warranty_start`), and — review C1/C2,
6 Sep 2026 — the Data tier *assigned* a field the schema did not declare
(`_deal_out(... amount=...)` into a DealOut without `amount`), which
pydantic drops without a word. Every one was invisible from the tier
being looked at, so the check is mechanical, in three directions:

1. every keyword the Data router passes to an `*Out(...)` constructor is
   a field of that schema — otherwise it is silently discarded;
2. every column an ORM model has that its `*Out` twin lacks is listed,
   so an omission is a decision someone wrote down (ACCEPTED_OMISSIONS)
   rather than an accident;
3. every field a dashboard TS type declares — nested ones included; the
   old version skipped any type named `Detail`, which is exactly where
   QuoteOut's missing terms hid — exists in some `*Out` schema, or is
   composed by the Application tier and listed in ACCEPTED_TS with the
   route that adds it.

Findings are a prompt to look, not a verdict; anything deliberate goes in
the accepted lists with the reason, so the report stays at zero and the
next drift is visible immediately.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, "data")
import chann_data.schemas as S  # noqa: E402
from chann_data import models as M  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# (schema, column) the schema leaves out on purpose.
ACCEPTED_OMISSIONS: dict[tuple[str, str], str] = {
    ("DealProductOut", "position"): "ordering key; the router returns lines already in order",
    ("LicenseOut", "auto_accept_new_customers"): "a license setting, read through /settings",
    ("LicenseOut", "legal_name"): "company profile: CompanyProfileOut sends it",
    ("LicenseOut", "tax_id"): "company profile: CompanyProfileOut sends it",
    ("LicenseOut", "company_address"): "company profile: CompanyProfileOut sends it",
    ("LicenseOut", "company_phone"): "company profile: CompanyProfileOut sends it",
    ("LicenseOut", "company_email"): "company profile: CompanyProfileOut sends it",
    ("LicenseOut", "vat_rate"): "company profile: CompanyProfileOut sends it",
    ("MemberOut", "joined_at"): "not shown anywhere; the row id, uid, role and status are what callers read",
    ("TicketOut", "created_by"): "internal actor reference; owner_member_id is the field the app uses",
    ("WarrantyOut", "customer_chann_uid"): "the warranty list route builds its own dict and sends it there",
    ("WarrantyOut", "contact_id"): "the warranty list route builds its own dict; the app reads the customer link, not the id",
    ("WarrantyOut", "pdf_path"): "storage path, never sent to a browser",
    ("WarrantyOut", "generated_document_id"): "issued-document link, read through the documents routes",
}

# (file, TS type) that describes a component's own props or local state,
# not API data — nothing to compare against.
LOCAL_TYPES: set[tuple[str, str]] = {
    ("_inline-create.tsx", "CreateField"),
    ("_searchable-picker.tsx", "PickerOption"),
    ("_record.tsx", "FieldSpec"),
    ("MemberManagement.tsx", "Person"),  # one member grouped with every OA row they hold; page state
    ("_nav-model.tsx", "NavEntry"),  # one left-navigation link: href, icon, permission keys — nothing crosses a tier
    ("_nav-model.tsx", "NavGroup"),  # a labelled group of those links
    ("_nav.tsx", "NavContextValue"),  # the rail's open/collapsed state, shared with the top bar's menu button
    ("DocumentTemplates.tsx", "Preview"),  # the open template preview: which version, its filled HTML and its blank list — page state, discarded on close
}

# (file, TS type) whose fields the Application tier composes rather than
# any *Out schema sending them, with the route that adds them.
ACCEPTED_TS: dict[tuple[str, str], str] = {
    ("AiReports.tsx", "Answer"): "POST reports/ai returns {clarify|error|spec,result,text,files} built in reports_ai.py",
    ("ApprovalSettings.tsx", "Workflow"): "GET approval-workflows/{type} adds `summary` from describe_workflow",
    ("RoleManagement.tsx", "CatalogEntry"): "GET permissions/catalog builds {key, group, label} in the Data router, not from a schema",
    ("ApprovalQueue.tsx", "Step"): "approval steps come from the approval_steps JSON the Data tier stores (step_order, approver_type, approver_ref are its keys)",
    ("CustomerHome.tsx", "Survey"): "the survey row's scale_config_json is a JSON column returned as-is by the survey routes",
    ("GuidePage.tsx", "Step"): "GET guides/{audience} is built by services/guides.py, not a schema",
    ("PipelineSummary.tsx", "Pipeline"): "GET licenses/{id}/pipeline returns the Data tier's summary dict (phase9 pipeline_summary)",
    ("_csv-import.tsx", "ImportResult"): "POST …/import returns csv_import.py's per-row verdicts",
    ("_bulk-paste.tsx", "ImportResult"): "same route as the CSV import — pasted lines go through it",
    ("_bulk-paste.tsx", "Parsed"): "client-side parse of a pasted line, never sent as-is",
    ("ServiceReports.tsx", "PdfResult"): "POST service-reports/{id}/document returns {document_id, sha256, url} from routers_phase2",
    ("MemberManagement.tsx", "MemberRow"): "GET licenses/{id}/members?include_removed=1 is composed by routers_phase2: one row per (member, OA) with channel, is_owner, joined_at and the profile's display_name/phone",
    ("DocumentTemplates.tsx", "InUse"): "GET document-templates/in-use is composed by routers_phase2 from documents/selection.py: {source, template_id, template_name, version_id, version} per document type",
}

# Application-composed fields any TS type may declare (added by routers_phase2).
COMPOSED_FIELDS = {
    "display_name",  # _with_names on members/technicians
    "author_display_name",  # list_notes (C18)
    "missing_fields",  # dispatch-check (C11)
    "reason_code",  # _with_reason (C11)
    "ready", "missing",  # dispatch-check
    "url", "sha256", "generated_document_id", "output_path", "renderer",  # documents
    "created",  # open chat session
    "summary",  # approval workflow
    # routers_phase2 adds it to each row of GET document-templates/{id}/versions:
    # an asset link to the Word file the version was compiled from, or null
    "source_docx_url",
    "keys",  # picker options, local only
    # services/ticket_machine.py adds it to each row of GET tickets: the
    # cover state of the unit the fault is about, taken from the warranty
    # behind the ticket's serial. product_name/warranty_number/warranty_end
    # come from the same place and already exist as WarrantyOut fields.
    "warranty_status",
}

problems: list[str] = []

# ------------------------------------------------------------ 1. router kwargs
out_schemas = {
    name: obj
    for name in dir(S)
    if isinstance((obj := getattr(S, name)), type)
    and issubclass(obj, BaseModel)
    and name.endswith("Out")
}
known = set().union(*(set(o.model_fields) for o in out_schemas.values()))

router_src = Path("data/chann_data/routers/internal.py").read_text()
for node in ast.walk(ast.parse(router_src)):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in out_schemas
    ):
        fields = out_schemas[node.func.id].model_fields
        for kw in node.keywords:
            if kw.arg and kw.arg not in fields:
                problems.append(
                    f"internal.py:{node.lineno} passes {node.func.id}({kw.arg}=…) "
                    f"but the schema has no such field — pydantic drops it silently"
                )

# ------------------------------------------------------------ 2. model columns
# Schemas whose ORM twin is not simply the name minus "Out".
MODEL_FOR = {
    "TicketOut": "ServiceTicket",
    "TeamOut": "TechnicianTeam",
    "GroupOut": "SalesGroup",
    "MemberOut": "LicenseMember",
}
# Columns every schema is allowed to drop without a note.
ALWAYS_OMITTED = {"license_id", "id", "created_at", "updated_at"}

omissions: list[str] = []
for name, schema in out_schemas.items():
    model = getattr(M, MODEL_FOR.get(name, name[:-3]), None)
    table = getattr(model, "__table__", None)
    if table is None:
        continue
    for column in table.columns.keys():
        if column in schema.model_fields or column in ALWAYS_OMITTED:
            continue
        reason = ACCEPTED_OMISSIONS.get((name, column))
        if reason is None:
            omissions.append(f"{name} lacks {model.__name__}.{column}")

# ------------------------------------------------------------ 3. TS declared fields
suspect: list[tuple[str, str, str]] = []
for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    for block in re.finditer(r"type\s+(\w+)\s*=\s*\{(.*?)\n\};", text, re.S):
        tname, body = block.group(1), block.group(2)
        if tname == "Context" or (f.name, tname) in ACCEPTED_TS or (f.name, tname) in LOCAL_TYPES:
            continue
        declared: set[str] = set()
        for fm in re.finditer(r"^\s+(\w+)\??:\s*(.*)$", body, re.M):
            field, value = fm.group(1), fm.group(2).strip()
            if value.startswith("{"):
                # A nested object: check what is inside it, not the key.
                for inner in re.finditer(r"(\w+)\??:", value):
                    declared.add(inner.group(1))
                continue
            declared.add(field)
        for field in sorted(declared):
            if field not in known and field not in COMPOSED_FIELDS:
                suspect.append((f.name, tname, field))

print(f"{len(known)} schema field names known across {len(out_schemas)} *Out schemas")
if problems:
    print("\nASSIGNED BY THE DATA ROUTER BUT NOT IN THE SCHEMA (dropped silently):")
    for line in problems:
        print(f"  {line}")
if omissions:
    print("\nMODEL COLUMNS THE *Out SCHEMA DOES NOT SEND (add to the schema, or to ACCEPTED_OMISSIONS with a reason):")
    for line in omissions:
        print(f"  {line}")
if suspect:
    print("\nDECLARED IN TS BUT IN NO *Out SCHEMA (nor composed by the Application tier):")
    for where, tname, field in suspect:
        print(f"  {where:28} {tname}.{field}")
if not (problems or omissions or suspect):
    print("every declared field exists in a schema; every assigned field is declared")
