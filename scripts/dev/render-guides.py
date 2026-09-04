#!/usr/bin/env python3
"""Render the user guides and the permission handout from their single
source (application/chann_app/services/guides.py, data/chann_data/permissions.py)
into docs/guides/ — the files the owner hands to people and feeds to an
image generator.

Run after any change to the guides or the permission catalogue:

    python3 scripts/dev/render-guides.py          # write
    python3 scripts/dev/render-guides.py --check  # exit 1 if docs are stale

tests/unit/test_guides.py runs the --check so a stale handout fails CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))

from chann_app.services.guides import GUIDES, guide_as_markdown  # noqa: E402
from chann_data.permissions import PERMISSION_KEYS, describe  # noqa: E402

OUT = ROOT / "docs" / "guides"

GROUP_TH = {
    "customer": "ลูกค้า", "deal": "ดีล", "quote": "ใบเสนอราคา", "product": "สินค้า",
    "note": "บันทึก", "followup": "การติดตาม", "ticket": "งานซ่อม", "service_report": "รายงานการซ่อม",
    "warranty": "รับประกัน", "approval": "การอนุมัติ", "team": "ทีมช่าง", "member": "สมาชิก",
    "role": "บทบาท", "setting": "ตั้งค่าร้าน", "report": "รายงานสรุป", "assignment_rule": "กฎมอบหมายงาน",
    "billing": "การเรียกเก็บเงิน", "audit_log": "ประวัติการใช้งาน", "chat": "แชท", "document": "เอกสาร",
    "platform": "แพลตฟอร์ม", "template": "แบบฟอร์ม", "survey": "แบบประเมิน",
}


def permissions_markdown() -> str:
    groups: dict[str, list[str]] = {}
    for key in sorted(PERMISSION_KEYS):
        if key.startswith("platform.admin."):
            continue
        group = key.split(".", 1)[0]
        groups.setdefault(group, []).append(key)
    out = [
        "# สิทธิ์การใช้งาน — คู่มือสำหรับเจ้าของร้าน",
        "",
        "สิทธิ์ตั้งได้ที่แดชบอร์ด Sale > บทบาทและทีม (ต้องมีสิทธิ์ «จัดการบทบาท») "
        "เมื่อพนักงานพิมพ์คำสั่งที่ไม่มีสิทธิ์ ระบบจะบอกชื่อสิทธิ์ที่ต้องขอ ตามชื่อในตารางนี้",
        "",
        "[IMAGE: permissions-overview — หน้าจอ 'บทบาทและทีม' ธีมเขียว แสดงบทบาท เจ้าของ / CS / ขาย / ช่าง "
        "และ toggle สิทธิ์เป็นหมวด ลูกค้า ดีล งานซ่อม รายงาน]",
        "",
    ]
    for group, keys in groups.items():
        out.append(f"## {GROUP_TH.get(group, group)}")
        out.append("")
        out.append("| สิทธิ์ | ความหมาย |")
        out.append("|---|---|")
        for key in keys:
            out.append(f"| `{key}` | {describe(key, 'th')} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def images_template() -> str:
    import json

    slots = {step["image"]: f"/api/v1/guide/images/{step['image']}.png" for guide in GUIDES.values() for step in guide["steps"]}
    slots["permissions-overview"] = "/api/v1/guide/images/permissions-overview.png"
    return json.dumps({"_comment": "รูปต่อ slot: path ใน Application (/api/v1/guide/images/<slot>.png, ไฟล์ใน chann_app/static/help) "
                       "หรือ URL https เต็ม — แก้ทั้งสองไฟล์ให้เหมือนกัน", "images": slots},
                      ensure_ascii=False, indent=2) + "\n"


def expected_files() -> dict[Path, str]:
    files = {OUT / f"{oa}.md": guide_as_markdown(oa) for oa in GUIDES}
    files[OUT / "PERMISSIONS.md"] = permissions_markdown()
    files[OUT / "help_images.template.json"] = images_template()
    return files


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for path, content in expected_files().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            stale.append(path)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    if check:
        if stale:
            print("stale guide docs — run scripts/dev/render-guides.py:")
            for p in stale:
                print(f"  {p.relative_to(ROOT)}")
            return 1
        print("docs/guides are current")
        return 0
    print(f"wrote {len(stale)} file(s)" if stale else "docs/guides already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
