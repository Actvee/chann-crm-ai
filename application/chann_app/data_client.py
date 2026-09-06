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
    def __init__(self, status_code: int, detail, structured: dict | None = None):
        text = str(detail)
        super().__init__(f"data tier returned {status_code}: {text[:200]}")
        self.status_code = status_code
        self.detail = text[:200]
        # The original object when the Data tier sent one. Some refusals
        # carry data the caller must act on — the dispatch gate replies
        # with WHICH fields are missing, and str()-ing that into
        # "{'missing': [...]}" would leave the caller parsing a repr.
        self.structured = structured


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

    async def record_webhook_event(self, event_id: str, oa: str) -> bool:
        """True the first time this LINE event id is seen; False on a
        redelivery. Errors count as "new" — dropping a real message is
        worse than a rare duplicate."""
        try:
            resp = await self._client.post(
                f"{self._base}/internal/v1/webhook-events",
                headers=self._headers, json={"event_id": event_id, "oa": oa},
            )
        except Exception:  # noqa: BLE001
            return True
        if resp.status_code == 409:
            return False
        return True

    async def set_customer_owner(self, license_id: str, customer_id: str, owner_member_id: str | None, actor_id: str | None = None) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/{customer_id}/owner",
            headers=self._headers_for(actor_id), json={"owner_member_id": owner_member_id},
        )
        return self._unwrap(resp)

    async def set_deal_owner(self, license_id: str, deal_id: str, owner_member_id: str | None, actor_id: str | None = None) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}/owner",
            headers=self._headers_for(actor_id), json={"owner_member_id": owner_member_id},
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
            structured = None
            try:
                raw = resp.json().get("detail", resp.text)
                if isinstance(raw, dict):
                    structured = raw
                detail = str(raw)
            except Exception:
                detail = resp.text
            raise DataTierError(resp.status_code, detail, structured)
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

    async def register_warranty(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/warranties",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_warranties(
        self, license_id: str, serial_number: str | None = None,
        customer_chann_uid: str | None = None,
    ) -> list[dict]:
        params = {}
        if serial_number:
            params["serial_number"] = serial_number
        if customer_chann_uid:
            params["customer_chann_uid"] = customer_chann_uid
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/warranties",
            headers=self._headers, params=params or None,
        )
        return self._unwrap(resp)

    async def lookup_serial(
        self, serial_number: str, actor_chann_uid: str = "",
    ) -> dict:
        """Which shops registered this serial (16.4).

        Not license-scoped, unlike every other call here — that is the
        point. The Data tier audits it with cross_tenant=true.
        """
        resp = await self._client.get(
            f"{self._base}/internal/v1/warranties/lookup",
            headers=self._headers,
            params={"serial_number": serial_number, "actor_chann_uid": actor_chann_uid},
        )
        return self._unwrap(resp)

    async def create_ticket(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def list_team_members(self, license_id: str, team_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/technician-teams/{team_id}/members",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def set_ticket_status(
        self, license_id: str, ticket_id: str, status: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/status",
            headers=self._headers_for(actor_id), json={"status": status},
        )
        return self._unwrap(resp)

    async def get_ticket(self, license_id: str, ticket_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_members(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/members",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def list_tickets(
        self, license_id: str, status: str | None = None,
        visible_to: str | None = None,
    ) -> list[dict]:
        params: dict = {}
        if status:
            params["status"] = status
        if visible_to:
            params["visible_to"] = visible_to
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets",
            headers=self._headers, params=params or None,
        )
        return self._unwrap(resp)

    async def update_ticket(
        self, license_id: str, ticket_id: str, fields: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def ticket_dispatch_check(self, license_id: str, ticket_id: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/tickets/{ticket_id}/dispatch-check",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def assign_ticket(
        self, license_id: str, ticket_id: str, *, target_type: str, target_ref: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/assign",
            headers=self._headers_for(actor_id),
            json={"target_type": target_type, "target_ref": target_ref},
        )
        return self._unwrap(resp)

    async def claim_ticket(
        self, license_id: str, ticket_id: str, member_id: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/claim",
            headers=self._headers_for(actor_id), json={"member_id": member_id},
        )
        return self._unwrap(resp)

    async def check_in_ticket(
        self, license_id: str, ticket_id: str, *, member_id: str,
        gps_lat: float | None = None, gps_lng: float | None = None,
        photo_url: str | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/tickets/{ticket_id}/check-in",
            headers=self._headers_for(actor_id),
            json={
                "member_id": member_id, "gps_lat": gps_lat,
                "gps_lng": gps_lng, "photo_url": photo_url,
            },
        )
        return self._unwrap(resp)

    async def check_out_ticket(
        self, license_id: str, ticket_id: str, *, member_id: str, report_data: dict,
        gps_lat: float | None = None, gps_lng: float | None = None,
        photo_url: str | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/tickets/{ticket_id}/check-out",
            headers=self._headers_for(actor_id),
            json={
                "member_id": member_id, "report_data": report_data,
                "gps_lat": gps_lat, "gps_lng": gps_lng, "photo_url": photo_url,
            },
        )
        return self._unwrap(resp)

    async def set_service_report_status(
        self, license_id: str, report_id: str, status: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/service-reports/{report_id}/status",
            headers=self._headers_for(actor_id), json={"status": status},
        )
        return self._unwrap(resp)

    async def list_service_reports(
        self, license_id: str, status: str | None = None,
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/service-reports",
            headers=self._headers, params={"status": status} if status else None,
        )
        return self._unwrap(resp)

    async def add_ticket_photo(
        self, license_id: str, ticket_id: str, payload: dict,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/photos",
            headers=self._headers, json=payload,
        )
        return self._unwrap(resp)

    async def get_assignment_rules(self, license_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/assignment-rules",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def upsert_assignment_rule(
        self, license_id: str, scope: str, rules_json: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/licenses/{license_id}/assignment-rules",
            headers=self._headers_for(actor_id),
            json={"scope": scope, "rules_json": rules_json},
        )
        return self._unwrap(resp)

    async def execute_assignment(
        self, license_id: str, *, scope: str, entity_type: str, entity_id: str,
        context: dict | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/assignment-rules/execute",
            headers=self._headers_for(actor_id),
            json={
                "scope": scope, "entity_type": entity_type,
                "entity_id": entity_id, "context": context or {},
            },
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


    # --------------------------------------------------------------- Phase 18

    # --------------------------------------------------------------- Phase 17

    # ---------------------------------------------------- user review (4 Sep 2026)
    async def archive_inactive_leads(self, license_id: str, days: int, actor_id: str | None = None) -> list[dict]:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/customers/archive-inactive-leads",
            json={"days": int(days)}, headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def run_report_query(self, license_id: str, spec: dict, actor_id: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/reports/query",
            json=spec, headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def platform_tenants(self, *, q: str | None = None, status: str | None = None) -> list[dict]:
        params = {k: v for k, v in (("q", q), ("status", status)) if v}
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/tenants", params=params or None, headers=self._headers,
        )
        return self._unwrap(resp)

    async def platform_tenant(self, license_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/tenants/{license_id}", headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def set_license_status(self, license_id: str, status: str, actor_id: str | None = None) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/status",
            json={"status": status}, headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def platform_audit(
        self, *, cross_tenant: bool | None = None, license_id: str | None = None,
        actor_type: str | None = None, action: str | None = None, limit: int = 100,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if cross_tenant is not None:
            params["cross_tenant"] = "true" if cross_tenant else "false"
        for key, value in (("license_id", license_id), ("actor_type", actor_type), ("action", action)):
            if value:
                params[key] = value
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/audit", params=params, headers=self._headers,
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

    async def update_note(
        self, license_id: str, note_id: str, body: str, actor_id: str | None = None,
    ) -> dict:
        """Rewrite a note's text.

        The Data Tier has had PATCH/DELETE on notes since Phase 6 (and
        keeps the old text in the audit entry, because editing a note
        rewrites a record other people may have acted on). Nothing ever
        called them: a note could be written and never corrected, in chat
        or on the dashboard.
        """
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/notes/{note_id}",
            headers=self._headers_for(actor_id), json={"body": body},
        )
        return self._unwrap(resp)

    async def delete_note(
        self, license_id: str, note_id: str, actor_id: str | None = None,
    ) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}/notes/{note_id}",
            headers=self._headers_for(actor_id),
        )
        if resp.status_code not in (204, 200):
            self._unwrap(resp)

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

    async def delete_technician_team(self, license_id: str, team_id: str) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}/technician-teams/{team_id}",
            headers=self._headers,
        )
        self._unwrap(resp)

    async def add_team_member(
        self, license_id: str, team_id: str, member_id: str, *, is_lead: bool = False,
    ) -> dict:
        """Idempotent on the Data Tier: re-adding updates is_lead."""
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/technician-teams/{team_id}/members",
            headers=self._headers, json={"member_id": member_id, "is_lead": is_lead},
        )
        return self._unwrap(resp)

    async def remove_team_member(self, license_id: str, team_id: str, member_id: str) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}/technician-teams/{team_id}/members/{member_id}",
            headers=self._headers,
        )
        self._unwrap(resp)

    async def reject_ticket(
        self, license_id: str, ticket_id: str, member_id: str, actor_id: str | None = None,
    ) -> dict:
        """12.4: the assignee declines; the ticket goes back to the
        dispatcher's queue, never auto-reassigned."""
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/reject",
            headers=self._headers_for(actor_id), json={"member_id": member_id},
        )
        return self._unwrap(resp)

    async def attach_report_document(
        self, license_id: str, report_id: str, *, document_id: str, pdf_path: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/service-reports/{report_id}/document",
            headers=self._headers_for(actor_id),
            params={"document_id": document_id, "pdf_path": pdf_path},
        )
        return self._unwrap(resp)

    async def list_ticket_photos(self, license_id: str, ticket_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/tickets/{ticket_id}/photos",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def set_identity_signature(self, chann_uid: str, signature_url: str) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/identities/{chann_uid}/signature",
            headers=self._headers, json={"signature_url": signature_url},
        )
        return self._unwrap(resp)

    async def identity_signature(self, chann_uid: str) -> str | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/signature",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp).get("signature_url") or None

    # ------------------------------------------ Phase 16 display preferences

    async def get_display_preferences(self, chann_uid: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/display-preferences",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def set_display_preferences(self, chann_uid: str, fields: dict) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/identities/{chann_uid}/display-preferences",
            headers=self._headers, json=fields,
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


    # ----------------------------------------------------------- Phase 16.5
    async def get_consent(self, chann_uid: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/consent", headers=self._headers,
        )
        return self._unwrap(resp)

    async def put_consent(self, chann_uid: str, version: str) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/identities/{chann_uid}/consent",
            json={"version": version}, headers=self._headers,
        )
        return self._unwrap(resp)

    async def create_pdpa_request(self, *, chann_uid: str, request_type: str, requested_via: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform/pdpa/requests",
            json={"chann_uid": chann_uid, "request_type": request_type, "requested_via": requested_via},
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def process_pdpa_request(self, request_id: str, processed_by: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform/pdpa/requests/{request_id}/process",
            json={"processed_by": processed_by}, headers=self._headers,
        )
        return self._unwrap(resp)

    async def reject_pdpa_request(self, request_id: str, *, reason: str, processed_by: str | None = None) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform/pdpa/requests/{request_id}/reject",
            json={"reason": reason, "processed_by": processed_by}, headers=self._headers,
        )
        return self._unwrap(resp)

    async def list_pdpa_requests(self, *, status: str | None = None, chann_uid: str | None = None) -> list[dict]:
        params = {k: v for k, v in (("status", status), ("chann_uid", chann_uid)) if v}
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/pdpa/requests", params=params, headers=self._headers,
        )
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

    # ------------------------------------------------ active tenant (3 Sep)

    async def get_active_tenant(self, chann_uid: str, oa: str) -> str | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/chat/active-tenant/{oa}/{chann_uid}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return str(self._unwrap(resp).get("license_id") or "") or None

    async def set_active_tenant(self, chann_uid: str, oa: str, license_id: str) -> None:
        resp = await self._client.put(
            f"{self._base}/internal/v1/chat/active-tenant/{oa}/{chann_uid}",
            headers=self._headers, json={"license_id": str(license_id)},
        )
        self._unwrap(resp)

    async def claim_warranty(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        """Attach a customer to a unit the shop registered (404 unknown
        here, 409 held by another customer)."""
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/warranties/claim",
            headers=self._headers_for(actor_id), json=payload,
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

    async def list_customers(
        self, license_id: str, stage: str | None = None, customer_chann_uid: str | None = None,
    ) -> list[dict]:
        params = {}
        if stage:
            params["stage"] = stage
        if customer_chann_uid:
            params["customer_chann_uid"] = customer_chann_uid
        params = params or None
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

    async def list_deals(
        self, license_id: str, stage: str | None = None, contact_id: str | None = None,
    ) -> list[dict]:
        params = {}
        if stage:
            params["stage"] = stage
        if contact_id:
            params["contact_id"] = contact_id
        params = params or None
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
        lost_reason: str | None = None,
    ) -> dict:
        body: dict = {"stage": stage}
        if lost_reason:
            body["lost_reason"] = lost_reason
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/deals/{deal_id}/stage",
            headers=self._headers_for(actor_id),
            params={"allow_reopen": allow_reopen}, json=body,
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

    async def storefront_browse(self, limit: int = 20) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/public/storefront/products",
            headers=self._headers, params={"limit": limit},
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

    # ------------------------------------------------------------ Phase 15

    async def open_chat_session(
        self, license_id: str, *, customer_chann_uid: str, product_id: str | None = None,
        sla_minutes: int = 30, timeout_minutes: int = 60, actor_id: str | None = None,
    ) -> dict:
        """The customer's live conversation with this shop. The returned
        dict carries `_created` (True when this call opened it)."""
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions",
            headers=self._headers_for(actor_id),
            json={
                "customer_chann_uid": customer_chann_uid, "product_id": product_id,
                "sla_minutes": sla_minutes, "timeout_minutes": timeout_minutes,
            },
        )
        row = self._unwrap(resp)
        row["_created"] = resp.status_code == 201
        return row

    async def list_chat_sessions(
        self, license_id: str, status: str | None = None, customer_chann_uid: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        if customer_chann_uid:
            params["customer_chann_uid"] = customer_chann_uid
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def get_chat_session(self, license_id: str, session_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def list_chat_messages(
        self, license_id: str, session_id: str, since: str | None = None, limit: int = 200,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if since:
            params["since"] = since
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}/messages",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp)

    async def add_chat_message(
        self, license_id: str, session_id: str, *, sender_type: str, content: str,
        sender_chann_uid: str | None = None, content_en: str | None = None,
        sla_minutes: int = 30, timeout_minutes: int = 60,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}/messages",
            headers=self._headers,
            json={
                "sender_type": sender_type, "content": content,
                "sender_chann_uid": sender_chann_uid, "content_en": content_en,
                "sla_minutes": sla_minutes, "timeout_minutes": timeout_minutes,
            },
        )
        return self._unwrap(resp)

    async def assign_chat_session(
        self, license_id: str, session_id: str, member_id: str, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}/assign",
            headers=self._headers_for(actor_id), json={"member_id": member_id},
        )
        return self._unwrap(resp)

    async def close_chat_session(
        self, license_id: str, session_id: str, actor_id: str | None = None,
        status: str = "closed",
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}/close",
            headers=self._headers_for(actor_id), params={"status": status},
        )
        return self._unwrap(resp)

    async def mark_chat_read(self, license_id: str, session_id: str, reader: str = "agent") -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/chat-sessions/{session_id}/read",
            headers=self._headers, params={"reader": reader},
        )
        return self._unwrap(resp)

    async def sweep_chat_sessions(self) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/platform/chat-sessions/sweep", headers=self._headers,
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

    async def update_deal_product(
        self, license_id: str, deal_id: str, line_id: str, fields: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/deals/{deal_id}/products/{line_id}",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def set_quote_terms(
        self, license_id: str, quote_id: str, fields: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/terms",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def set_quote_status(
        self, license_id: str, quote_id: str, status: str,
        actor_id: str | None = None,
    ) -> dict:
        # POST, not PATCH: the Data Tier models this as a state transition
        # rather than a field edit, and it rejects PATCH outright. Caught by
        # cross-checking every client call against the real routes — the
        # button would have returned 405 on its first press in production.
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/status",
            headers=self._headers_for(actor_id), json={"status": status},
        )
        return self._unwrap(resp)

    async def pipeline_summary(self, license_id: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/pipeline",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def expire_overdue_quotes(self, license_id: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/expire-overdue",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def list_quote_products(self, license_id: str, quote_id: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/products",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def add_quote_product(
        self, license_id: str, quote_id: str, payload: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/products",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    async def update_quote_product(
        self, license_id: str, quote_id: str, line_id: str, fields: dict,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/quotes/{quote_id}/products/{line_id}",
            headers=self._headers_for(actor_id), json=fields,
        )
        return self._unwrap(resp)

    async def remove_quote_product(
        self, license_id: str, quote_id: str, line_id: str,
        actor_id: str | None = None,
    ) -> None:
        resp = await self._client.delete(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/quotes/{quote_id}/products/{line_id}",
            headers=self._headers_for(actor_id),
        )
        if resp.status_code >= 400:
            self._unwrap(resp)

    async def link_quote_document(
        self, license_id: str, quote_id: str, document_id: str,
        actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/quotes/{quote_id}/document",
            headers=self._headers_for(actor_id), params={"document_id": document_id},
        )
        return self._unwrap(resp)

    async def get_generated_document(
        self, license_id: str, document_id: str,
    ) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/generated-documents/{document_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def record_generated_document(
        self, license_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/generated-documents",
            headers=self._headers_for(actor_id), json=payload,
        )
        return self._unwrap(resp)

    # ---------------------------------------------------------- Phase 14
    # Approval workflows, steps and satisfaction surveys. The Data Tier
    # owns every rule (who may act, what "all approved" means); these are
    # thin calls, one per route, with the same 404→None convention as the
    # rest of this file.

    async def get_approval_workflow(self, license_id: str, entity_type: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/approval-workflows/{entity_type}",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def replace_approval_workflow(
        self, license_id: str, entity_type: str, rules_json: dict, *,
        updated_by: str | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.put(
            f"{self._base}/internal/v1/licenses/{license_id}/approval-workflows/{entity_type}",
            headers=self._headers_for(actor_id),
            json={"rules_json": rules_json, "updated_by": updated_by},
        )
        return self._unwrap(resp)

    async def open_approval_steps(self, license_id: str, report_id: str) -> list[dict]:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/service-reports/{report_id}/approval-steps",
            headers=self._headers,
        )
        return self._unwrap(resp) or []

    async def pending_approval_steps(
        self, license_id: str, *, member_id: str | None = None, roles: list[str] | tuple[str, ...] = (),
    ) -> list[dict]:
        params: dict = {}
        if member_id:
            params["member_id"] = member_id
        if roles:
            params["roles"] = ",".join(roles)
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/approval-steps/pending",
            headers=self._headers, params=params,
        )
        return self._unwrap(resp) or []

    async def approval_steps_for_entity(
        self, license_id: str, entity_type: str, entity_id: str,
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/approval-steps/for/{entity_type}/{entity_id}",
            headers=self._headers,
        )
        return self._unwrap(resp) or []

    async def act_on_approval_step(
        self, license_id: str, step_id: str, *, approve: bool,
        member_id: str | None = None, roles: list[str] | tuple[str, ...] = (),
        reason: str | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/approval-steps/{step_id}/act",
            headers=self._headers_for(actor_id),
            json={
                "approve": approve, "member_id": member_id,
                "roles": list(roles), "reason": reason,
            },
        )
        return self._unwrap(resp)

    async def pending_survey_for_ticket(self, license_id: str, ticket_id: str) -> dict | None:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}"
            f"/surveys/pending-for-ticket/{ticket_id}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)

    async def mark_survey_sent(self, license_id: str, survey_id: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/surveys/{survey_id}/sent",
            headers=self._headers,
        )
        return self._unwrap(resp)

    async def answer_survey(
        self, license_id: str, survey_id: str, *, score: int,
        comment: str | None = None, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.post(
            f"{self._base}/internal/v1/licenses/{license_id}/surveys/{survey_id}/answer",
            headers=self._headers_for(actor_id),
            json={"score": score, "comment": comment},
        )
        return self._unwrap(resp)
