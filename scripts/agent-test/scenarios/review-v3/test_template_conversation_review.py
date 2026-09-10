"""State probes for a draft decision; no real model or storage calls."""
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from agent_test_runner.backends import FakeBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["ใช้เลยครับ", "ยืนยันใช้แบบนี้", "ขอดูก่อน"])
async def test_related_draft_reply_keeps_context_or_publishes(message):
    template = yaml.safe_load((HERE.parent / "template-design-in-chat.yaml").read_text())
    backend = FakeBackend()
    actor = template["actor"]
    backend.reset(actor)
    first = template["steps"][0]["send"]
    kwargs = dict(oa=actor["oa"], role=actor["role"], language="th",
                  permissions=actor["permissions"], refs={})
    try:
        await backend.send(message=first["message"], ai=first["ai"], **kwargs)
        assert backend.client._pending is not None
        assert len(backend.client._template_versions) == 1
        await backend.send(message=message, ai=None, **kwargs)
        published = any(v["status"] == "published" for v in backend.client._template_versions)
        assert backend.client._pending is not None or published, (
            "Related reply cleared conversation context without publishing; "
            "the persisted unpublished draft survives but chat cannot continue it"
        )
        if message == "ขอดูก่อน":
            assert not published, "Preview request must not publish"
    finally:
        await backend.close()
