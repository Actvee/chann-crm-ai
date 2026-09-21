"""Chann Identity resolution and tenant selection (Master Spec 1.6).

The flow the spec mandates:

    line_user_id -> chann_identities
        miss -> create, primary_role from the OA that was messaged
        hit  -> reuse
    chann_uid -> license_members
        exactly one active tenant  -> use it
        several                    -> ask the user to choose (full UX in Phase 16)
        none                       -> guide them through registration (Phase 8)
"""
from __future__ import annotations

import logging

from dataclasses import dataclass, field
from enum import Enum

from ..data_client import DataClient


class TenantResolution(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    NONE = "none"


@dataclass
class ResolvedContext:
    chann_uid: str
    primary_role: str
    display_name: str | None
    resolution: TenantResolution
    memberships: list[dict]
    # The OA THIS message actually arrived on — ground truth for the current
    # conversation. Deliberately separate from primary_role: an identity is
    # global (one row per line_user_id) and primary_role is fixed at first
    # contact, so it goes stale the moment the same LINE account later
    # messages a DIFFERENT OA. LINE issues the same user ID to one physical
    # account across every channel under one provider, which is exactly how
    # this project's three OAs are set up. Anything deciding "what does THIS
    # message's channel allow" must read oa, never primary_role.
    oa: str = ""
    # The person's OTHER companies on this OA, when a stored choice made
    # one of several the active one (3 Sep). Lets the chat offer
    # "เปลี่ยนร้าน" without a second lookup; empty for the ordinary case.
    alternatives: list[dict] = field(default_factory=list)

    @property
    def license_id(self) -> str | None:
        """Only meaningful when exactly one tenant matched. Returning None for
        the ambiguous case is deliberate — picking the first membership would
        silently write a record into the wrong company."""
        if self.resolution is TenantResolution.SINGLE:
            return self.memberships[0]["license_id"]
        return None


# The OA a message arrives on determines the identity's primary role.
OA_TO_ROLE = {
    "customer": "customer",
    "sales": "sales",
    "technician": "technician",
}


def member_channel(oa: str | None) -> str:
    """Which license_members channel an OA acts through (owner, 8 Sep
    2026): the Technician OA reads the "technician" row, the Sales/CS OA
    the "sales" row. The Customer OA has no members row; callers on it
    that ask anyway get the sales answer, which is "not a member"."""
    return "technician" if oa == "technician" else "sales"


async def resolve_context(client: DataClient, oa: str, line_user_id: str,
                          display_name: str | None = None) -> ResolvedContext:
    primary_role = OA_TO_ROLE[oa]
    identity = await client.resolve_identity(line_user_id, primary_role, display_name)
    # oa-scoped: a person can hold a real membership at Company X as Sales
    # staff and simultaneously have never been onboarded as Company X's
    # customer or technician — the three OAs are three different personas
    # that happen to share one LINE userId. See
    # MemberRepository.memberships_of for the full reasoning.
    memberships = await client.memberships_of(identity["chann_uid"], oa=oa)
    memberships, alternatives = await apply_active_tenant(
        client, identity["chann_uid"], oa, memberships,
    )

    if len(memberships) == 1:
        resolution = TenantResolution.SINGLE
    elif len(memberships) > 1:
        resolution = TenantResolution.MULTIPLE
    else:
        resolution = TenantResolution.NONE

    return ResolvedContext(
        chann_uid=identity["chann_uid"],
        primary_role=identity["primary_role"],
        display_name=identity.get("display_name"),
        resolution=resolution,
        memberships=memberships,
        oa=oa,
        alternatives=alternatives,
    )


log = logging.getLogger(__name__)


async def ensure_display_name(
    client: DataClient, ctx: ResolvedContext, *, oa: str, line_user_id: str, fetch=None,
) -> ResolvedContext:
    """Fill a nameless identity with what LINE calls the person.

    Asked once per person: the name is stored on the identity, so the
    next message finds it there and LINE is not asked again. Every
    failure — no token, LINE down, the person not a friend — leaves the
    context exactly as it was; the message goes on without the name.
    `fetch` is the LINE call, injectable for tests."""
    # getattr, not ctx.display_name: nothing on this road may raise past
    # the webhook, and a context built elsewhere may not carry the field.
    if getattr(ctx, "display_name", None):
        return ctx
    try:
        from ..line.client import get_profile_name

        name = await (fetch or get_profile_name)(oa, line_user_id)
        if not name:
            return ctx
        await client.set_identity_display_name(ctx.chann_uid, name)
        ctx.display_name = name
    except Exception:  # noqa: BLE001
        log.exception("could not store the LINE display name for %s", getattr(ctx, "chann_uid", "?"))
    return ctx


async def apply_active_tenant(
    client: DataClient, chann_uid: str, oa: str, memberships: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Narrow several memberships to the one the person chose on this OA.

    Owner walk, 3 Sep: one LINE account that is staff at ร้านทดสอบ and a
    customer of Dev Company got the staff shop on the Customer OA's home
    and "บัญชีนี้ดูแลหลายร้าน" in chat, with no way to say which. The
    stored choice (Data Tier Redis, k_active_tenant) is honoured only when
    it is still one of the memberships — a revoked link can never keep
    someone acting in a shop they left. Returns (memberships, alternatives):
    the chosen one alone, and the rest for "เปลี่ยนร้าน".
    """
    if len(memberships) < 2:
        return memberships, []
    try:
        active = await client.get_active_tenant(chann_uid, oa)
    except Exception:  # noqa: BLE001 — a cache miss must degrade to "which shop?"
        log.warning("could not read the active tenant for %s on %s", chann_uid, oa)
        active = None
    if not active:
        return memberships, []
    chosen = [m for m in memberships if str(m.get("license_id")) == str(active)]
    if not chosen:
        return memberships, []
    return chosen, [m for m in memberships if str(m.get("license_id")) != str(active)]
