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

    async def memberships_of(self, chann_uid: str) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/internal/v1/identities/{chann_uid}/memberships",
            headers=self._headers,
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
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
