"""The illustrated-guide pictures are drawn by scripts/dev/render-guide-images.py
from the fonts beside it, so a fresh clone can regenerate them. This keeps
the renderer, the slot list and the shipped files in step."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/dev/render-guide-images.py"

pytest.importorskip("PIL")


def _load():
    spec = importlib.util.spec_from_file_location("render_guide_images", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_slot_has_a_scene_and_a_shipped_file():
    module = _load()
    slots = set(json.loads((ROOT / "application/chann_app/help_images.json").read_text(encoding="utf-8"))["images"])
    scenes = {slot for group in module.SCENES.values() for slot in group}
    assert scenes == slots
    for slot in slots:
        assert (ROOT / "application/chann_app/static/help" / f"{slot}.png").is_file(), slot
    assert module.check() == []


def test_the_fonts_ship_with_the_script():
    for name in ("Sarabun-Regular.ttf", "Sarabun-Bold.ttf"):
        assert (ROOT / "scripts/dev/guide-fonts" / name).is_file(), name
    assert (ROOT / "scripts/dev/guide-fonts/README.md").is_file()


def test_rendering_produces_square_pngs(tmp_path):
    from PIL import Image

    module = _load()
    module.main(tmp_path, only=("customer-link", "sales-crm"))
    for slot in ("customer-link", "sales-crm"):
        with Image.open(tmp_path / f"{slot}.png") as im:
            assert im.size == (module.W, module.H) and im.format == "PNG"
