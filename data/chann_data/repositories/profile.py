"""Profile CRUD — Phase 8 (Master Spec 8.3-8.5).

One function, `update_profile`, is the domain service both self-edit and
Sales/CS-on-behalf edit call — spec 8.5's test_profile_chat_vs_liff requires
chat and LIFF produce identical results, which only holds if both paths
funnel through the same code rather than each reimplementing the write.

Authorization (self vs on-behalf, and which tenant relationship justifies an
on-behalf edit) is NOT here — it belongs in the Application tier, which is
the only tier that knows who is asking and in what tenant context. This
repository only ever answers "is this identity real and are these field
values acceptable", never "is this caller allowed to change it".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChannIdentity, CustomerLicenseLink, LicenseMember

EDITABLE_FIELDS = frozenset(
    {"first_name", "last_name", "phone", "email", "address"}
)

# Thai mobile numbers: 0 + 9 digits, optionally with hyphens as people type
# them ("08x-xxx-xxxx"). Deliberately lenient — rejecting a real number over
# formatting sends the user back to hand-typing digits with no spaces, which
# is not friendlier.
_PHONE_RE = re.compile(r"^0\d(-?\d){8}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileConflict(RuntimeError):
    pass


class ProfileNotFound(LookupError):
    pass


def _normalise_phone(raw: str) -> str:
    digits = raw.replace("-", "").replace(" ", "")
    if not _PHONE_RE.match(raw.strip()) and not (
        digits.isdigit() and len(digits) == 10 and digits.startswith("0")
    ):
        raise ProfileConflict(f"invalid phone number: {raw!r}")
    return digits


def _validate_email(raw: str) -> str:
    value = raw.strip()
    if not _EMAIL_RE.match(value):
        raise ProfileConflict(f"invalid email address: {raw!r}")
    return value


class ProfileRepository:
    def __init__(self, session: Session):
        self._s = session

    def get(self, chann_uid: str) -> ChannIdentity | None:
        return self._s.get(ChannIdentity, chann_uid)

    def update_profile(self, chann_uid: str, fields: dict) -> ChannIdentity:
        """Update whichever of EDITABLE_FIELDS are present in `fields`.

        Unknown keys are rejected rather than silently ignored — a caller
        that thinks it set a field it didn't (e.g. a typo'd key from the AI
        parser) should find out immediately, not ship a profile edit that
        silently did less than asked.
        """
        identity = self._s.get(ChannIdentity, chann_uid)
        if identity is None:
            raise ProfileNotFound(f"no identity for chann_uid {chann_uid!r}")

        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ProfileConflict(f"not editable: {', '.join(sorted(unknown))}")

        for key, value in fields.items():
            if value is None:
                continue
            text = str(value).strip()
            if key == "phone" and text:
                text = _normalise_phone(text)
            elif key == "email" and text:
                text = _validate_email(text)
            setattr(identity, key, text or None)

        if not identity.registered and any(fields.values()):
            identity.registered = True
            identity.registered_at = datetime.now(timezone.utc)

        self._s.flush()
        return identity

    def may_edit_on_behalf(
        self, *, actor_chann_uid: str, target_chann_uid: str, license_id
    ) -> bool:
        """Is there a real tenant relationship justifying an on-behalf edit?

        Two relationships qualify: the target is a customer linked to this
        tenant (Sales/CS editing a customer who has actually dealt with this
        shop — Phase 6.5's customer_license_links), or the target is a
        fellow member of the same tenant. Neither check trusts a bare name
        the AI might have parsed — both are real rows in this tenant's data.
        """
        if actor_chann_uid == target_chann_uid:
            return True

        linked_customer = self._s.execute(
            select(CustomerLicenseLink).where(
                CustomerLicenseLink.chann_uid == target_chann_uid,
                CustomerLicenseLink.license_id == license_id,
            )
        ).first()
        if linked_customer is not None:
            return True

        fellow_member = self._s.execute(
            select(LicenseMember).where(
                LicenseMember.chann_uid == target_chann_uid,
                LicenseMember.license_id == license_id,
            )
        ).first()
        return fellow_member is not None
