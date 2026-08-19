"""Tenant-scoped Phase 2 repositories and invariants."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import (
    CustomRole,
    LicenseMember,
    LicenseSetting,
    OwnershipTransfer,
    RolePermission,
)
from ..permissions import PERMISSION_KEYS, validate_permission_keys
from .tenant_scope import TenantScope


# ชื่อ role ที่เจ้าของเดิมจะถูกลดลงมาเมื่อโอน ownership.
# license_members.role ไม่มี FK ไป custom_roles จึงเขียนชื่ออะไรลงไปก็ได้
# ถ้าเขียนชื่อที่ไม่มีแถวใน custom_roles สมาชิกคนนั้นจะเหลือสิทธิ์ศูนย์
# (AuthorizationRepository.context ตีเป็น unknown role) และแก้เองไม่ได้
# จึงต้อง validate ก่อนเขียนเสมอ ห้ามเขียน literal ตรงๆ
DEMOTION_ROLE_NAME = "admin"


class Phase2Conflict(RuntimeError):
    pass


class Phase2NotFound(LookupError):
    pass


class RoleRepository:
    def __init__(self, session: Session):
        self._s = session

    def list(self, scope: TenantScope) -> list[CustomRole]:
        return list(
            self._s.execute(
                select(CustomRole)
                .where(CustomRole.license_id == scope.license_id)
                .order_by(CustomRole.is_owner.desc(), CustomRole.role_name)
            ).scalars()
        )

    def get(self, scope: TenantScope, role_name: str) -> CustomRole | None:
        return self._s.execute(
            select(CustomRole).where(
                CustomRole.license_id == scope.license_id,
                CustomRole.role_name == role_name,
            )
        ).scalar_one_or_none()

    def permission_keys(self, scope: TenantScope, role_name: str) -> set[str]:
        role = self.get(scope, role_name)
        if role is None:
            raise Phase2NotFound("role not found")
        if role.is_owner:
            return set(PERMISSION_KEYS)
        return set(
            self._s.execute(
                select(RolePermission.permission_key).where(
                    RolePermission.license_id == scope.license_id,
                    RolePermission.role == role_name,
                    RolePermission.allowed.is_(True),
                )
            ).scalars()
        )

    def create(
        self, scope: TenantScope, role_name: str, permission_keys: set[str]
    ) -> CustomRole:
        validate_permission_keys(permission_keys)
        normalized = role_name.strip()
        if not normalized:
            raise ValueError("role_name must not be empty")
        if "/" in normalized:
            raise ValueError("role_name must not contain '/'")
        if normalized.casefold() == "owner":
            raise Phase2Conflict("owner is a protected role")
        role = CustomRole(
            id=uuid.uuid4(), license_id=scope.license_id, role_name=normalized, is_owner=False
        )
        self._s.add(role)
        self._s.flush()
        self._replace_permission_rows(scope, normalized, permission_keys)
        return role

    def update(
        self,
        scope: TenantScope,
        current_name: str,
        new_name: str,
        permission_keys: set[str],
    ) -> CustomRole:
        validate_permission_keys(permission_keys)
        role = self.get(scope, current_name)
        if role is None:
            raise Phase2NotFound("role not found")
        if role.is_owner:
            raise Phase2Conflict("owner role is immutable")
        normalized = new_name.strip()
        if not normalized:
            raise ValueError("role_name must not be empty")
        if "/" in normalized:
            raise ValueError("role_name must not contain '/'")
        if normalized.casefold() == "owner":
            raise Phase2Conflict("owner is a protected role")

        if normalized != current_name:
            # role_permissions follows through its ON UPDATE CASCADE FK. The
            # membership string is deliberately updated in the same DB
            # transaction because Phase 1 kept it as free text.
            self._s.execute(
                update(LicenseMember)
                .where(
                    LicenseMember.license_id == scope.license_id,
                    LicenseMember.role == current_name,
                )
                .values(role=normalized)
            )
            role.role_name = normalized
            self._s.flush()
        self._replace_permission_rows(scope, normalized, permission_keys)
        return role

    def delete(self, scope: TenantScope, role_name: str) -> None:
        role = self.get(scope, role_name)
        if role is None:
            raise Phase2NotFound("role not found")
        if role.is_owner:
            raise Phase2Conflict("owner role cannot be deleted")
        assigned = self._s.execute(
            select(LicenseMember.id).where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.role == role_name,
            ).limit(1)
        ).scalar_one_or_none()
        if assigned is not None:
            raise Phase2Conflict("role is assigned to a member")
        self._s.delete(role)

    def _replace_permission_rows(
        self, scope: TenantScope, role_name: str, permission_keys: set[str]
    ) -> None:
        self._s.execute(
            delete(RolePermission).where(
                RolePermission.license_id == scope.license_id,
                RolePermission.role == role_name,
            )
        )
        self._s.add_all(
            RolePermission(
                id=uuid.uuid4(),
                license_id=scope.license_id,
                role=role_name,
                permission_key=key,
                allowed=True,
            )
            for key in sorted(permission_keys)
        )


class AuthorizationRepository:
    def __init__(self, session: Session):
        self._s = session

    def context(self, scope: TenantScope, chann_uid: str) -> dict | None:
        member = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.chann_uid == chann_uid,
                LicenseMember.status == "active",
            )
        ).scalar_one_or_none()
        if member is None:
            return None
        role = RoleRepository(self._s).get(scope, member.role)
        if role is None:
            # Unknown role names deny all permissions. This is fail-secure and
            # makes incomplete seed/onboarding visible instead of permissive.
            keys: set[str] = set()
            is_owner = False
        else:
            keys = RoleRepository(self._s).permission_keys(scope, role.role_name)
            is_owner = role.is_owner
        return {
            "member_id": member.id,
            "chann_uid": member.chann_uid,
            "role": member.role,
            "is_owner": is_owner,
            "permission_keys": sorted(keys),
        }


class MemberRoleRepository:
    def __init__(self, session: Session):
        self._s = session

    def set_role(self, scope: TenantScope, chann_uid: str, role_name: str) -> LicenseMember:
        role = RoleRepository(self._s).get(scope, role_name)
        if role is None:
            raise Phase2NotFound("role not found")
        member = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.chann_uid == chann_uid,
            )
        ).scalar_one_or_none()
        if member is None:
            raise Phase2NotFound("member not found")
        current_role = RoleRepository(self._s).get(scope, member.role)
        if current_role is not None and current_role.is_owner and not role.is_owner:
            raise Phase2Conflict("owner changes require the transfer flow")
        member.role = role.role_name
        return member


class LicenseSettingRepository:
    def __init__(self, session: Session):
        self._s = session

    def list(self, scope: TenantScope) -> list[LicenseSetting]:
        return list(
            self._s.execute(
                select(LicenseSetting)
                .where(LicenseSetting.license_id == scope.license_id)
                .order_by(LicenseSetting.setting_key)
            ).scalars()
        )

    def upsert(self, scope: TenantScope, key: str, value) -> LicenseSetting:
        normalized = key.strip()
        if not normalized:
            raise ValueError("setting_key must not be empty")
        if "/" in normalized:
            raise ValueError("setting_key must not contain '/'")
        row = self._s.execute(
            select(LicenseSetting).where(
                LicenseSetting.license_id == scope.license_id,
                LicenseSetting.setting_key == normalized,
            )
        ).scalar_one_or_none()
        if row is None:
            row = LicenseSetting(
                id=uuid.uuid4(),
                license_id=scope.license_id,
                setting_key=normalized,
                setting_value=value,
            )
            self._s.add(row)
        else:
            row.setting_value = value
        self._s.flush()
        return row

    def delete(self, scope: TenantScope, key: str) -> None:
        row = self._s.execute(
            select(LicenseSetting).where(
                LicenseSetting.license_id == scope.license_id,
                LicenseSetting.setting_key == key,
            )
        ).scalar_one_or_none()
        if row is None:
            raise Phase2NotFound("setting not found")
        self._s.delete(row)


class OwnershipTransferRepository:
    def __init__(self, session: Session):
        self._s = session

    def request(
        self, scope: TenantScope, from_chann_uid: str, to_chann_uid: str
    ) -> OwnershipTransfer:
        members = list(
            self._s.execute(
                select(LicenseMember)
                .where(
                    LicenseMember.license_id == scope.license_id,
                    LicenseMember.chann_uid.in_([from_chann_uid, to_chann_uid]),
                    LicenseMember.status == "active",
                )
                .with_for_update()
            ).scalars()
        )
        by_uid = {member.chann_uid: member for member in members}
        if from_chann_uid not in by_uid or to_chann_uid not in by_uid:
            raise Phase2NotFound("both transfer members must be active in the tenant")
        if from_chann_uid == to_chann_uid:
            raise Phase2Conflict("owner transfer target must be another member")
        source = by_uid[from_chann_uid]
        source_role = RoleRepository(self._s).get(scope, source.role)
        if source_role is None or not source_role.is_owner:
            raise Phase2Conflict("only the current owner can request transfer")
        pending = self._s.execute(
            select(OwnershipTransfer.id).where(
                OwnershipTransfer.license_id == scope.license_id,
                OwnershipTransfer.status == "pending",
            ).limit(1)
        ).scalar_one_or_none()
        if pending is not None:
            raise Phase2Conflict("an ownership transfer is already pending")
        transfer = OwnershipTransfer(
            id=uuid.uuid4(),
            license_id=scope.license_id,
            from_member_id=source.id,
            to_member_id=by_uid[to_chann_uid].id,
            status="pending",
        )
        self._s.add(transfer)
        self._s.flush()
        return transfer

    def accept(
        self, scope: TenantScope, transfer_id: uuid.UUID, accepting_chann_uid: str
    ) -> OwnershipTransfer:
        transfer = self._s.execute(
            select(OwnershipTransfer)
            .where(
                OwnershipTransfer.id == transfer_id,
                OwnershipTransfer.license_id == scope.license_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if transfer is None:
            raise Phase2NotFound("ownership transfer not found")
        if transfer.status != "pending":
            raise Phase2Conflict("ownership transfer is no longer pending")
        target = self._s.execute(
            select(LicenseMember)
            .where(LicenseMember.id == transfer.to_member_id)
            .with_for_update()
        ).scalar_one()
        source = self._s.execute(
            select(LicenseMember)
            .where(LicenseMember.id == transfer.from_member_id)
            .with_for_update()
        ).scalar_one()
        if target.chann_uid != accepting_chann_uid:
            raise Phase2Conflict("only the nominated new owner can accept")
        self._apply_transfer(scope, source, target)
        transfer.status = "accepted"
        transfer.accepted_at = datetime.now(timezone.utc)
        return transfer

    def force(self, scope: TenantScope, target_chann_uid: str) -> LicenseMember:
        target = self._s.execute(
            select(LicenseMember)
            .where(
                LicenseMember.license_id == scope.license_id,
                LicenseMember.chann_uid == target_chann_uid,
                LicenseMember.status == "active",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise Phase2NotFound("target member not found")
        owner_role = self._owner_role(scope)
        owners = list(
            self._s.execute(
                select(LicenseMember)
                .where(
                    LicenseMember.license_id == scope.license_id,
                    LicenseMember.role == owner_role.role_name,
                )
                .with_for_update()
            ).scalars()
        )
        if not owners:
            raise Phase2Conflict("tenant has no current owner")
        # Break-glass supersedes every normal transfer request. Leaving an old
        # request pending would let its nominee accept later and silently undo
        # the Platform Admin recovery operation.
        self._s.execute(
            update(OwnershipTransfer)
            .where(
                OwnershipTransfer.license_id == scope.license_id,
                OwnershipTransfer.status == "pending",
            )
            .values(status="cancelled")
        )
        demotion_role = None
        for owner in owners:
            if owner.id != target.id:
                if demotion_role is None:
                    demotion_role = self._demotion_role(scope)
                owner.role = demotion_role.role_name
        target.role = owner_role.role_name
        return target

    def _owner_role(self, scope: TenantScope) -> CustomRole:
        role = (
            self._s.execute(
                select(CustomRole).where(
                    CustomRole.license_id == scope.license_id,
                    CustomRole.is_owner.is_(True),
                )
            )
            .scalars()
            .first()
        )
        if role is None:
            raise Phase2Conflict("tenant has no owner role")
        return role

    def _demotion_role(self, scope: TenantScope) -> CustomRole:
        role = RoleRepository(self._s).get(scope, DEMOTION_ROLE_NAME)
        if role is None or role.is_owner:
            raise Phase2Conflict(
                f"demotion role '{DEMOTION_ROLE_NAME}' is missing in this tenant; "
                "recreate it before transferring ownership"
            )
        return role

    def _apply_transfer(
        self, scope: TenantScope, source: LicenseMember, target: LicenseMember
    ) -> None:
        if source.license_id != scope.license_id or target.license_id != scope.license_id:
            raise Phase2Conflict("ownership transfer crossed tenant boundary")
        source.role = self._demotion_role(scope).role_name
        target.role = self._owner_role(scope).role_name
