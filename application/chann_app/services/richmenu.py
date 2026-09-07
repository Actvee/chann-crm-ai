"""Which rich menu a person sees — Phase 19 / review E10 (6 Sep 2026).

The apply script (scripts/richmenu/richmenu-apply.sh) publishes, per OA,
a Thai pair of pages aliased chann-<oa>-main / chann-<oa>-more and an
English pair aliased chann-<oa>-main-en / chann-<oa>-more-en, and sets
the Thai main page as the OA's default. A default is all anyone ever
got: switching to English changed the replies and left the buttons in
Thai. This module links the person to the page for their language.

Best effort by design: LINE being down, a menu not yet applied, an
identity without a LINE user id — none of that may break the reply that
triggered the sync. The function logs and returns False.

Call sites:
  * registration.py — once a person has joined / linked / created.
  * chat.py's language switch (the chat stream wires this):
        await sync_rich_menu(client, oa=ctx.oa, chann_uid=ctx.chann_uid, language=language)
"""
from __future__ import annotations

import logging

from ..data_client import DataClient
from ..line.client import link_rich_menu

log = logging.getLogger(__name__)

PAGES = ("main", "more")
LANGUAGE_SUFFIX = {"th": "", "en": "-en"}


def rich_menu_alias(oa: str, language: str | None = None, page: str = "main") -> str:
    """The alias the apply script gave this OA's page in this language."""
    if page not in PAGES:
        raise ValueError(f"unknown rich menu page {page!r}")
    lang = "en" if str(language or "").lower().startswith("en") else "th"
    return f"chann-{oa}-{page}{LANGUAGE_SUFFIX[lang]}"


async def sync_rich_menu(
    client: DataClient, *, oa: str, chann_uid: str, language: str | None = None,
    line_user_id: str | None = None, http_client=None,
) -> bool:
    """Link the person to the main page for their language on this OA.

    `language` None means "whatever they chose" (display preferences).
    `line_user_id` is looked up from the identity when not given.
    Returns True when LINE accepted the link; False (and a log line)
    for every kind of failure.
    """
    if oa not in ("sales", "technician", "customer"):
        log.warning("rich menu sync skipped: unknown OA %r", oa)
        return False
    try:
        if language is None:
            prefs = await client.get_display_preferences(chann_uid) or {}
            language = str(prefs.get("language") or "th")
        if not line_user_id:
            line_user_id = await client.line_target_of(chann_uid)
        if not line_user_id:
            log.info("rich menu sync skipped: %s has no LINE user id", chann_uid)
            return False
        alias = rich_menu_alias(oa, language)
        rich_menu_id = await link_rich_menu(oa, line_user_id, alias, client=http_client)
        log.info("rich menu %s (%s) linked for %s on %s", alias, rich_menu_id, chann_uid, oa)
        return True
    except Exception as exc:  # noqa: BLE001 — never in the way of a reply
        log.warning("rich menu sync failed for %s on %s: %s", chann_uid, oa, exc)
        return False
