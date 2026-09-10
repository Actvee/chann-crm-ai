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


async def _run(sentences: list[str], *, oa: str, role: str, model: str, language: str) -> int:
    from chann_app.config import settings

    settings.openrouter_api_key = _key()
    settings.openrouter_model = model or os.environ.get("OPENROUTER_MODEL") or DEV_MODEL

    from chann_app.services.ai.intent import parse_intent
    from chann_data.permissions import DEFAULT_ROLE_TEMPLATES

    keys = sorted(DEFAULT_ROLE_TEMPLATES.get(role, DEFAULT_ROLE_TEMPLATES["admin"]))
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sentences", nargs="*", help="one or more messages to read")
    parser.add_argument("--file", help="a file of sentences, one per line (# starts a comment)")
    parser.add_argument("--oa", default="sales", choices=("sales", "technician", "customer"))
    parser.add_argument("--role", default="", help="defaults to the OA's usual role")
    parser.add_argument("--model", default="", help="override the configured model")
    parser.add_argument("--language", default="th")
    args = parser.parse_args()

    sentences = list(args.sentences)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                sentences.append(line)
    if not sentences:
        parser.error("give at least one sentence, or --file")

    role = args.role or {"sales": "sales", "technician": "technician", "customer": "customer"}[args.oa]
    return asyncio.run(_run(sentences, oa=args.oa, role=role, model=args.model, language=args.language))


if __name__ == "__main__":
    raise SystemExit(main())
