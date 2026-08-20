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
PROVIDER_PREFERENCE: dict[str, list[str]] = {
    "qwen": ["fireworks", "together"],
    "deepseek": ["deepseek_official", "fireworks"],
}


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
