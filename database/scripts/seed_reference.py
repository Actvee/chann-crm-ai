#!/usr/bin/env python3
"""Idempotent reference seed — through Phase 2.

Rules this obeys (03_DATABASE_MIGRATION_CACHE_DATA_SAFETY_STANDARD, section 7):
  * idempotent by BUSINESS KEY, not by surrogate UUID
  * safe to run repeatedly in any environment
  * no environment ever needs undocumented manual SQL to become usable

Bootstrap password
------------------
The Master Spec sketches a default platform admin with the literal password
"changeme123". That is fine for a local DEV box and unacceptable anywhere
else: the Platform Admin is the break-glass account with cross-tenant reach.

So this script reads PLATFORM_ADMIN_BOOTSTRAP_PASSWORD, and when APP_ENV is
not "dev" it refuses to run without one. Failing the seed is the correct
outcome — a Stage or Production database that comes up with a publicly known
admin password is worse than a database that does not come up at all.

Usage:
    DATABASE_URL=postgresql+psycopg://... APP_ENV=dev python3 seed_reference.py
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from argon2 import PasswordHasher  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from chann_data.models import CustomRole, License, PlatformAdmin, RolePermission  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402

DEV_FALLBACK_PASSWORD = "changeme123"  # DEV only, never promoted


def resolve_bootstrap_password(app_env: str) -> str:
    supplied = os.environ.get("PLATFORM_ADMIN_BOOTSTRAP_PASSWORD", "").strip()
    if supplied:
        return supplied
    if app_env == "dev":
        print("WARN: using the DEV fallback admin password. Never use this outside DEV.")
        return DEV_FALLBACK_PASSWORD
    raise SystemExit(
        "FATAL: PLATFORM_ADMIN_BOOTSTRAP_PASSWORD is required when APP_ENV="
        f"{app_env!r}. Refusing to seed a break-glass account with a known password."
    )


def seed_platform_admin(session: Session, password: str) -> str:
    """Business key: username."""
    username = os.environ.get("PLATFORM_ADMIN_USERNAME", "admin").strip()
    existing = session.execute(
        select(PlatformAdmin).where(PlatformAdmin.username == username)
    ).scalar_one_or_none()
    if existing:
        return f"platform_admin[{username}] exists — unchanged"

    session.add(
        PlatformAdmin(
            id=uuid.uuid4(),
            username=username,
            password_hash=PasswordHasher().hash(password),
        )
    )
    return f"platform_admin[{username}] created"


def seed_dev_fixture(session: Session) -> list[str]:
    """DEV-only fixture, kept separate from reference data so the two can
    never collide on a unique key (standard section 8)."""
    notes = []
    for code, name in (("DEVCO001", "Dev Company One"), ("DEVCO002", "Dev Company Two")):
        existing = session.execute(
            select(License).where(License.license_code == code)
        ).scalar_one_or_none()
        if existing:
            notes.append(f"license[{code}] exists — unchanged")
            continue
        session.add(License(id=uuid.uuid4(), license_code=code, company_name=name))
        notes.append(f"license[{code}] created")
    return notes


def seed_default_roles(session: Session) -> list[str]:
    """Create Phase 2 role templates once per tenant.

    Existing non-owner roles are intentionally not overwritten: the product
    permits tenants to customize the default templates after onboarding.
    """
    notes: list[str] = []
    licenses = list(session.execute(select(License).order_by(License.license_code)).scalars())
    for license_row in licenses:
        for role_name, permission_keys in DEFAULT_ROLE_TEMPLATES.items():
            role = session.execute(
                select(CustomRole).where(
                    CustomRole.license_id == license_row.id,
                    CustomRole.role_name == role_name,
                )
            ).scalar_one_or_none()
            if role is not None:
                notes.append(
                    f"role[{license_row.license_code}:{role_name}] exists — unchanged"
                )
                continue
            role = CustomRole(
                id=uuid.uuid4(),
                license_id=license_row.id,
                role_name=role_name,
                is_owner=role_name == "owner",
            )
            session.add(role)
            session.flush()
            if permission_keys is not None:
                session.add_all(
                    RolePermission(
                        id=uuid.uuid4(),
                        license_id=license_row.id,
                        role=role_name,
                        permission_key=permission_key,
                        allowed=True,
                    )
                    for permission_key in sorted(permission_keys)
                )
            notes.append(f"role[{license_row.license_code}:{role_name}] created")
    return notes


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("FATAL: DATABASE_URL is DERIVED_AT_DEPLOY and must be set")
    app_env = os.environ.get("APP_ENV", "dev").strip()

    password = resolve_bootstrap_password(app_env)
    engine = create_engine(database_url, future=True)

    print(f"seed_reference: APP_ENV={app_env}")
    with Session(engine) as session:
        results = [seed_platform_admin(session, password)]
        if app_env == "dev" and os.environ.get("SEED_DEV_FIXTURE", "1") == "1":
            results += seed_dev_fixture(session)
        results += seed_default_roles(session)
        session.commit()

    for line in results:
        print(f"  {line}")
    print("seed_reference: OK (idempotent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
