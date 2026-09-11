#!/usr/bin/env python3
"""Write the deployed model's own answers into the corpus, once.

tests/unit/test_chat_corpus.py plays every utterance with the model stubbed.
An entry that reaches the model needs an `ai=` — the crafted answer the stub
returns — or it classifies as a shrug. Before 11 ก.ย. 2569 a handful had one,
hand-written; after the sales OA started reading first, hundreds reach the
model, and a hand-written answer is a guess about what production says.

So this asks production. For every sales entry that (a) reaches the model
when played through the real router and (b) carries no `ai=` yet, it calls
the deployed model once and writes the verbatim answer into chat_corpus.py.
~$0.0005 a sentence. The test then measures what the model actually returns,
which is the only thing worth measuring.

    OR_KEY=... python3 scripts/dev/fill-corpus-answers.py          # writes
    OR_KEY=... python3 scripts/dev/fill-corpus-answers.py --dry    # lists
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import httpx  # noqa: E402

from chann_app.config import settings  # noqa: E402

settings.openrouter_api_key = "test"
settings.openrouter_model = "test"

from chann_app.services import chat as C  # noqa: E402
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES  # noqa: E402
from test_phase6_chat import FakeDataClient, _ai, _ctx  # noqa: E402

import chat_corpus  # noqa: E402

CORPUS = ROOT / "tests" / "unit" / "chat_corpus.py"
SUGGEST = json.dumps({"action": "suggest", "entity": "", "fields": {}, "missing": []})
DEV_MODEL = "google/gemini-3.1-flash-lite"
CUSTOMER = {"id": "CUST-1", "customer_id": "C-2026-0001", "first_name": "สมชาย",
            "last_name": "ใจดี", "phone": "0812345678", "stage": "lead"}
DEAL = {"id": "DEAL-1", "deal_id": "D-2026-0001", "stage": "proposed", "contact_id": "CUST-1",
        "notes": None, "products": [{"id": "L1", "product_name": "พัดลม",
                                     "quoted_unit_price": "1200", "qty": 1}]}
QUOTE = {"id": "QUOTE-1", "quote_id": "Q-2026-0001", "status": "sent", "deal_id": "DEAL-1",
         "contact_id": "CUST-1", "items": [], "total": "1000.00"}


class _Counting(httpx.AsyncBaseTransport):
    def __init__(self, inner):
        self.inner, self.n = inner, 0

    async def handle_async_request(self, request):
        self.n += 1
        return await self.inner.handle_async_request(request)


async def _reaches_model(case: dict) -> bool:
    client = FakeDataClient(role="sales", permission_keys=sorted(DEFAULT_ROLE_TEMPLATES["admin"]),
                            customers=[copy.deepcopy(CUSTOMER)], deals=[copy.deepcopy(DEAL)],
                            quotes=[copy.deepcopy(QUOTE)])
    calls = 0
    for message in list(case.get("pre") or []) + [case["text"]]:
        transport = _Counting(_ai(SUGGEST))
        try:
            await C.handle_chat_message(
                client, ctx=_ctx(primary_role="sales", oa="sales"), message=message,
                language="th", ai_client=httpx.AsyncClient(transport=transport),
            )
        except Exception:  # noqa: BLE001
            pass
        calls = transport.n
    return calls > 0


async def _ask(text: str) -> dict:
    from chann_app.services.ai.intent import parse_intent
    return await parse_intent(
        message=text, chann_uid="CHN-S-000001", role="sales", license_id="L1",
        permission_keys=sorted(DEFAULT_ROLE_TEMPLATES["admin"]), language="th", oa="sales",
    )


def _write(text: str, answer: dict) -> bool:
    src = CORPUS.read_text(encoding="utf-8")
    pat = re.compile(r'(_e\("' + re.escape(text) + r'", "s\.[a-z_]+", "[^"]*")\)')
    ai = json.dumps(answer, ensure_ascii=False).replace("null", "None").replace("true", "True").replace("false", "False")
    new, n = pat.subn(lambda m: m.group(1) + ",\n       ai=" + ai + ")", src, count=1)
    if n:
        CORPUS.write_text(new, encoding="utf-8")
    return bool(n)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    todo = [c for c in chat_corpus.SALES if "ai" not in c and not c.get("pre")]
    reaching = [c for c in todo if await _reaches_model(c)]
    print(f"{len(chat_corpus.SALES)} sales entries · {len(todo)} without an answer · "
          f"{len(reaching)} of those reach the model")
    if args.dry:
        for c in reaching:
            print("  ", c["text"])
        return 0

    key = os.environ.get("OR_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("no key: set OR_KEY", file=sys.stderr)
        return 2
    settings.openrouter_api_key = key
    settings.openrouter_model = os.environ.get("OPENROUTER_MODEL") or DEV_MODEL

    written = skipped = 0
    for c in reaching:
        try:
            answer = await _ask(c["text"])
        except Exception as exc:  # noqa: BLE001
            print(f"  UNREADABLE {c['text'][:40]}: {type(exc).__name__}")
            skipped += 1
            continue
        answer.pop("suggestions", None)
        if _write(c["text"], answer):
            written += 1
            print(f"  {c['text'][:40]:42} <- {json.dumps(answer, ensure_ascii=False)[:70]}")
        else:
            skipped += 1
            print(f"  NOT MATCHED {c['text'][:40]}")
    print(f"\n{written} answers written, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
