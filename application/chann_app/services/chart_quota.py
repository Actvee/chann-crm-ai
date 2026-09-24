"""How many AI-drawn charts a company may have in a month.

Owner, 17 ก.ย. 2569: "เรื่องกราฟอยากให้มีอิสระสามารถ generate กราฟจากระบบได้
เลย แต่ทำเป็นระบบ quota ก็ได้ว่าจะใช้งานกราฟแบบสร้างเองได้กี่ครั้งของบริษัท
นั้นๆ".

Only the AI-drawn picture is metered. The four fixed sales charts cost no
model call at all and stay free however often they are asked for, and so
does every report in words — running out of chart quota must never stop a
shop finding out its own numbers.

The allowance lives in `license_settings.ai_chart_quota`, which only the
Chann administrator edits; the counter lives beside it and is incremented
inside the Data tier's own transaction, so two people asking at the same
moment cannot both spend the same one.
"""
from __future__ import annotations

import logging

from ..data_client import DataClient
from .thai_datetime import local_today

log = logging.getLogger(__name__)

DEFAULT_QUOTA = 30


def this_month() -> str:
    return local_today().strftime("%Y-%m")


async def spend_one(client: DataClient, *, license_id: str) -> dict:
    """{"allowed", "used", "allowance", "month"}.

    A Data tier that cannot answer must not cost the shop its picture: the
    quota exists to cap a cost, and failing open on an outage is the
    cheaper mistake of the two.
    """
    try:
        out = await client.consume_ai_chart_quota(str(license_id), this_month())
    except Exception:  # noqa: BLE001
        log.exception("could not read the AI chart quota for %s", license_id)
        return {"allowed": True, "used": 0, "allowance": DEFAULT_QUOTA,
                "month": this_month(), "unknown": True}
    return dict(out or {"allowed": True, "used": 0, "allowance": DEFAULT_QUOTA})


def receipt(quota: dict, *, charged_for: str | None = None) -> dict:
    """What a spend looked like, in the shape both surfaces report.

    `charged_for` is "picture" or "question" (final review I2): the one
    credit pays for either, and a surface that cannot tell them apart
    labels a words-only answer "this chart was drawn by AI", or tells a
    shop over its allowance that a picture it never asked for was
    withheld."""
    out = {
        "allowed": bool(quota.get("allowed")), "used": int(quota.get("used") or 0),
        "allowance": int(quota.get("allowance") or 0), "unknown": bool(quota.get("unknown")),
    }
    if charged_for:
        out["charged_for"] = charged_for
    return out


async def charge_for_the_question(client: DataClient, *, license_id: str, out: dict) -> dict:
    """Round 21C: an ad-hoc question costs a credit too, because it costs a
    model call (owner, 23 ก.ย. 2569). The one rule behind both the
    dashboard (`routers_phase2._charge_for_the_question`) and the LINE road
    (`chat._handle_ai_report`), so the two cannot charge differently.

    A clarifying question costs nothing — nothing was answered — and nor
    does a refusal, which has no result. The five fixed reports never come
    here at all, which is what makes charging this road fair: a shop that
    has run out can still get its five numbers (spec §5).

    A request that drew a picture spends one credit, not two: the picture
    charge runs first and leaves its receipt in `out["quota"]`, so this one
    steps aside when it sees it. Over the allowance nothing is withheld —
    the answer has already been computed; the credit only counts it.
    """
    if out.get("clarify") or not out.get("result"):
        return out
    if out.get("quota"):
        return out
    out["quota"] = receipt(await spend_one(client, license_id=license_id), charged_for="question")
    return out
