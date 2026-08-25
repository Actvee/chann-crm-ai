"""OpenRouter provider preference and model tiers — Master Spec 4.3/4.4.

Master Spec 4.4 sketches this as config/openrouter.yaml. It is a Python module
instead: the Application Tier has no YAML parser in requirements.txt, and the
file ships inside the immutable image either way, so YAML would add a
dependency without making anything operator-tunable at runtime. The thing that
IS tunable at runtime stays tunable — the model slug still comes from the
OPENROUTER_MODEL env var, which is the switch ADR-014's fallback criteria
actually pull (4.5: "เปลี่ยน OPENROUTER_MODEL env var, ไม่กระทบโค้ด").
"""
from __future__ import annotations

# Ordered provider preference per model family. OpenRouter takes this as
# provider.order in the request body and falls through the list itself.
#
# EMPTY ON PURPOSE. The slugs originally here ("fireworks", "together") were
# copied from the spec and never checked against OpenRouter; a live call showed
# the request was actually served by "DeepInfra", i.e. neither preferred
# provider matched and only allow_fallbacks saved it. A preference list that
# silently matches nothing is worse than none: it looks like routing control
# while providing none, and would hard-fail the moment allow_fallbacks was
# turned off.
#
# To populate this, read the provider slugs off a real response
# (`.provider` in the OpenRouter JSON) or the model's page on openrouter.ai,
# and add only slugs observed to serve that model.
PROVIDER_PREFERENCE: dict[str, list[str]] = {}


def provider_block(model: str) -> dict | None:
    """Translate a model slug into OpenRouter's provider routing block.

    Returns None for a model family we have no preference for, which lets
    OpenRouter route it however it likes rather than pinning it to providers
    that may not even serve that model.
    """
    family = model.split("/", 1)[0].strip().lower() if "/" in model else ""
    order = PROVIDER_PREFERENCE.get(family)
    if not order:
        return None
    return {"order": list(order), "allow_fallbacks": True}
