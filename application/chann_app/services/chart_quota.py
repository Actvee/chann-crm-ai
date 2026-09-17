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
