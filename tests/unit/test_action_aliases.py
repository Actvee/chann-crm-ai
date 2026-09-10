"""The model's verb is a synonym; the handler must not care which one.

Owner's review of the message path, 10 ก.ย. 2569: a person types naturally
and the model reads them correctly, so the reply must not depend on which
synonym the model happened to pick. `ACTION_ALIASES` existed for exactly
that, but was consulted only at the permission gate — so "add" passed the
gate, reached `_handle_customer_intent`, matched no branch (which compares
`action == "create"`), and fell out of the bottom as "ในแชทยังทำรายการนี้
ไม่ได้" with a complete name and phone in hand and nothing written.

Two more tables are keyed on canonical verbs and were missed the same way:
`_AI_GUARDED`, so a negation the model read as action="add" skipped the
negation guard entirely, and `READ_ACTIONS`.

Each test below runs the aliased verb and its canonical twin through the
real dispatcher and asserts they are indistinguishable — in the reply AND
in what reached the Data tier.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chann_app.services import chat  # noqa: E402
from chann_app.services.chat import ACTION_ALIASES  # noqa: E402
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ctx  # noqa: E402

KEYS = [
    "customer.read", "customer.create", "customer.update", "customer.archive",
    "deal.read", "deal.create", "approval.view",
    "followup.read", "followup.create", "followup.update", "followup.delete",
]
WRITE_CALLS = ("create_", "update_", "delete_", "archive_")


async def _run(action, entity, fields, *, message, oa="sales", keys=None):
    """One model answer through the real dispatcher. Returns the reply text
    and the write calls that reached the Data tier."""
    permission_keys = list(KEYS if keys is None else keys)
    client = FakeDataClient(role="sales", permission_keys=permission_keys)
    reply = await chat._execute_intent(
        client,
        intent={"action": action, "entity": entity, "fields": dict(fields), "missing": []},
        ctx=_ctx(primary_role="sales", oa=oa),
        license_id=LICENSE_ID, language="th",
        permission_keys=permission_keys, message=message,
    )
    writes = [r[0] for r in client.recorded if r[0].startswith(WRITE_CALLS)]
    return (reply.text or ""), writes


CUSTOMER = {"first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678"}


class TestAnAliasBehavesLikeItsCanonicalVerb:
    """Same fields, same sentence, only the verb differs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", ["add", "new"])
    async def test_creating_a_customer(self, alias):
        message = "เพิ่มลูกค้า สมชาย ใจดี 0812345678"
        canon_text, canon_writes = await _run("create", "customer", CUSTOMER, message=message)
        alias_text, alias_writes = await _run(alias, "customer", CUSTOMER, message=message)
        # The bug: the alias reached the handler and fell off the end.
        assert "ยังทำรายการนี้ไม่ได้" not in alias_text
        assert alias_writes == canon_writes != []
        assert alias_text == canon_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", ["edit", "modify"])
    async def test_updating_a_customer(self, alias):
        message = "แก้เบอร์ลูกค้าสมชาย เป็น 0899999999"
        fields = {"customer_ref": "สมชาย", "phone": "0899999999"}
        canon_text, canon_writes = await _run("update", "customer", fields, message=message)
        alias_text, alias_writes = await _run(alias, "customer", fields, message=message)
        assert "ยังทำรายการนี้ไม่ได้" not in alias_text
        assert alias_writes == canon_writes
        assert alias_text == canon_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", ["view", "list", "get", "show", "search", "find"])
    async def test_reading_the_approval_queue(self, alias):
        message = "ดูรายการรออนุมัติ"
        canon_text, _ = await _run("read", "approval", {}, message=message)
        alias_text, alias_writes = await _run(alias, "approval", {}, message=message)
        assert alias_writes == []
        assert alias_text == canon_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", ["view", "list", "get", "show"])
    async def test_reading_the_diary(self, alias):
        message = "ดูนัดหมาย"
        canon_text, _ = await _run("read", "followup", {}, message=message)
        alias_text, alias_writes = await _run(alias, "followup", {}, message=message)
        assert alias_writes == []
        assert alias_text == canon_text


class TestTheNegationGuardSeesAnAliasToo:
    """_AI_GUARDED is keyed on canonical verbs. An aliased verb used to walk
    straight past it — the reason this fix is a safety fix and not only a
    capability one."""

    APPOINTMENT = {
        "customer_ref": "C-2026-0001", "date": "2026-09-12",
        "time": "10:00", "note": "ตรวจแอร์",
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ไม่ต้องนัด C-2026-0001 พรุ่งนี้",
        "ยังไม่ต้องนัดนะครับ",
        "ถ้าลูกค้าว่างค่อยนัด C-2026-0001",
    ])
    @pytest.mark.parametrize("alias", ["add", "new"])
    async def test_a_sentence_that_declines_writes_nothing(self, message, alias):
        text, writes = await _run(alias, "followup", self.APPOINTMENT, message=message)
        assert writes == []
        # And it says so in the guard's own words, not the generic fallback.
        assert "ยังไม่ได้ตั้งนัด" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", ["add", "new"])
    async def test_the_guard_answers_the_alias_exactly_as_the_canonical_verb(self, alias):
        message = "ไม่ต้องนัด C-2026-0001 พรุ่งนี้"
        canon_text, _ = await _run("create", "followup", self.APPOINTMENT, message=message)
        alias_text, _ = await _run(alias, "followup", self.APPOINTMENT, message=message)
        assert alias_text == canon_text


