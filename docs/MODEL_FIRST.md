# Model-first — the governing rule for how a message is understood

> เจ้าของสั่ง 10 ก.ย. 2569: "ต่อจากนี้ให้บันทึก model first แบบนี้ในทุกๆที่ อย่าให้หลุดอีก
> เพราะการใช้กฎแบบเดิมเลย น่าจะทำให้ประสบการณ์ใช้งานแย่ลงจึงเปลี่ยนมาใช้รูปแบบนี้ และคิด
> solution ที่ support แนวทางนี้เป็นหลักเท่านั้น"

This overrides any older guidance in this repo about how chat input is routed. If a
proposal, a plan or a patch conflicts with it, the proposal is wrong.

## The rule

**A sentence a person typed is READ before an action is chosen.** The model reads; the
model **proposes**; the code **validates and acts**. A keyword may no longer decide that a
record changes.

Buttons and postbacks keep the direct path — they name the action and the record already,
so there is nothing to read.

## Why, in the owner's words and in measurements

Matching a word and writing immediately is what made the assistant feel like a command
line. Every example below was reproduced against the real handlers:

| what was typed | what a word-matching rule did |
|---|---|
| `ช่วยเพิ่มลูกค้า สมชาย ใจดี 0812345678 ให้หน่อยครับ` | answered with the nine-topic help guide |
| `ชื่อ สมชาย ใจดี เบอร์ 0812345678` | read as editing your own profile; the phone went into the surname |
| `ลูกค้าขอใบเสนอราคา` | issued a real quotation against a guessed deal |
| `ลูกค้าอยากได้ใบเสนอราคาสำหรับพัดลม 2 ตัว` | issued one **and** deleted the deal's line, putting nothing back |
| `ขอบคุณมากครับ ช่างทำงานดีมาก` | opened a repair job |
| `ตั้งนัดสมชาย` | asked for a date, kept nothing, and started over on the next message |

The same sentences, read by the deployed model, come back correct — including
`ลูกค้าขอใบเสนอราคา` → `{action: create, entity: quote, missing: ["deal_code"]}`, which
knows both that a quote is wanted and that it does not know which deal.

## Before touching any rule — the order

Run `scripts/dev/ask-model.py` first (needs `OR_KEY`, ~$0.0005 a sentence).

0. **Is it broken at all?** `simulate-phrasings` stubs the model as NOT_SURE, which made
   `เปลี่ยนที่อยู่` look broken when the real model reads it correctly. Check the real
   handler too.
1. **Did the message even reach the model?** If a rule dispatched first, the model reading
   it correctly proves nothing — **narrow or delete that rule**. This is the conversion
   itself.
2. **It reached the model and was read wrong** → change `INTENT_SYSTEM_PROMPT`. One
   definition, and the corpus measures it.
