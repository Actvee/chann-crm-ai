"""The only way this tier reaches persistent state.

Boundary rule (CLAUDE.md 4): the Application Tier must not import SQLAlchemy,
psycopg, or redis. It talks to the Data Tier over internal HTTP. The boundary
test in tests/boundary enforces this by inspecting imports, so the rule is
executable rather than aspirational.
"""
from __future__ import annotations

import httpx
from urllib.parse import quote

from .config import settings


class DataTierError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"data tier returned {status_code}: {detail[:200]}")
        self.status_code = status_code
        self.detail = detail[:200]


class DataClient:
    def __init__(self, base_url: str | None = None, secret: str | None = None,
                 client: httpx.AsyncClient | None = None):
        self._base = (base_url or settings.data_base_url).rstrip("/")
        self._secret = secret if secret is not None else settings.admin_secret
        self._client = client or httpx.AsyncClient(timeout=10.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self._secret, "Content-Type": "application/json"}

    def _headers_for(self, actor_id: str | None) -> dict[str, str]:
        headers = dict(self._headers)
        if actor_id:
            headers["X-Actor-Id"] = actor_id
        return headers

    async def write_audit_log(
        self,
        *,
        entity_type: str,
        entity_id: str,
        actor_type: str,
        action: str,
        license_id: str | None = None,
        actor_id: str | None = None,
        field_changes: dict | None = None,
        ai_reasoning: str | None = None,
        cross_tenant: bool = False,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/audit-log",
            headers=self._headers,
            json={
                "license_id": license_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "action": action,
                "field_changes": field_changes,
                "ai_reasoning": ai_reasoning,
                "cross_tenant": cross_tenant,
            },
        )
        return self._unwrap(resp)

    async def resolve_identity(self, line_user_id: str, primary_role: str,
                               display_name: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/identities/resolve",
            headers=self._headers,
            json={
                "line_user_id": line_user_id,
                "primary_role": primary_role,
                "display_name": display_name,
            },
        )
        return self._unwrap(resp)

    async def health(self) -> dict:
        resp = await self._client.get(f"{self._base}/health")
        return self._unwrap(resp)

    async def memberships_of(self, chann_uid: str, oa: str | None = None) -> list[dict]:
        params = {"oa": oa} if oa else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/memberships",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def get_member(self, license_id: str, chann_uid: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/members/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def authorization_context(self, license_id: str, chann_uid: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/authorization/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_roles(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/roles", headers=self._headers
        )
        return self._unwrap(resp)

    # ---------------------------------------------------------- Phase 10
    # Company identity as it appears on customer-facing documents.

    async def get_company_profile(self, license_id: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/company-profile",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def update_company_profile(
        self, license_id: str, payload: dict, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/company-profile",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def create_role(self, license_id: str, payload: dict, actor_id: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/roles",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def update_role(
        self, license_id: str, role_name: str, payload: dict, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/roles/{quote(role_name, safe='')}",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def delete_role(
        self, license_id: str, role_name: str, actor_id: str | None = None
    ) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}/roles/{quote(role_name, safe='')}",
            headers=self._headers_for(actor_id),
        )
        if resp.status_code not in (200, 204):
            self._unwrap(resp)

    async def set_member_role(
        self, license_id: str, chann_uid: str, role_name: str, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/members/{chann_uid}/role",
            headers=self._headers_for(actor_id),
            json={"role_name": role_name},
        )
        return self._unwrap(resp)

    async def list_license_settings(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/settings", headers=self._headers
        )
        return self._unwrap(resp)

    async def put_license_setting(
        self, license_id: str, key: str, value, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/licenses/{license_id}/settings/{quote(key, safe='')}",
            headers=self._headers_for(actor_id),
            json={"setting_value": value},
        )
        return self._unwrap(resp)

    async def delete_license_setting(
        self, license_id: str, key: str, actor_id: str | None = None
    ) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}/settings/{quote(key, safe='')}",
            headers=self._headers_for(actor_id),
        )
        if resp.status_code not in (200, 204):
            self._unwrap(resp)

    async def request_ownership_transfer(
        self, license_id: str, from_chann_uid: str, to_chann_uid: str
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/ownership-transfers",
            headers=self._headers,
            json={"from_chann_uid": from_chann_uid, "to_chann_uid": to_chann_uid},
        )
        return self._unwrap(resp)

    async def accept_ownership_transfer(
        self, license_id: str, transfer_id: str, accepting_chann_uid: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/ownership-transfers/{transfer_id}/accept",
            headers=self._headers_for(actor_id),
            json={"accepting_chann_uid": accepting_chann_uid},
        )
        return self._unwrap(resp)

    async def force_transfer_owner(
        self, license_id: str, target_chann_uid: str, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform/licenses/{license_id}/break-glass/transfer-owner",
            headers=self._headers_for(actor_id),
            json={"target_chann_uid": target_chann_uid},
        )
        return self._unwrap(resp)

    async def authenticate_platform_admin(self, username: str, password: str) -> dict | None:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform-admins/authenticate",
            headers=self._headers,
            json={"username": username, "password": password},
        )
        if resp.status_code == 401:
            return None
        return self._unwrap(resp)

    async def create_platform_admin_session(
        self, session_id: str, admin_id: str, ttl_s: int
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform-admin-sessions",
            headers=self._headers,
            json={"session_id": session_id, "admin_id": admin_id, "ttl_s": ttl_s},
        )
        return self._unwrap(resp)

    async def get_platform_admin_session(self, session_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform-admin-sessions/{session_id}",
            headers=self._headers,
        )
        if resp.status_code == 401:
            return None
        return self._unwrap(resp)

    async def delete_platform_admin_session(self, session_id: str) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/platform-admin-sessions/{session_id}",
            headers=self._headers,
        )
        if resp.status_code not in (200, 204):
            self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response):
        if resp.status_code >= 400:
            try:
                detail = str(resp.json().get("detail", resp.text))
            except Exception:
                detail = resp.text
            raise DataTierError(resp.status_code, detail)
        # 204 No Content is a valid, deliberate success response (every
        # endpoint that only stores/deletes state uses it) — by HTTP
        # definition its body is empty, so calling .json() on it always
        # raises JSONDecodeError. This crashed EVERY call to
        # set_pending_intent/clear_pending_intent/set_last_customer_ref
        # silently until the webhook-level exception logging added
        # alongside this fix made it visible for the first time.
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ Phase 6

    async def permission_catalog(self) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/permissions/catalog", headers=self._headers
        )
        return self._unwrap(resp)

    async def record_message_entity(
        self, license_id: str, message_id: str, entity_type: str, entity_id: str
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/message-entity-map",
            headers=self._headers,
            json={
                "message_id": message_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        )
        return self._unwrap(resp)

    async def get_message_entity(self, license_id: str, message_id: str) -> dict | None:
        """None for both 'no such mapping' and 'belongs to another tenant' —
        the Data tier deliberately does not distinguish them."""
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/message-entity-map/{quote(message_id, safe='')}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def create_notification(
        self,
        license_id: str,
        *,
        target_chann_uid: str,
        type: str,
        message: str,
        message_en: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        delivery_line: bool = True,
        delivery_dashboard: bool = True,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/notifications",
            headers=self._headers_for(actor_id),
            json={
                "target_chann_uid": target_chann_uid,
                "type": type,
                "message": message,
                "message_en": message_en,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "delivery_line": delivery_line,
                "delivery_dashboard": delivery_dashboard,
            },
        )
        return self._unwrap(resp)

    async def list_notifications(
        self, license_id: str, chann_uid: str, *, unread_only: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/members/{chann_uid}/notifications",
            headers=self._headers,
            params={"unread_only": str(unread_only).lower(), "limit": limit},
        )
        return self._unwrap(resp)

    async def notification_unread_count(self, license_id: str, chann_uid: str) -> int:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/members/{chann_uid}/notifications/unread-count",
            headers=self._headers,
        )
        return int(self._unwrap(resp)["unread_count"])

    async def mark_notification_read(
        self, license_id: str, chann_uid: str, notification_id: str
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/members/{chann_uid}/notifications/{notification_id}/read",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def announced_today(
        self, license_id: str, notification_type: str,
    ) -> set[str]:
        """entity_ids already notified about today, for one type."""
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/notifications/announced-today",
            headers=self._headers, params={"type": notification_type},
        )
        return set((self._unwrap(resp) or {}).get("entity_ids") or [])

    async def line_target_of(self, chann_uid: str) -> str | None:
        """The LINE user id to push to, or None if this person has none."""
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/line-target",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return (self._unwrap(resp) or {}).get("line_user_id")

    async def list_licenses(
        self, status: str | None = None, exclude_status: str | None = None,
    ) -> list[dict]:
        params: dict = {}
        if status:
            params["status"] = status
        if exclude_status:
            params["exclude_status"] = exclude_status
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses", headers=self._headers,
            params=params or None,
        )
        return self._unwrap(resp)

    async def list_follow_ups(
        self, license_id: str, status: str | None = None,
    ) -> list[dict]:
        params = {"status": status} if status else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/follow-ups",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def create_note(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/notes",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_notes(
        self, license_id: str, entity_type: str, entity_id: str, limit: int = 50,
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/notes",
            headers=self._headers,
            params={"entity_type": entity_type, "entity_id": entity_id, "limit": limit},
        )
        return self._unwrap(resp)

    async def create_follow_up(
        self, license_id: str, payload: dict, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/follow-ups",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def due_follow_ups(self, license_id: str, days: int = 1) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/follow-ups/due",
            headers=self._headers,
            params={"days": days},
        )
        return self._unwrap(resp)

    async def set_follow_up_status(
        self, license_id: str, follow_up_id: str, status: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/follow-ups/{follow_up_id}/status",
            headers=self._headers_for(actor_id),
            json={"status": status},
        )
        return self._unwrap(resp)

    # ---------------------------------------------------------- Phase 6.5

    async def create_license(
        self, *, company_name: str, created_by_chann_uid: str,
        display_name: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses",
            headers=self._headers,
            json={
                "company_name": company_name,
                "created_by_chann_uid": created_by_chann_uid,
                "display_name": display_name,
            },
        )
        return self._unwrap(resp)

    async def search_shops(self, q: str, limit: int = 10) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/public/shops",
            headers=self._headers,
            params={"q": q, "limit": limit},
        )
        return self._unwrap(resp)

    async def create_invite(
        self, license_id: str, payload: dict, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/invites",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def list_invites(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/invites",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def revoke_invite(
        self, license_id: str, invite_id: str, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/invites/{invite_id}/revoke",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def redeem_invite(
        self, *, invite_code: str, chann_uid: str, display_name: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/invites/redeem",
            headers=self._headers,
            json={
                "invite_code": invite_code,
                "chann_uid": chann_uid,
                "display_name": display_name,
            },
        )
        return self._unwrap(resp)

    async def link_customer(self, *, chann_uid: str, company_code: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/customer-links",
            headers=self._headers,
            json={"chann_uid": chann_uid, "company_code": company_code},
        )
        return self._unwrap(resp)

    async def my_shops(self, chann_uid: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/customers/{chann_uid}/shops",
            headers=self._headers,
        )
        return self._unwrap(resp)

    # ------------------------------------------------------------ Phase 7

    async def upsert_product(
        self, license_id: str, product_id: str, payload: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/products/{quote(product_id, safe='')}",
            headers=self._headers_for(actor_id),
            json=payload,
        )
        return self._unwrap(resp)

    async def list_products(
        self, license_id: str, *, category: str | None = None,
        include_archived: bool = False, limit: int = 200,
    ) -> list[dict]:
        params: dict = {"include_archived": str(include_archived).lower(), "limit": limit}
        if category:
            params["category"] = category
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/products",
            headers=self._headers,
            params=params,
        )
        return self._unwrap(resp)

    async def upload_products_csv(
        self, license_id: str, content: str, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/products/csv",
            headers=self._headers_for(actor_id),
            json={"content": content},
        )
        return self._unwrap(resp)

    async def archive_product(
        self, license_id: str, product_id: str, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/products/{quote(product_id, safe='')}",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def create_sales_group(self, license_id: str, group_name: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/sales-groups",
            headers=self._headers,
            json={"group_name": group_name},
        )
        return self._unwrap(resp)

    async def list_sales_groups(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/sales-groups",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def create_technician_team(self, license_id: str, team_name: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/technician-teams",
            headers=self._headers,
            json={"team_name": team_name},
        )
        return self._unwrap(resp)

    async def list_technician_teams(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/technician-teams",
            headers=self._headers,
        )
        return self._unwrap(resp)

    # ------------------------------------------------------------ Phase 8

    async def get_profile(self, chann_uid: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/profile",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def update_profile(
        self, chann_uid: str, fields: dict, actor_id: str | None = None
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/identities/{chann_uid}/profile",
            headers=self._headers_for(actor_id),
            json=fields,
        )
        return self._unwrap(resp)

    async def check_profile_edit(
        self, license_id: str, actor_chann_uid: str, target_chann_uid: str
    ) -> bool:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/profile-edit-check/{actor_chann_uid}/{target_chann_uid}",
            headers=self._headers,
        )
        return bool(self._unwrap(resp)["allowed"])

    # ------------------------------------------ Chat state (conversation continuity)

    async def set_pending_intent(
        self, chann_uid: str, oa: str, *, action: str, entity: str | None,
        fields: dict, missing: list[str], ttl_seconds: int = 600,
    ) -> None:
        resp = await self._client.put(
            f"{self._base}/internal/v1/chat/pending-intent/{oa}/{chann_uid}",
            headers=self._headers,
            json={
                "action": action, "entity": entity, "fields": fields,
                "missing": missing, "ttl_seconds": ttl_seconds,
            },
        )
        self._unwrap(resp)

    async def get_pending_intent(self, chann_uid: str, oa: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/chat/pending-intent/{oa}/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def clear_pending_intent(self, chann_uid: str, oa: str) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/chat/pending-intent/{oa}/{chann_uid}",
            headers=self._headers,
        )
        self._unwrap(resp)

    async def set_last_entity_ref(
        self, chann_uid: str, oa: str, *, entity_type: str, entity_id: str,
        code: str, ttl_seconds: int = 600,
    ) -> None:
        resp = await self._client.put(
            f"{self._base}/internal/v1/chat/last-entity/{oa}/{chann_uid}",
            headers=self._headers,
            json={
                "entity_type": entity_type, "entity_id": entity_id,
                "code": code, "ttl_seconds": ttl_seconds,
            },
        )
        self._unwrap(resp)

    async def get_last_entity_ref(self, chann_uid: str, oa: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/chat/last-entity/{oa}/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def set_last_customer_ref(
        self, chann_uid: str, oa: str, *, customer_id: str, name: str,
        ttl_seconds: int = 600,
    ) -> None:
        resp = await self._client.put(
            f"{self._base}/internal/v1/chat/last-customer/{oa}/{chann_uid}",
            headers=self._headers,
            json={"customer_id": customer_id, "name": name, "ttl_seconds": ttl_seconds},
        )
        self._unwrap(resp)

    async def get_last_customer_ref(self, chann_uid: str, oa: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/chat/last-customer/{oa}/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def set_smartbrowz_token(
        self, access_token: str, *, api_domain: str | None = None, ttl_seconds: int = 3300,
    ) -> None:
        resp = await self._client.put(
            f"{self._base}/internal/v1/chat/smartbrowz-token",
            headers=self._headers,
            json={"access_token": access_token, "api_domain": api_domain,
                  "ttl_seconds": ttl_seconds},
        )
        self._unwrap(resp)

    async def get_smartbrowz_token(self) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/chat/smartbrowz-token", headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def clear_smartbrowz_token(self) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/chat/smartbrowz-token", headers=self._headers,
        )
        self._unwrap(resp)

    # ------------------------------------------------------------ Phase 9 CRM

    async def create_customer(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/customers",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def get_customer(self, license_id: str, customer_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/{customer_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_customers(self, license_id: str, stage: str | None = None) -> list[dict]:
        params = {"stage": stage} if stage else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/customers",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def update_customer(
        self, license_id: str, customer_id: str, fields: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/{customer_id}",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def promote_customer(
        self, license_id: str, customer_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/{customer_id}/promote",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def archive_customer(
        self, license_id: str, customer_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/{customer_id}/archive",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def create_deal(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/deals",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def get_deal(self, license_id: str, deal_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_deals(self, license_id: str, stage: str | None = None) -> list[dict]:
        params = {"stage": stage} if stage else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/deals",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def add_deal_product(
        self, license_id: str, deal_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}/products",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def update_deal(
        self, license_id: str, deal_id: str, fields: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def remove_deal_product(
        self, license_id: str, deal_id: str, deal_product_id: str,
        actor_id: str | None = None,
    ) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/deals/{deal_id}/products/{deal_product_id}",
            headers=self._headers_for(actor_id),
        )
        self._unwrap(resp)

    async def transition_deal_stage(
        self, license_id: str, deal_id: str, stage: str, *,
        allow_reopen: bool = False, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}/stage",
            headers=self._headers_for(actor_id),
            params={"allow_reopen": allow_reopen}, json={"stage": stage},
        )
        return self._unwrap(resp)

    async def archive_deal(
        self, license_id: str, deal_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}/archive",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def storefront_search(self, q: str, limit: int = 10) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/public/storefront/products",
            headers=self._headers, params={"q": q, "limit": limit},
        )
        return self._unwrap(resp)

    async def storefront_record_interest(
        self, *, chann_uid: str, license_id: str, product_name: str,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/public/storefront/interest",
            headers=self._headers,
            json={"chann_uid": chann_uid, "license_id": license_id, "product_name": product_name},
        )
        return self._unwrap(resp)

    # ------------------------------------------------------------ Phase 10

    async def create_quote(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def get_quote(self, license_id: str, quote_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_quotes(self, license_id: str, status: str | None = None) -> list[dict]:
        params = {"status_": status} if status else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def transition_quote_status(
        self, license_id: str, quote_id: str, status: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/status",
            headers=self._headers_for(actor_id), json={"status": status},
        )
        return self._unwrap(resp)

    async def create_document_template(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/document-templates",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_document_templates(
        self, license_id: str, document_type: str | None = None,
    ) -> list[dict]:
        params = {"document_type": document_type} if document_type else None
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/document-templates",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def create_document_template_version(
        self, license_id: str, template_id: str, payload: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/document-templates/{template_id}/versions",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_document_template_versions(
        self, license_id: str, template_id: str,
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/document-templates/{template_id}/versions",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def preview_document_template_version(
        self, license_id: str, version_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/document-template-versions/{version_id}/preview",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def publish_document_template_version(
        self, license_id: str, version_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/document-template-versions/{version_id}/publish",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def archive_document_template_version(
        self, license_id: str, version_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/document-template-versions/{version_id}/archive",
            headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def link_quote_document(
        self, license_id: str, quote_id: str, document_id: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/document",
            headers=self._headers_for(actor_id), params={"document_id": document_id},
        )
        return self._unwrap(resp)

    async def record_generated_document(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/generated-documents",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)
