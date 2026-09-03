"""Application-tier tenant identity and permission boundary for Phase 2."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from ..auth.liff import LiffTokenInvalid, verify_id_token
from ..data_client import DataClient
from .identity import apply_active_tenant

# What a linked customer may do from the customer app. Mirrors
# chat.OA_ALLOWED_PERMISSION_KEYS["customer"]; kept here so the LIFF
# principal does not import the 9k-line chat module.
CUSTOMER_PERMISSION_KEYS = frozenset({
    "customer.read", "customer.update",
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

    @property
    def is_customer(self) -> bool:
        return self.audience == "customer"

    def require(self, permission_key: str) -> None:
        if permission_key not in self.permission_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission required: {permission_key}",
            )


async def resolve_tenant_principal(
    client: DataClient,
    x_liff_id_token: str = Header(default=""),
    x_liff_audience: str = Header(default="sales"),
    x_license_id: str = Header(default=""),
) -> TenantPrincipal:
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no tenant membership")
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="multiple tenant memberships; X-License-Id is required",
        )

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
        )

    context = await client.authorization_context(
        str(selected["license_id"]), identity["chann_uid"]
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
    )