class TestTheTableIsAppliedWhole:
    """A new alias added to ACTION_ALIASES must not need a second edit
    somewhere else to take effect — that is how this drifted in the first
    place."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias,canonical", sorted(ACTION_ALIASES.items()))
    async def test_every_alias_in_the_table_reaches_the_same_place(self, alias, canonical):
        message = "ดูลูกค้า"
        canon_text, canon_writes = await _run(canonical, "customer", CUSTOMER, message=message)
        alias_text, alias_writes = await _run(alias, "customer", CUSTOMER, message=message)
        assert alias_writes == canon_writes
        assert alias_text == canon_text

    def test_read_actions_still_derives_from_the_table(self):
        # The set the handlers use for "show me" must stay a projection of
        # the table, not a hand-kept copy.
        assert chat.READ_ACTIONS == frozenset(
            {"read"} | {a for a, c in ACTION_ALIASES.items() if c == "read"}
        )


class TestAskingForAnInviteCode:
    """Owner, 10 ก.ย. 2569: "ขอรหัสเชิญช่าง แบบนี้ได้ แต่พอพิม ขอรหัสเชิญ AI
    กลับไม่เข้าใจ ทั้งๆที่มีอยู่ 2 แบบคือเชิญช่างกับ sale ควรจะเข้าใจและถามว่า
    จะเอาตัวไหน". The guide had been telling people to type the bare form
    since before this was reported."""

    @staticmethod
    async def _say(message):
        from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

        keys = sorted(DEFAULT_ROLE_TEMPLATES["admin"])
        client = FakeDataClient(role="sales", permission_keys=keys)
        reply = await chat.handle_chat_message(
            client, ctx=_ctx(primary_role="sales", oa="sales"),
            message=message, language="th",
        )
        invites = [r for r in client.recorded if r[0] == "create_invite"]
        return (reply.text or ""), [q[0] for q in (reply.quick_replies or [])], invites

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", ["ขอรหัสเชิญช่าง", "เพิ่มช่าง", "รหัสช่าง"])
    async def test_the_technician_form_still_issues_a_technician_code(self, message):
        text, _, invites = await self._say(message)
        assert "รหัสเชิญช่าง:" in text
        assert len(invites) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ขอรหัสเชิญทีมขาย", "ขอรหัสเชิญเซลส์", "เพิ่มพนักงาน", "invite sales",
    ])
    async def test_the_sales_form_issues_a_sales_code(self, message):
        text, _, invites = await self._say(message)
        assert "รหัสเชิญทีมขาย/CS:" in text
        assert len(invites) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "ขอรหัสเชิญ", "รหัสเชิญ", "ขอโค้ดเชิญ", "เพิ่มสมาชิก", "invite code",
    ])
    async def test_the_bare_form_asks_which_and_issues_nothing(self, message):
        text, buttons, invites = await self._say(message)
        # It understood; it just needs one more word.
        assert "2 แบบ" in text
        assert buttons == ["ช่าง", "ทีมขาย/CS"]
        # And nothing was created while it asked.
        assert invites == []

    @pytest.mark.asyncio
    async def test_the_buttons_are_answerable(self):
        """Each button's payload must be a message the router handles — a
        quick reply that leads nowhere is worse than no quick reply."""
        _, _, _ = await self._say("ขอรหัสเชิญ")
        for payload, expected in (
            ("ขอรหัสเชิญช่าง", "รหัสเชิญช่าง:"),
            ("ขอรหัสเชิญทีมขาย", "รหัสเชิญทีมขาย/CS:"),
        ):
            text, _, invites = await self._say(payload)
            assert expected in text
            assert len(invites) == 1

    @pytest.mark.asyncio
    async def test_the_guide_only_names_commands_that_work(self):
        """The bare form was documented and unimplemented for a year. Every
        invite command the guide prints must reach a handler."""
        from chann_app.services import guides

        printed = set()
        for text in (str(guides.GUIDES),):
            for candidate in ("ขอรหัสเชิญ", "ขอรหัสเชิญช่าง", "ขอรหัสเชิญทีมขาย"):
                if candidate in text:
                    printed.add(candidate)
        assert printed, "the guide names no invite command at all"
        for command in sorted(printed):
            text, buttons, _ = await self._say(command)
            assert "ยังไม่แน่ใจ" not in text, f"{command} is documented but unhandled"
