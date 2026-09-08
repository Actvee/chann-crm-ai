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
    return REPO_ROOT
