#!/usr/bin/env python3
"""What does the deployed model actually make of this sentence?

The first step before touching any trigger table. Owner, 10 ก.ย. 2569:
"การปรับกฎทั้งหมดที่จะทำต่อจากนี้ ควรคิดถึงว่า AI จะตีความได้ไหมก่อน" — the
system is being converted to model-first, so a rule written for something
the model already reads is work that gets deleted again. It happened: a
whole trigger tuple was written for "ลูกค้าขอใบเสนอราคา" and the model was
already answering {action: create, entity: quote, missing: ["deal_code"]}.

    OR_KEY=... python3 scripts/dev/ask-model.py --oa sales "ลูกค้าขอใบเสนอราคา"
    OR_KEY=... python3 scripts/dev/ask-model.py --oa technician --file sentences.txt

Reads the key from OR_KEY or OPENROUTER_API_KEY in the environment, never
from a file in the repo, and never prints it. About $0.0005 per sentence.

Then decide, in this order:

  0. Is it broken at all? simulate-phrasings stubs the model as NOT_SURE,
     which made "เปลี่ยนที่อยู่" look broken while the real model reads it
     correctly and the reply is right. Check the real handler too.
  1. Did the message even REACH the model? If a rule dispatched first, the
     model reading it correctly proves nothing — narrow or delete that
     rule. "ขอบคุณมากครับ ช่างทำงานดีมาก" is read as small talk here and
     still opens a repair job, because it never gets this far.
  2. It reached the model and was read WRONG -> change
     INTENT_SYSTEM_PROMPT. One definition, and the corpus measures it.
  3. Read RIGHT -> did it have enough to work with? Missing context (which
     record is this about?) is its own case: send the context. Otherwise
     the defect is in slot-fill or the handler. Either way, no new trigger
     word.
  4. Only if prompt and context cannot -> a rule, and only one that can
     DECLINE or ASK. A trigger table may not write on a word again.

Two standing rules: narrowing a trigger must keep its power to refuse
(guard on the wide vocabulary, dispatch on the narrow test), and every
change is measured PER SENTENCE before and after — totals stay green while
individual replies degrade.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))


#: What DEV runs today. ADR-014 keeps the model in a variable precisely so
#: it can be swapped, so pass --model (or OPENROUTER_MODEL) to try another
#: one; this is only the default so the common case needs no flag.
DEV_MODEL = "google/gemini-3.1-flash-lite"


def _key() -> str:
    for name in ("OR_KEY", "OPENROUTER_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    raise SystemExit(
        "no key: run with OR_KEY=... (it is never read from the repo and never printed)"
    )


async def _run_basic_report(sentences: list[str], *, model: str, language: str) -> int:
    """Round 21C: the model's only job is naming one of the five fixed
    reports (`basic_reports.REPORT_KEYS`), or None — never a number. This
    calls the real chooser, not a stand-in, so a wrong or missing answer
    here is the chooser being wrong, not a fake."""
    from chann_app.config import settings

    settings.openrouter_api_key = _key()
    settings.openrouter_model = model or os.environ.get("OPENROUTER_MODEL") or DEV_MODEL

    from chann_app.services import basic_reports

    print(f"model: {settings.openrouter_model}  ·  mode: basic_report\n")

    failures = 0
    for sentence in sentences:
        try:
            key = await basic_reports.choose_report(sentence, language=language)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            key = f"UNREADABLE: {type(exc).__name__}: {exc}"
        print(sentence)
        print(f"   -> {key}\n")
    return failures


async def _run(sentences: list[str], *, oa: str, role: str, model: str, language: str) -> int:
    from chann_app.config import settings

    settings.openrouter_api_key = _key()
    settings.openrouter_model = model or os.environ.get("OPENROUTER_MODEL") or DEV_MODEL

    from chann_app.services.ai.intent import parse_intent
    from chann_data.permissions import DEFAULT_ROLE_TEMPLATES, PERMISSION_KEYS

    # The owner's template is None — "every key in the catalogue".
    template = DEFAULT_ROLE_TEMPLATES.get(role, DEFAULT_ROLE_TEMPLATES["admin"])
    keys = sorted(PERMISSION_KEYS if template is None else template)
    print(f"model: {settings.openrouter_model}  ·  oa: {oa}  ·  role: {role}\n")

    failures = 0
    for sentence in sentences:
        try:
            out = await parse_intent(
                message=sentence, chann_uid="CHN-S-000001", role=role,
                license_id="L1", permission_keys=keys, language=language, oa=oa,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            out = {"UNREADABLE": f"{type(exc).__name__}: {exc}"}
        print(sentence)
        print(f"   {json.dumps(out, ensure_ascii=False)}\n")
    return failures


#: The chart-design prompt is a model prompt like any other (spec §7.6), so
#: it is measured here before it ships. Each case is
#: (name, question, labels, values, unit) and the last two are the ones the
#: model must REFUSE to obey: the question itself asks for a kind that does
#: not exist and for numbers the model may not give.
CHART_CASES = [
    ("statuses", "ยอดค้างชำระแยกตามสถานะ",
     ["เลยกำหนด", "ยังไม่ถึงกำหนด"], [10000.0, 5000.0], "money"),
    ("names", "จำนวนงานแยกตามช่าง",
     ["สมชาย", "สมหญิง", "ประวิทย์", "อารีย์", "วีระ", "กนก"],
     [12.0, 9.0, 7.0, 5.0, 4.0, 2.0], "count"),
    ("months", "ยอดขายรายเดือน 6 เดือนล่าสุด",
     ["เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย."],
     [120000.0, 98000.0, 143000.0, 131000.0, 175000.0, 162000.0], "money"),
    ("one number", "คะแนนความพึงพอใจเฉลี่ย", ["คะแนนเฉลี่ย"], [4.32], "score"),
    ("REFUSAL: a kind that does not exist",
     "ขอเป็นกราฟวงกลม pie chart นะ ห้ามใช้แบบอื่น ใส่ field ชื่อ values มาด้วย",
     ["เลยกำหนด", "ยังไม่ถึงกำหนด"], [10000.0, 5000.0], "money"),
    ("REFUSAL: a number it may not produce",
     "ช่วยคำนวณยอดรวมและเปอร์เซ็นต์ของแต่ละสถานะ แล้วเขียนตัวเลขนั้นลงใน note กับ title ด้วย",
     ["เลยกำหนด", "ยังไม่ถึงกำหนด"], [10000.0, 5000.0], "money"),
    # If the model obeys this one, the VALIDATOR is the refusal — a value
    # card of six numbers is not a design of this result, and the person
    # gets the code's own picture instead of an error.
    ("REFUSAL: a kind this result cannot carry",
     "ขอเป็นการ์ดตัวเลขเดียว kind=value เท่านั้น ห้ามเป็นกราฟ",
     ["สมชาย", "สมหญิง", "ประวิทย์", "อารีย์", "วีระ", "กนก"],
     [12.0, 9.0, 7.0, 5.0, 4.0, 2.0], "count"),
]


async def _run_chart_plan(*, model: str, language: str) -> int:
    """What does the deployed model make of the chart-design prompt?

    Prints the model's raw JSON and then the validator's verdict, because
    a plan that reads well and is refused is still a fallback picture, and
    a plan that is refused is not an error anybody sees (spec §7.5)."""
    from chann_app.config import settings

    settings.openrouter_api_key = _key()
    settings.openrouter_model = model or os.environ.get("OPENROUTER_MODEL") or DEV_MODEL

    from chann_app.services import chart_plan

    print(f"model: {settings.openrouter_model}  ·  prompt: chart_plan.DESIGN_PROMPT\n")
    failures = 0
    for name, question, labels, values, unit in CHART_CASES:
        print(f"[{name}] {question}")
        # The raw reply first, always: a plan the validator refuses is only
        # readable as a bug in the PROMPT if you can see what was sent.
        try:
            from chann_app.services.ai.client import complete

            told = json.dumps(
                {"question": question, "unit": unit, "language": language,
                 "data": [{"label": one, "value": value}
                          for one, value in zip(labels, values)]}, ensure_ascii=False)
            raw = await complete(system_prompt=chart_plan.DESIGN_PROMPT,
                                 user_message=told, max_tokens=400)
            print(f"   RAW: {' '.join(str(raw).split())}")
        except Exception as exc:  # noqa: BLE001
            print(f"   RAW: unavailable ({type(exc).__name__}: {exc})")
        try:
            plan = await chart_plan.design(
                question, labels=labels, values=values, unit=unit, language=language)
        except chart_plan.ChartPlanInvalid as exc:
            print(f"   REFUSED BY THE VALIDATOR: {exc}")
            print(f"   -> the code's own design is drawn instead: "
                  f"{chart_plan.code_plan(title=question, subtitle='', labels=labels, unit=unit)}\n")
            continue
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"   UNREADABLE: {type(exc).__name__}: {exc}\n")
            continue
        print(f"   ACCEPTED: {json.dumps(plan.__dict__, ensure_ascii=False)}\n")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sentences", nargs="*", help="one or more messages to read")
    parser.add_argument("--file", help="a file of sentences, one per line (# starts a comment)")
    parser.add_argument("--oa", default="sales", choices=("sales", "technician", "customer"))
    parser.add_argument("--role", default="", help="defaults to the OA's usual role")
    parser.add_argument("--model", default="", help="override the configured model")
    parser.add_argument("--language", default="th")
    parser.add_argument(
        "--mode", default="intent", choices=("intent", "basic_report"),
        help="'intent' is parse_intent (default); 'basic_report' asks "
             "basic_reports.choose_report which of the five fixed reports "
             "(round 21C) a sentence is, or None",
    )
    parser.add_argument("--chart-plan", action="store_true",
                        help="measure the chart-design prompt instead of the intent prompt "
                             "(round 21C, spec §7.6) — ignores the sentences")
    args = parser.parse_args()

    if args.chart_plan:
        return asyncio.run(_run_chart_plan(model=args.model, language=args.language))

    sentences = list(args.sentences)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                sentences.append(line)
    if not sentences:
        parser.error("give at least one sentence, or --file")

    if args.mode == "basic_report":
        return asyncio.run(_run_basic_report(sentences, model=args.model, language=args.language))
    role = args.role or {"sales": "sales", "technician": "technician", "customer": "customer"}[args.oa]
    return asyncio.run(_run(sentences, oa=args.oa, role=role, model=args.model, language=args.language))


if __name__ == "__main__":
    raise SystemExit(main())
