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

from dataclasses import dataclass
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
    )
