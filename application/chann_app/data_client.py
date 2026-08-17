"""The only way this tier reaches persistent state.

Boundary rule (CLAUDE.md 4): the Application Tier must not import SQLAlchemy,
psycopg, or redis. It talks to the Data Tier over internal HTTP. The boundary
test in tests/boundary enforces this by inspecting imports, so the rule is
executable rather than aspirational.
"""
from __future__ import annotations

import httpx

from .config import settings


class DataTierError(RuntimeError):
    pass


class DataClient:
    def __init__(self, base_url: str | None = None, secret: str | None = None,
                 client: httpx.AsyncClient | None = None):
        self._base = (base_url or settings.data_base_url).rstrip("/")
        self._secret = secret if secret is not None else settings.admin_secret
        self._client = client or httpx.AsyncClient(timeout=10.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self._secret, "Content-Type": "application/json"}

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
            raise DataTierError(f"data tier returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
