"""Making the repo importable, and making sure no real model is ever called.

The three tiers are separate import roots (`application/`, `data/`), the way
they are deployed; the unit-test folder is a root too, because the fake Data
client the simulators use lives there. Importing it rather than copying it is
deliberate: a second copy of that fake would drift, and a fake that has drifted
from the one the test suite uses is worse than no fake at all.

`openrouter_api_key` is set to a placeholder for the same reason the
simulators set it: with no key the chat engine short-circuits to "AI not
configured" and a scenario would be testing the wrong branch. Every model call
is still answered by an in-process transport — see AiProbe — so the placeholder
never leaves the process.

Object storage is stood in the same way Redis is on the `db` backend: an
in-process dictionary. Without it `get_document_store()` returns the null
store, every document feature answers "storage is not configured", and a
scenario covering one would be asserting on the wrong branch. Nothing reaches
GCS — the stand-in is a dict that lives and dies with the run.

LINE is stood in too (round 21E). A push to the customer used to fail
here — no channel token — and be swallowed, so a scenario "proved" a send
that never went. Sending a document now refuses to call a failed push a
send, so the run needs a LINE that takes pushes: `MemoryLine` records them.
A LINE user id beginning with `U-line-down` is refused the way LINE refuses
(a `LineReplyError`), so a scenario can seed a customer whose push fails and
assert the reply says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def prepare() -> Path:
    """Put the tiers on sys.path and neutralise the model. Idempotent."""
    for relative in ("application", "data", "tests/unit"):
        path = str(REPO_ROOT / relative)
        if path not in sys.path:
            sys.path.insert(0, path)

    from chann_app.config import settings

    # Overwritten, not defaulted: if the environment happens to hold a real
    # OPENROUTER_API_KEY, a scenario must still not be able to spend it. The
    # placeholder is only ever seen by the in-process AiProbe transport.
    settings.openrouter_api_key = "agent-test-channel"
    settings.openrouter_model = "agent-test-model"

    _install_memory_document_store()
    _install_memory_line()
    return REPO_ROOT


class MemoryDocumentStore:
    """Object storage as a dict, with the real store's interface.

    Deliberately not a subclass of anything: it implements `put`/`get`/
    `delete` as `services/storage/base.py` declares them, and a drift in
    that interface should fail loudly here rather than be inherited into
    silence.
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes, content_type: str):
        self.objects[key] = content

        class _Stored:
            path = key
        return _Stored()

    async def get(self, *, path: str) -> bytes:
        from chann_app.services.storage.base import DocumentStoreError

        if path not in self.objects:
            raise DocumentStoreError(f"no stored document at {path}")
        return self.objects[path]

    async def delete(self, *, path: str) -> None:
        self.objects.pop(path, None)


def _install_memory_document_store() -> MemoryDocumentStore:
    """Point the factory at the dictionary, once."""
    from chann_app.services.storage import base as storage_base

    existing = getattr(storage_base, "_agent_test_store", None)
    if existing is not None:
        return existing
    store = MemoryDocumentStore()
    storage_base._agent_test_store = store
    storage_base.get_document_store = lambda *a, **k: store
    return store


class MemoryLine:
    """LINE's push endpoint as a list. Only the push is stood in; replies
    to a webhook never reach this runner."""

    DOWN_PREFIX = "U-line-down"

    def __init__(self):
        self.pushed: list[tuple[str, str, list]] = []

    def _take(self, oa: str, to: str, messages: list) -> list[str]:
        from chann_app.line.client import LineReplyError

        if not to:
            raise LineReplyError("push requires a target LINE user id")
        # CHANN_TEST_LINE_REFUSES=1: LINE refuses everything — the proof that
        # every scenario claiming a send fails when nothing is delivered.
        import os
        if str(to).startswith(self.DOWN_PREFIX) or os.environ.get("CHANN_TEST_LINE_REFUSES") == "1":
            raise LineReplyError("LINE push failed: 500 (agent-test stand-in)")
        self.pushed.append((oa, to, messages))
        return [f"agent-test-msg-{len(self.pushed)}"]

    async def push_text(self, oa, to_line_user_id, text, client=None, quick_reply=None):
        return self._take(oa, to_line_user_id, [text])

    async def push_messages(self, oa, to_line_user_id, messages, client=None):
        return self._take(oa, to_line_user_id, list(messages))


def _install_memory_line() -> MemoryLine:
    """Point the notification road's push at the list, once."""
    from chann_app.services import notify

    existing = getattr(notify, "_agent_test_line", None)
    if existing is not None:
        return existing
    line = MemoryLine()
    notify._agent_test_line = line
    notify.push_text = line.push_text
    notify.push_messages = line.push_messages
    return line
