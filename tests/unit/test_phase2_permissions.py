"""Fast Phase 2 permission and Application policy tests."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "application"))

from chann_data.permissions import DEFAULT_ROLE_TEMPLATES, PERMISSION_KEYS  # noqa: E402
from chann_app.routers_phase2 import (  # noqa: E402
    RolePolicyCompileIn,
    RoleWriteIn,
    compile_role_policy,
    create_role,
)
from chann_app.services.authorization import TenantPrincipal  # noqa: E402


def principal(*keys: str, owner: bool = False) -> TenantPrincipal:
    return TenantPrincipal(
        license_id="tenant-a",
        chann_uid="CHN-S-000001",
        role="custom-label",
        is_owner=owner,
        permission_keys=frozenset(keys),
    )


class TestPermissionCatalogue:
    def test_final_appendix_keys_are_present_and_old_contact_keys_are_absent(self):
        assert "customer.create" in PERMISSION_KEYS
        assert "platform.admin.break_glass" in PERMISSION_KEYS
        assert "contact.create" not in PERMISSION_KEYS

    def test_owner_is_all_permissions_without_grant_rows(self):
        assert DEFAULT_ROLE_TEMPLATES["owner"] is None

    def test_cs_vs_sales_default_is_least_privilege(self):
        cs = DEFAULT_ROLE_TEMPLATES["cs"]
        member = DEFAULT_ROLE_TEMPLATES["member"]
        assert cs is not None and member is not None
        assert "ticket.assign" in cs
        assert "deal.create" not in cs
        assert "deal.create" in member
        assert "ticket.assign" not in member
        assert "approval.approve" in cs


class TestApplicationPermissionGate:
    def test_gate_uses_permission_key_not_role_name(self):
        principal("role.manage").require("role.manage")
        with pytest.raises(HTTPException) as exc:
            principal().require("role.manage")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_role_create_calls_data_only_after_permission_gate(self):
        class FakeDataClient:
            called = False

            async def create_role(self, license_id, payload):
                self.called = True
                return {"license_id": license_id, **payload}

        client = FakeDataClient()
        result = await create_role(
            "tenant-a",
            RoleWriteIn(role_name="หัวหน้าทีมขาย", permission_keys=["deal.create"]),
            principal("role.manage"),
            client,
        )
        assert client.called
        assert result["permission_keys"] == ["deal.create"]

    @pytest.mark.asyncio
    async def test_role_create_denies_without_permission_even_for_admin_named_role(self):
        denied = TenantPrincipal(
            license_id="tenant-a",
            chann_uid="CHN-S-000002",
            role="admin",
            is_owner=False,
            permission_keys=frozenset(),
        )

        class NeverCalled:
            async def create_role(self, *_args, **_kwargs):
                pytest.fail("Data Tier must not be called after a denied permission gate")

        with pytest.raises(HTTPException) as exc:
            await create_role(
                "tenant-a",
                RoleWriteIn(role_name="x", permission_keys=[]),
                denied,
                NeverCalled(),
            )
        assert exc.value.status_code == 403


class TestPhase2PolicyCompilerBoundary:
    @pytest.mark.asyncio
    async def test_explicit_keys_compile_and_require_confirmation(self):
        result = await compile_role_policy(
            "tenant-a",
            RolePolicyCompileIn(policy_prompt="ให้ deal.create และ customer.read"),
            principal("role.manage"),
        )
        assert result["permission_keys"] == ["customer.read", "deal.create"]
        assert result["requires_user_confirmation"] is True
        assert result["ai_used"] is False

    @pytest.mark.asyncio
    async def test_ambiguous_prompt_fails_closed_until_phase4_ai_adapter(self):
        with pytest.raises(HTTPException) as exc:
            await compile_role_policy(
                "tenant-a",
                RolePolicyCompileIn(policy_prompt="ให้ทำงานฝ่ายขายทั่วไป"),
                principal("role.manage"),
            )
        assert exc.value.status_code == 422
