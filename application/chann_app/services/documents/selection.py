"""Which template a document is actually rendered from.

One rule, in one place, because there were two copies of it —
`quote_issue._resolve_template` and `report_issue._resolve_template` —
and the templates page had no copy at all, so it could not tell a shop
which of their layouts was in use. A page that shows a different answer
from the one the renderer uses is worse than a page that shows nothing.

**The rule.** The shop's own template for that document type that is
`is_active` and has a published version compiled to a real stored file;
the built-in layout when there is none. When more than one is still
active — which is every shop that has never made a choice, because
`is_active` defaulted to true and nothing ever wrote it — the most
recently created one wins, which is exactly what the old "first in the
list" did (the Data tier orders templates `created_at DESC`). That is
deliberate: a shop that published a quote template before this change
keeps getting that same document afterwards.

**Deactivated, or archived.** Falling back to the built-in rather than
failing is the existing principle (see `quote_issue._resolve_template`'s
docstring): a shop must not lose the ability to issue a quote because of
something that happened to a template. So if the chosen template is
switched off, or its only published version is archived, the next
document renders with the built-in layout and says so on the templates
page — it does not raise, and it does not quietly promote some other
template the shop did not choose.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# The document types a shop can upload a template for: the ones with a
# real issue path behind them. A type nothing renders would be a slot a
# shop could fill and never see used, so it is not offered.
TEMPLATE_DOCUMENT_TYPES = ("quote", "service_report")


def is_builtin_template(template: dict) -> bool:
    """The system's own layout, registered as a row so that
    `generated_documents.template_version_id` can point at it.

    Case-insensitive: the codes are `BUILTIN-QUOTE` and
    `BUILTIN-SERVICE-REPORT`, and a lowercase `startswith("builtin")` —
    which is what the templates page used — matched neither, so the
    built-in was listed as if a shop could edit it.
    """
    return str(template.get("template_code") or "").upper().startswith("BUILTIN")


def usable_version(versions: list[dict]) -> dict | None:
    """The published version of a template that would render, or None.

    Highest version number, so republishing supersedes rather than having
    to unpublish the old one first. A version whose compiled path is a
    `builtin://` marker is not a stored file and cannot be filled, so it
    does not count as usable.
    """
    published = [v for v in versions if v.get("status") == "published"]
    if not published:
        return None
    newest = max(published, key=lambda v: int(v.get("version") or 0))
    compiled = str(newest.get("compiled_template_path") or "")
    if not compiled or compiled.startswith("builtin://"):
        return None
    return newest


def choosable_templates(templates: list[dict]) -> list[dict]:
    """The tenant's own templates for a type, best candidate first.

    Order comes from the Data tier (`created_at DESC`) and is preserved,
    so the tie-break among several still-active templates is "the most
    recently created" — the behaviour shops have today.
    """
    return [
        t for t in templates
        if not is_builtin_template(t) and bool(t.get("is_active", True))
    ]


async def resolve_tenant_template(
    client, license_id: str, document_type: str,
) -> tuple[dict | None, dict | None]:
    """(template, version) the shop chose, or (None, None) for the built-in.

    Every Data tier failure is logged and treated as "no tenant template":
    a document that could not look up a layout still has one to render
    with, and refusing to issue it would be a worse answer than issuing
    the standard layout.
    """
    try:
        templates = await client.list_document_templates(
            license_id, document_type=document_type,
        )
    except Exception:
        log.exception("could not read %s templates; using the built-in", document_type)
        return None, None

    for template in choosable_templates(templates or []):
        try:
            versions = await client.list_document_template_versions(
                license_id, str(template["id"]),
            )
        except Exception:
            log.exception("could not read versions for template %s", template.get("id"))
            continue
        version = usable_version(versions or [])
        if version is None:
            continue
        return template, version
    return None, None
