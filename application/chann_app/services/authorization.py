"""Application-tier tenant identity and permission boundary for Phase 2."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from ..auth.liff import LiffTokenInvalid, verify_id_token
from ..data_client import DataClient
from .identity import apply_active_tenant, member_channel

# Methods that read. Anything else against a suspended tenant is refused
# before the route runs (review C4, 6 Sep 2026): chat already treated a
# suspended shop as read-only, the LIFF pages did not.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# What a linked customer may do from the customer app. Mirrors
# chat.OA_ALLOWED_PERMISSION_KEYS["customer"]; kept here so the LIFF
# principal does not import the 9k-line chat module.
#: What a linked customer can do in a shop they are linked to. customer.read
#: is their own history and their own row, never the shop's list.
#:
#: customer.update was in this set and nothing in the customer app ever used
#: it — their own details are edited through /api/liff/{audience}/profile.
#: What it did do was open PATCH /customers/{id} and POST /promote on EVERY
#: row in the shop (reproduced 11 ก.ย. 2569). Both routes now refuse a
#: customer principal outright; this removes the grant that made them
#: reachable at all.
CUSTOMER_PERMISSION_KEYS = frozenset({
    "customer.read",
    "ticket.create", "ticket.read",
    "warranty.read", "warranty.create",
})
from .identity import OA_TO_ROLE


@dataclass(frozen=True)
class TenantPrincipal:
    license_id: str
    chann_uid: str
    role: str
    is_owner: bool
    permission_keys: frozenset[str]
    # Which OA's app is calling — a customer's permission set is fixed
    # (below) and their reads must be scoped to their own records.
    audience: str = "sales"
    # Phase 18: "suspended" means read-only. Carried so a route that must
    # decide for itself (a write disguised as a GET) can, and so the
    # permissions endpoint can tell the page.
    license_status: str = "active"

    @property
    def is_customer(self) -> bool:
        return self.audience == "customer"

    @property
    def is_suspended(self) -> bool:
        return self.license_status == "suspended"

    def require_any(self, *permission_keys: str) -> None:
        """Any one of several keys. For routes whose natural key was added
        to the catalogue after roles were already built on the broader one
        (ticket.assign next to ticket.update) — the old grant keeps
        working, the new one now means something."""
        if not any(key in self.permission_keys for key in permission_keys):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission required: {' or '.join(permission_keys)}",
            )

    def require(self, permission_key: str) -> None:
        if permission_key not in self.permission_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission required: {permission_key}",
            )


def refuse_if_suspended(license_status: str | None, method: str | None) -> None:
    """A suspended shop is read-only: 423 Locked with a body the pages can
    translate, for any non-read method."""
    if (license_status or "active") != "suspended":
        return
    if method is None or method.upper() in READ_METHODS:
        return
    raise HTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail={"error": "tenant_suspended"},
    )


async def resolve_tenant_principal(
    client: DataClient,
    x_liff_id_token: str = Header(default=""),
    x_liff_audience: str = Header(default="sales"),
    x_license_id: str = Header(default=""),
    method: str | None = None,
) -> TenantPrincipal:
    """`method` is the HTTP method of the request being authorised; passing
    it makes a suspended tenant read-only here, once, rather than in
    every route."""
    if x_liff_audience not in OA_TO_ROLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid LIFF audience")
    try:
        claims = await verify_id_token(x_liff_id_token, x_liff_audience)
    except LiffTokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    identity = await client.resolve_identity(claims["sub"], OA_TO_ROLE[x_liff_audience])
    # oa-scoped, as chat's resolve_context has always been: on the Customer
    # OA the "memberships" are the shops this person is a customer of,
    # not the companies they work for. Without the scope, an account that
    # is staff somewhere opened the customer app inside the STAFF company
    # (owner walk, 3 Sep) and saw "ลูกค้าของ ร้านทดสอบ" with nothing in it.
    memberships = await client.memberships_of(identity["chann_uid"], oa=x_liff_audience)
    memberships, _alternatives = await apply_active_tenant(
        client, identity["chann_uid"], x_liff_audience, memberships,
    )
    if x_license_id:
        selected = next(
            (row for row in memberships if str(row["license_id"]) == x_license_id), None
        )
        if selected is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a tenant member")
    elif len(memberships) == 1:
        selected = memberships[0]
    elif not memberships:
        if x_liff_audience == "customer":
            # A customer who has not linked a shop yet. They may still use
            # the platform-wide storefront ("ค้นหา …", "สนใจ") and their own
            # profile — the home screen used to 401 on every button and say
            # nothing about linking a shop (review D5, 6 Sep 2026). No
            # license: every tenant-scoped route refuses them at
            # _require_same_tenant, exactly as before.
            return TenantPrincipal(
                license_id="",
                chann_uid=identity["chann_uid"],
                role="customer",
                is_owner=False,
                permission_keys=CUSTOMER_PERMISSION_KEYS,
                audience="customer",
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no tenant membership")
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="multiple tenant memberships; X-License-Id is required",
        )

    license_status = str(selected.get("license_status") or "active")
    refuse_if_suspended(license_status, method)

    if x_liff_audience == "customer":
        # A customer is linked through customer_license_links and has no
        # license_members row by design (Phase 6.5), so the members-based
        # authorization lookup below can only 404. Their capabilities are
        # the fixed customer set, the same one chat's customer branch and
        # OA_ALLOWED_PERMISSION_KEYS describe; every route reading with
        # this principal must scope to principal.chann_uid.
        return TenantPrincipal(
            license_id=str(selected["license_id"]),
            chann_uid=identity["chann_uid"],
            role="customer",
            is_owner=False,
            permission_keys=CUSTOMER_PERMISSION_KEYS,
            audience="customer",
            license_status=license_status,
        )

    # The row of the channel in use: the technician app reads the
    # technician row, the sales dashboard the sales row (owner, 8 Sep 2026).
    context = await client.authorization_context(
        str(selected["license_id"]), identity["chann_uid"],
        channel=member_channel(x_liff_audience),
    )
    if context is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="inactive tenant member")
    return TenantPrincipal(
        license_id=str(selected["license_id"]),
        chann_uid=identity["chann_uid"],
        role=context["role"],
        is_owner=bool(context["is_owner"]),
        permission_keys=frozenset(context["permission_keys"]),
        audience=x_liff_audience,
        license_status=license_status,
    )