3. **Read correctly — did it have enough to work with?** Missing context ("which record is
   this about?") is its own case: send the context. Otherwise the defect is in slot-fill or
   the handler. **Either way, no new trigger word.**
4. **Only if the prompt and the context cannot** → a rule, and only one that can DECLINE or
   ASK.

## Two standing rules

**A word may decline but may not act.** Narrowing a trigger so it stops writing must not
remove its power to refuse. Guard on the wide vocabulary, dispatch on the narrow test:

```python
if any(t in message.lower() for t in QUOTE_CREATE_TRIGGERS):
    guarded = await _guarded_in_context(..., action="quote_create", ...)
    if guarded is not None:
        return guarded                       # a refusal — never a write
    if _is_typed_quote_create(message):      # narrow: head of sentence, or a D- code
        return await _handle_quote_create_direct(...)
```

**Measure per sentence, never on totals.** When the quote branch was narrowed,
`simulate-phrasings` stayed 488·6·0 and agent-test stayed 347/347 while two replies
silently degraded. Play every corpus utterance through the real router before and after and
diff reply + rows written, line by line.

## What the code still does, and must keep doing

The model proposes. Before anything is written, the code checks, in this order:

| layer | question |
|---|---|
| schema | is this a value the system defines? (a phone is not a price; a status must be a real status) |
| permission key | does this person hold the key? |
| **OA allowance** | is this action available on this channel at all? (`_oa_allows` — a separate question from the key, and one that has been forgotten before) |
| intent_guard | is this sentence actually an order, or a report, a question, a refusal? |
| handler | record state and business rules |

None of that moves to the model. Ever.

## Where it stands

**The sales OA reads first (11 ก.ย. 2569).** `_route_chat_message` asks the model before
any keyword table for a fresh sentence on `ctx.oa == "sales"`; the tables run only when
the model answers `suggest`. What stays deterministic, by design: a pending answer, small
talk, a greeting, a rich-menu tile, the help menu. Measured over the 1,168-utterance
corpus through the real router:

| OA | model reads | rule decides | share |
|---|---|---|---|
| sales | 492 | 48 | **91%** (was 27%) |
| technician | 51 | 233 | 18% |
| customer | 12 | 332 | 3% |

Overall **555/1168 (48%)**, from 218. The same inversion is the plan for the technician
OA; the customer OA's rule (what a customer may do alone) is in `SESSION_HANDOFF.md` and
its conversion means classifying into that road's actions, not widening them.

**The corpus carries the model's real answers.** `tests/unit/chat_corpus.py` entries take
`ai={...}` — since the inversion, 297 sales entries hold the verbatim answer the deployed
model gave (`scripts/dev/fill-corpus-answers.py`, ~$0.15 a run). `test_chat_corpus.py`
therefore measures *what the model returns and what the road does with it*, not a
hand-written stub. Re-run the filler for an entry whenever the prompt changes what the
model would say about it (clear its `ai=` and run the script).

What the real answers found on the first pass (sales understood share 83% → 96% after):
`read/profile` answered as an edit refusal; `read/report` with `type: "sales_graph"` given
the text summary; `read/customer {customer_code}` listed instead of shown; `reject/approval`
held by the guard because the model road passed it no trigger words ("ไม่ผ่าน" IS the
verb); `update/quote` with a `D-` code; `read/deal {status: "lost"}` listing everything.
Each was a hole in the model ROAD, fixed there — no keyword table was touched.

What stays honest in the baseline (`chat_corpus_baseline.json`): 21 sales sentences now
classify `partial` (the model road asks or lands one handler away) and 5 `ai_fallback`
(the model misreads: "ดีลค้าง" as jobs, "ใครว่างบ้าง" as agenda). Those are prompt work,
measured with `evaluate-model.py`, never a rule.

## What a stubbed model cannot tell you

Every harness in this repo answers the model call with a canned string:
`test_phase6_chat._ai`, `simulate-phrasings`, `measure-road-share`,
`map-rule-roads`. They prove which ROAD a sentence takes and what the handlers do
with it. They cannot prove the model reads it correctly — and the stubs are written
by the same hand as the code, so they agree with it by construction.

Owner, 11 ก.ย. 2569: *"ตอนนี้ที่ทดสอบคือยังทำตามแนวทาง model first ใช่ไหม เห็นทดสอบ
แต่ credit ไม่ได้ถูกใช้เลย"*. Asking the deployed model the same day found four bugs
in code that had already passed its tests:

| sentence | the model returns | the code expected |
|---|---|---|
| `ดีล D-2026-0001 ลูกค้าตกลงซื้อแล้ว` | `"status": "won"` | `"stage"` |
| `สร้างกลุ่มขาย เหนือ` | `missing: ["members"]` | nothing missing |
| `ลบกลุ่มขาย เหนือ` | `entity:"team"`, no `scope` | `scope:"sales"` |
| `เพิ่ม สมชาย เข้ากลุ่มขาย เหนือ` | `entity:"team"`, no `scope` | `scope:"sales"` |

The last two would have written to the TECHNICIAN table — the defect that round had
just fixed, re-entering through the model road.

So: **a prompt change is invisible to the offline suite.** The only regression test for
one is `scripts/agent-test/evaluate-model.py --run` (49 cases × 3 repeats, ~$0.10).
Run it before and after, and compare `varying_outputs` as well as pass/fail — a case
whose ACTION varies between repeats is a coin flip in production.

`scripts/dev/ask-model.py` answers the one-sentence question. The key is never in the
environment by default; ask for it.

## Rejected by measurement — do not re-propose without new evidence

- Deleting `_appointment_net` before the context block exists: loses five real appointments
  (`พรุ่งนี้ 10 โมง โทรหาสมชาย` among them).
- Routing deal-stage sentences (`ปิดสำเร็จ`) through the model *instead of* the typed road:
  the prompt documents no way to express a stage transition, so there is no model-road path
  to `transition_deal_stage`. (11 ก.ย. 2569 — the handler now ACCEPTS a stage when the model
  sends one anyway, and hands it to the same `_handle_deal_stage_command`. That is a net,
  not a conversion: the typed road still decides these sentences, and the entry above still
  stands until the prompt says how to express a transition.)
- Moving `_sales_interest_item` to the model road: the deal-create field shape carries no
  product or quantity, so the model's natural answer cannot be executed.
