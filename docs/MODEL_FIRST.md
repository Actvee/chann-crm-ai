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

Measured over 1,161 corpus utterances through the real router: **the rule road still decides
83%** of sentences without consulting the model. Closing holes does not move that number;
moving the reading does.

The conversion order, largest blocker first:

1. ~~Make the conversational refs tenant-safe~~ (in progress) — the model cannot be given
   "the record in focus" until that record is scoped per shop, because a cached ref keyed
   only on `(person, OA)` already writes across shops.
2. Send the record in focus to the model, per-OA and per-shop.
3. Narrow the read-only deictic branches ("ลูกค้าคนนี้", "ดีลล่าสุด").
4. Narrow the branches that only offer a confirmation.
5. Narrow the write branches, one per ship, largest last.

Each step is measured with a per-sentence A/B and ships on its own.

## Rejected by measurement — do not re-propose without new evidence

- Deleting `_appointment_net` before the context block exists: loses five real appointments
  (`พรุ่งนี้ 10 โมง โทรหาสมชาย` among them).
- Routing deal-stage sentences (`ปิดสำเร็จ`) through the model: the prompt documents no way
  to express a stage transition, so there is no model-road path to `transition_deal_stage`.
- Moving `_sales_interest_item` to the model road: the deal-create field shape carries no
  product or quantity, so the model's natural answer cannot be executed.
