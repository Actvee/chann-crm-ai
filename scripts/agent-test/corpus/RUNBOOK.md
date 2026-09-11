# Measuring an architecture change, or a model change, on the same ground

351 labelled cases · 514 turns · 136 multi-turn, across the three OAs.
Written 10 ก.ย. 2569 for the owner's question about whether the assistant
reads free text well enough, and kept because that question comes back
every time either the routing or the model changes.

Each case carries what SHOULD happen — not only the reply, but whether a
write is allowed and to what. That is the point: a reply that sounds right
over a row that should not exist is the failure this corpus exists to
catch.

## The two arms

Both run the same cases through the same handlers. The only difference is
who writes the model's answer.

    # A — stub. The corpus authors the model's answers. Free, fast,
    #     deterministic. Says what the pipeline DOES with a given answer;
    #     says nothing about what a real model would answer.
    /tmp/dv/bin/python scripts/agent-test/probe.py \
        scripts/agent-test/corpus/corpus-sales.json --out /tmp/arm-stub.jsonl

    # B — the configured model. Billable. Needs the key already in the
    #     environment; the script never prints it.
    export OPENROUTER_API_KEY=... OPENROUTER_MODEL=...
    /tmp/dv/bin/python scripts/agent-test/probe.py \
        scripts/agent-test/corpus/corpus-sales.json --real-model --out /tmp/arm-real.jsonl

Everything else about the run is identical, so the two files are directly
comparable, per turn.

## What to report — six numbers, never one score

An aggregate hides the thing that matters. Report, per arm and per OA:

1. **task accuracy** — against `must_write` / `label.right_write` /
   `must_ask`, not only `expect.no_mutation`. The machine check cannot see
   a right write on the wrong record, or a question that was never asked.
2. **unintended mutations** — writes the case forbids. Split by whether
   the model was consulted, because that decides whose problem it is.
3. **unnecessary clarifying questions** — turns that asked with the
   information already in hand. This is the over-blocking counter, and it
   is the one that goes up when a guard is tightened too far.
4. **multi-turn continuity** — of the 136 multi-turn cases, how many had
   turn 2+ resolve a reference to turn 1 correctly.
5. **latency** p50/p95, per OA. `turns[].latency_ms` for the whole turn;
   `turns[].model_calls[].latency_ms` for the provider alone.
6. **cost per completed task**, not per message: a message answered wrongly
   three times is one task's cost, not three.

## Reading a result honestly

- The stub arm cannot produce a "the model misread it" failure. If every
  failure in arm A is a rule deciding before the model, that is a fact
  about the routing and says nothing about the model either way.
- Cases worded with "พรุ่งนี้"/"วันนี้"/"วันเสาร์" move with the calendar
  (`local_today()`), so record the run date. This corpus was labelled on
  2026-09-10.
- The data tier is `FakeDataClient`. Constraints the real tier enforces —
  the phone uniqueness rule in `phase9.py:270`, foreign keys — can refuse
  in production what the fake accepts. A write recorded here is a write
  ATTEMPTED, which is what matters for "did it decide to write", but it is
  not proof the row landed.
- The corpus was written from the owner's reports and from reading the
  code. It has selection bias by construction and its per-OA counts
  (191/80/80) do not reflect real traffic.

## The bar, set before the run

Declare it first, and do not move it afterwards.

| Change | Passes when | Fails when |
|---|---|---|
| a guard | unintended mutations fall AND legitimate writes stay at 100% | one legitimate write is blocked |
| context sent to the model | continuity rises, no regression elsewhere | any previously-passing case fails |
| a model swap | the residual failures the old model produced are fixed, on the SAME corpus and the SAME prompt | latency p95 or cost per task rises more than the accuracy gained |

Hold the model fixed while testing the architecture, and hold the
architecture fixed while testing the model. Changing both at once measures
neither.

## Asking the model itself — `corpus-model-check.json`

A stub answering "I have no reading for this" makes the pipeline look
broken when it is not, and makes a narrowing look safe when it is not.
This file holds the sentences whose ANSWER depends on how the deployed
model reads them, and it is meant to be run against the real model:

```bash
OPENROUTER_API_KEY=…  OPENROUTER_MODEL=google/gemini-3.1-flash-lite \
  python3 scripts/agent-test/probe.py scripts/agent-test/corpus/corpus-model-check.json \
  --real-model --out /tmp/model-check.jsonl --summary
```

About $0.0005 a sentence — the whole file is roughly one satang. The key
comes from the environment and is never printed; nothing here reads it
from the repo.

**Run it before narrowing any dispatch.** `docs/MODEL_FIRST.md` step 1
asks whether the message even reached the model: the probe answers that
directly (`ai_calls` per turn), and it records the rows written as well as
the reply, which is the measurement the owner asked for from the start.

What the first run found (11 ก.ย. 2569, 20 real calls, 0 forbidden
writes) — each one invisible to a stubbed run:

* `ลบลูกค้ายังไง` and `ลบลูกค้าสมชายไปหรือยัง` never reach the model at
  all; the `ลบลูกค้า` trigger claims them and answers
  `ไม่พบลูกค้าชื่อ "ยังไง" ในบริษัทนี้` — the question word searched as a
  person's name.
* `ตั้งทีมขายชื่อ ทีมกรุงเทพ` really does write `create_technician_team`.
  The sales-group request lands in the technician-team table.
* `เพิ่มสมศักดิ์เข้าทีมขาย ภาคเหนือ` and `ลบทีมขาย` never reach the model
  either — a regex claims them first, which is why the unguarded team
  delete is reachable from a sentence about a sales team.
* `ใครลบลูกค้ารายนี้` → `ไม่พบลูกค้ารหัส ลูกค้ารายนี้`;
  `ใครเปลี่ยนข้อมูลบริษัทล่าสุด` → the company profile;
  `ลูกค้าที่ยังไม่ได้ติดต่อมีใครบ้าง` → the appointment list. Three
  "who did this" / filtered-list shapes with no handler behind them.

Add a case here whenever a judgement call turns on what the model would
say. Keep `expect.no_mutation` honest: it is what makes a forbidden write
visible.
