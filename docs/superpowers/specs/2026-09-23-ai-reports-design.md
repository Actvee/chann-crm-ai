# AI reports that are right (round 21C) — design

**Owner's ask (23 ก.ย. 2569):** *"ยอดมูลค่าดีลทั้งหมดขึ้น 0 ในรูปแบบใหม่"* → the
diagnosis in `docs/superpowers/specs/2026-09-23-ai-reports-diagnosis.md`, then five
decisions taken on the spot:

1. **มูลค่าดีล = `amount` ที่พิมพ์ไว้ชนะ ถ้าไม่มีค่อยรวมรายการสินค้า** — one definition, one
   helper, everywhere.
2. **ใบแจ้งหนี้ที่ชำระครบแล้ว ปิดดีลเป็น "ปิดสำเร็จ" ให้เอง** and writes its line items and
   total back onto the deal. (Owner's correction, same day, verbatim:
   *"ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอนชำระเงินแล้ว"* — the trigger is the payment that settles
   the bill, **not** issuing it.)
3. **"invoice ต้องแก้ไขรายการสินค้าหรือข้อมูลต่างๆได้เหมือน quote ด้วย"**
4. **เพิ่ม `deals.closed_at`** (migration `0038_deal_closed_at`).
5. **รูปที่ AI ออกแบบ แต่ไม่ได้คำนวณ** — server computes, the model returns a validated
   chart plan, we render HTML/SVG and screenshot it through SmartBrowz `preview_image`;
   image-generation models are rejected.
6. **รายงานพื้นฐานห้ามกินโควตา AI** — credits go only to a model-designed picture or an
   ad-hoc question.
7. Five fixed reports, chosen by the model, computed by code.
8. **ส่งเอกสารให้ลูกค้าทางไลน์ได้เลยถ้าผูกไลน์ไว้แล้ว** (owner, same day, verbatim):
   *"ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้วสำหรับใบเสนอราคาหรือ invoice ต่างๆสามารถออกคำสั่งหรือกดปุ่ม
   ส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มีผูกก็แจ้งว่าไม่ได้หรือทำปุ่มเป็นไม่พร้อมใช้งาน"* (§8.5)

Spec of the round before this one: `docs/superpowers/specs/2026-09-23-external-api-design.md`
(21B, migration `0037_api_keys`). 21C sits on top of it.

---

## 1. What this is

Four things that all come from one fault: **the report engine does arithmetic nobody
checked against the rest of the product.**

- **One definition of a deal's value**, in one place, used by the pipeline card, the deal
  list, chat and the AI report — today there are four copies and two of them disagree
  (diagnosis §1d).
- **Five basic reports on fixed code paths.** The model still *reads the sentence* and
  *chooses* which of the five it is (model-first, `docs/MODEL_FIRST.md`), but it never
  produces the numbers and never produces a spec for them. Buttons and cards reach the
  same five functions directly.
- **The Phase-17 road keeps working for everything else**, with its two known defects
  fixed: equality-only filters (so "งานค้าง" counted only `open`) and the deal-value
  expression (so "ยอดมูลค่าดีลทั้งหมด" answered `0`).
- **The picture is designed by the model and computed by us.** The numbers are already in
  hand when the model is asked; all it returns is a *chart plan* — kind, labels, unit,
  which bar to highlight — which a validator checks before anything is drawn.

And, because the numbers have to mean something, the two record-keeping holes the reports
stand on: a deal never records *when* it closed, and a paid invoice never tells the deal it
was paid.

**The rule that governs all of it** (`docs/MODEL_FIRST.md`, CLAUDE.md §-1 rule 6, and the
round-20K "dumped arithmetic" lesson, `docs/SESSION_HANDOFF.md:545+`): the model reads and
proposes; **code validates, computes and acts**. Arithmetic is never the model's. A number
that has been through a model — or through a JSON round-trip that made it a string — and
is then added to something else fails silently; `scripts/dev/check-dumped-arithmetic.py`
exists because we paid for that twice.

---

## 2. Deal value — the one definition

### The decision

> **The typed `amount` wins. When `amount` is null, the value is the sum of the deal's line
> items. When there is neither, it is 0.**

```
deal_value(deal) = COALESCE(deals.amount, SUM(deal_products.qty * deal_products.quoted_unit_price), 0)
```

### Where the four copies are today

| where | expression today | after 21C |
|---|---|---|
| pipeline card (`data/chann_data/repositories/phase9.py:896-898`) | `coalesce(sum(qty*price), Deal.amount, 0)` — **lines first** | the shared expression |
| chat deal queries (`application/chann_app/services/chat.py::_deal_value`, ~18618) | lines if any, else `amount` | calls the Data tier's number |
| the four sales charts (`application/chann_app/services/sales_charts.py:126`, `deal_value`) | lines if any, else `amount` | calls the shared helper |
| dashboard deal list (`presentation/app/liff/sales/deals/DealList.tsx:39-45`) | `amount != null && amount !== "" && Number(amount) > 0 ? amount : Σ lines` | the `> 0` goes: **a typed zero is a typed value**, and only `null`/`""` falls through to the lines |
| AI report (`data/chann_data/repositories/phase17.py:104`) | `sum(Deal.amount)` — **the bug** | the shared expression |

### How it is shared, not copied

A single module-level SQL expression in the Data tier:

```python
# data/chann_data/repositories/deal_value.py
DEAL_VALUE = func.coalesce(
    Deal.amount, func.sum(DealProduct.quoted_unit_price * DealProduct.qty), 0,
)


def deal_value_subquery(*extra_columns):
    """One row per deal: `deal_id`, whatever else the caller asked for,
    and `value`. The caller adds its own WHERE and then aggregates."""
    return (
        select(Deal.id.label("deal_id"), *extra_columns, DEAL_VALUE.label("value"))
        .outerjoin(DealProduct, DealProduct.deal_id == Deal.id)
        .group_by(Deal.id, Deal.amount, *extra_columns)
    )
```

used by `pipeline_summary`, by the new `BasicReportRepository`, and by
`ReportQueryRepository` when the spec is `sum|avg|min|max` of `deals.amount`. Every user
joins `deal_products` with an **outer join grouped by `Deal.id`** and wraps that in a
subquery before aggregating, because the join multiplies rows: a `count` taken over the
joined rows counts line items, not deals (diagnosis §5 step 1's warning). `count` therefore
keeps its own un-joined statement.

The Application tier does not re-implement it. `sales_charts.deal_value(deal)` and
`chat._deal_value(deal)` become one Python helper,
`application/chann_app/services/deal_value.py::deal_value(deal: dict) -> Decimal`, with the
same precedence, used wherever a deal dict (not a row) is in hand.

### What changes for a shop

Pipeline totals move for any deal that has **both** a typed amount and line items: the
typed amount now wins where the sum of lines used to. That is the owner's decision, and it
is a visible change — it belongs in the release note and in the handoff, not in a
footnote. AI-report deal totals move from `0` to the real number; that is the fix itself.

### Pinned by a test, in both places at once

An integration test seeds three deals — lines only (30,000) · `amount` only (250,000) ·
both, where the typed 99,000 must beat the 1,000 of lines — and asserts
**`AI report total == pipeline_summary total == 379,000`**, plus that a `count` grouped by
stage still counts three deals and not four line items. The dashboard's own `dealValue` is
pinned separately, in a unit test of the shared Python helper it now matches. The diagnosis
§1f note is that nothing tested any of this at all.

---

## 3. Invoice edits, and the deal closing when the bill is paid

### 3.1 What an invoice is made of

`Invoice` (`data/chann_data/models.py:1235`) holds the money columns
(`subtotal, discount_amount, vat_rate, vat_amount, total, paid_amount`, all `Numeric(18,2)`,
`vat_rate` `Numeric(6,4)` where **NULL is not 0**) and a frozen `data_snapshot` JSONB with
the line items and the parties as they were when the bill was raised.

**There is no invoice line-item table, and `InvoiceRepository` has no `update` method at
all.** A quote's lines live in `quote_products` and are edited row by row; an invoice's
lines live inside one JSONB document. So "edit an invoice like a quote" means: rewrite the
snapshot's `line_items` and recompute every money column from them, in one call, with the
functions that already exist —

- `application/chann_app/services/documents/snapshot.py:76 build_line_items(lines) -> list[dict]`
  (`line_no, product_name, qty, unit_price, line_total, notes`),
- `…/snapshot.py:100 compute_totals(line_items, vat_rate, discount=None) -> dict`
  (`subtotal, discount_applicable, discount_amount, net_total, vat_applicable, vat_rate,
  vat_rate_percent, vat_amount, grand_total`; discount before VAT, clamped to subtotal),
- `…/snapshot.py:255 build_invoice_snapshot(*, lines, customer, company, invoice=None,
  quote=None, deal=None, discount=None, issued_at=None, due_date=None) -> dict`.

The recomputation happens in code, from `Decimal`, never from a model and never from a
number that has already been through JSON — the round-20K rule, and
`scripts/dev/check-dumped-arithmetic.py` will say so if it is broken.

### 3.2 The state table

Statuses are `INVOICE_STATUSES = ("draft", "issued", "partially_paid", "paid", "void")`
(`models.py:1231`).

| status | edit lines/details | issue | record payment | void | what closes the deal |
|---|---|---|---|---|---|
| `draft` | **yes** — free; totals recomputed | yes → `issued` | no (not open) | yes | — |
| `issued`, `paid_amount = 0` | **yes** — allowed, and the invoice is marked **needing re-issue** (§3.3) | re-issue (new PDF, same number) | yes → `partially_paid` or `paid` | yes | — |
| `partially_paid` (`paid_amount > 0`) | **no** — `InvoiceLocked`; the road is void + a fresh invoice, or a credit note (not in this round) | re-issue only | yes → `paid` when it settles | only with `invoice.void`, and the existing round-20V rule about money already received applies | — |
| `paid` | **no** | no | no (nothing outstanding) | per the 20V rule | **yes — this is the trigger** (§3.4) |
| `void` | no | no | no | no | — |

The one rule in words: **an invoice is editable until money has touched it.**
`paid_amount > 0` locks it. That is stricter than "editable until issued" and looser than
"editable while draft", and it is the assumption stated here for the owner to overturn:
*editable while no payment has been recorded; an edit after issue marks it as needing
re-issue; locked once `paid_amount > 0`.*

### 3.3 What the quote actually does, and where the invoice differs

**Read before implementing — and this was read, so the plan does not guess.** A quote is
**editable only while it is `draft`**:

```python
# data/chann_data/repositories/phase10.py:250
def _editable(self, scope: TenantScope, quote_id: uuid.UUID) -> Quote:
    row = self.get(scope, quote_id)
    if row is None:
        raise Phase10NotFound("quote not found in this tenant")
    if row.status != "draft":
        raise Phase10Conflict(
            f"quote {row.quote_id} is {row.status} and can no longer be edited"
        )
    return row
```

`_editable` gates `set_terms` (:142), `add_product` (:264), `update_product` (:295),
`remove_product` (:332). The 409 becomes a sentence in chat
(`chat.py:14703 LINE_QUOTE_LOCKED`, matched on `"can no longer be edited"` at :15201,
:15236, :17656, :17854) and a boolean on the dashboard
(`QuoteDetail.tsx:464 const editable = canUpdate && quoteStatus === "draft";`).
`_QUOTE_ALLOWED_TRANSITIONS` (phase10.py:48) has **no way back to `draft`**, and there is no
`sent_at` column anywhere.

So there is no "edit after sent → needs re-issue" rule to copy: a sent quote simply refuses.
What the owner is asking for — *"invoice ต้องแก้ไขรายการสินค้าหรือข้อมูลต่างๆได้เหมือน quote ด้วย"* —
is that an invoice have a line editor **at all**, which today it does not. We therefore
copy the quote's **shape** and loosen its **threshold**, and both halves are stated so the
owner can overturn either:

| | quote (today) | invoice (this round) |
|---|---|---|
| guard | `status == "draft"` | `paid_amount == 0` **and** `status != "void"` |
| one place | `_editable` in the repository | `_editable` in `InvoiceRepository`, same name, same shape |
| refusal | `Phase10Conflict` → 409 → one sentence | `InvoiceConflict` → 409 → one sentence |
| chat copy | `LINE_QUOTE_LOCKED` | `LINE_INVOICE_LOCKED`, same wording pattern |
| screen | `editable = canUpdate && status === "draft"` | `editable = canUpdate && paidAmount === 0 && status !== "void"` |

**The assumption, stated for the owner:** *editable while no payment has been recorded; an
edit after issue marks the invoice as needing re-issue; locked once `paid_amount > 0`.* It
is deliberately looser than the quote's rule because a quote that has been sent can be
superseded by a new quote at no cost, while an invoice carries a number the shop's books
depend on — re-raising one is not free.

**"Needs re-issue" is new, and invoice-only**, because a quote can never reach the state it
describes. It is stamped, not derived: an edit sets `data_snapshot["needs_reissue_at"]` when
the invoice already has a `generated_document_id`, and `InvoiceOut.needs_reissue` is that key
turned into a boolean, so the dashboard and chat read one flag. It lives **inside the
snapshot** — the invoice's one document of record, which an edit rewrites anyway — rather
than in a column of its own, which would mean a second migration in a round that has one.
Re-issuing renders a new PDF under the **same** number (`INV-YYYY-NNNN` is allocated once,
round 20V, `InvoiceRepository._unique_invoice_id`:152) through the existing
`issue_invoice_document(..., allow_reissue=True)` and records it with the existing
`link_document` audit verb; `issue` and `link_document` both clear the flag.

"ข้อมูลต่างๆ" (the details, not just the lines) means `note`, `due_date`, and the discount —
the same three a quote's `set_terms` covers. Customer and deal links are **not** editable:
changing who a numbered bill is addressed to is a void-and-reissue, not an edit.

### 3.4 The deal closes when the bill is settled

**Trigger:** the payment that takes `paid_amount` to `total` — i.e. the moment the invoice
row's status becomes `paid`. Not issuing. Not a partial payment. Owner, 23 ก.ย. 2569:
*"ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอนชำระเงินแล้ว"*. It is the same moment the receipt becomes
possible (`issue_receipt_document` already requires full payment), so the shop sees one
event, not two.

**Where:** inside the Data tier, in the same transaction as the payment —
`data/chann_data/repositories/invoices.py:352 add_payment(...) -> tuple[Invoice, InvoicePayment]`,
whose line 392 is already the exact moment the decision is made:

```python
row.status = "paid" if row.paid_amount >= row.total else "partially_paid"
```

— not in the Application tier afterwards. A write-back that is a second HTTP call can
half-happen; this one cannot. (`add_payment` recomputes `paid_amount` from
`SUM(invoice_payments.amount)` rather than incrementing it, :387, so "did this payment
settle it?" is answered from the ledger, not from an accumulator.)

**What is written onto the deal, when `invoice.deal_id` is set:**

1. the deal's line items are **replaced** by the invoice snapshot's lines
   (`product_name`, `qty`, `quoted_unit_price`, `product_id` where the line carries one,
   `position` in the invoice's own order);
2. `deals.amount` = the invoice `total`;
3. `stage` → `won`;
4. `closed_at` = the payment's `paid_at` (§4).

**Idempotency and the refusals — all four stated, because each has bitten something:**

- **A second settle never re-writes.** The write-back is guarded by the invoice's *first*
  transition into `paid`; a paid invoice refuses further payments, so the transition
  happens once per invoice. Additionally, when the deal is **already `won` with a
  `closed_at`** (a second invoice on the same deal), only nothing is written — no lines, no
  amount, no stage — and the fact is logged. Overwriting a deal's contents from the second
  bill of two is how you lose the first one.
- **A `lost` deal is not resurrected**, and an archived deal is not touched. The payment
  still succeeds; the deal is left as it is and the reply says the deal was not changed.
- **No invoice without `deal_id`** does anything to any deal. (An invoice may be raised
  from a quote, a deal, or a customer with a deal chosen — round 20X; `deal_id` is the only
  link this uses.)
- **The stage machine is not widened.** `_ALLOWED_TRANSITIONS` (`phase9.py:43-48`) has no
  `new → won`, on purpose: a person may not click a deal from "new" straight to "won". The
  settle path is not a person clicking — the money is the proof — so it uses its own
  method, `DealRepository.close_won_from_invoice(...)`, which sets the stage and
  `closed_at` directly and documents why. The human transition table is untouched.

**Audit** — `audit_log.action` has a CHECK constraint and the verb list is
`data/chann_data/audit_actions.py::AUDIT_ACTIONS`:
`create, update, delete, assign, transfer, cross_tenant_lookup, link_document, upsert,
status, remove_product, claim, reject, check_in, check_out, pdpa_erasure, pdpa_export`.
The settle writes **`status`** on the deal (stage → won) and **`update`** on the deal
(lines + amount). **No new verb.** Inventing one fails the write, and because the audit row
shares the transaction with the change, it takes the payment down with it — this is the
exact failure recorded for quote issuing in that module's docstring.

**What the person is told.** The payment reply in chat and the invoice sheet both say, in
one sentence, that the deal was closed and what it now holds:
`"ชำระครบแล้ว · ปิดดีล D-2026-0007 เป็นปิดสำเร็จ · มูลค่าดีล 32,100 บาท ตามใบแจ้งหนี้"`. A write-back
nobody is told about is a number that changes by itself.

---

## 4. `deals.closed_at`

**Migration `0038_deal_closed_at`**, `down_revision = "0037_api_keys"`;
`EXPECTED_MIGRATION_HEAD` in `data/chann_data/main.py` becomes `"0038_deal_closed_at"`.

```
ALTER TABLE deals ADD COLUMN closed_at timestamptz NULL;
CREATE INDEX ix_deals_closed_at ON deals (license_id, closed_at);
UPDATE deals SET closed_at = updated_at WHERE stage IN ('won','lost') AND closed_at IS NULL;
```

- **Set** in exactly two places, both in `DealRepository`: `transition_stage` (when
  `to_stage` is `won` or `lost`) and `close_won_from_invoice` (§3.4). Value: `now(UTC)`,
  or the payment's `paid_at` on the settle path.
- **Cleared** on reopen — `transition_stage` to `new` sets `closed_at = None`, beside the
  existing `lost_reason` clearing, and for the same reason: a deal sitting in "new" that
  still says when it closed is a lie the reports would believe.
- **Backfill** from `updated_at`, which is the closest honest approximation for rows closed
  before the column existed. The backfill is **stated in the reply of report #2**: "ดีลที่
  ปิดก่อน 23 ก.ย. 2569 ใช้วันที่แก้ไขล่าสุดเป็นวันปิด" — the round-20L lesson is that a number
  whose provenance is not said is a number nobody can check.
- **Used by** report #2 ("won this month vs last month") and by the Phase-17 whitelist,
  which gains `closed_at` as a filterable **date field** of `deals` (not a filter value) in
  both `phase17.ENTITIES["deals"]["date_fields"]` and
  `reports_ai.ALLOWED_ENTITIES["deals"]`, so "ยอดปิดสำเร็จเดือนนี้" can be asked ad-hoc too.
  `sales_charts.deal_closed_on` stops guessing with `expected_close_date ?? created_at` and
  uses `closed_at` when it is set.

---

## 5. The five basic reports

**What makes them "basic": code computes them, start to finish.** No spec, no whitelist
walk, no model call in the number path. The model's only job is to read a sentence and say
*which of the five* it is — a `report_key` from a five-value enum — which is model-first
(the model reads, code acts) without handing arithmetic to anyone.

Entry points, all three reaching the same function:

- **chat**, a typed sentence the model classifies (and the existing `_is_ai_report_request`
  road hands over before Phase 17 is reached);
- **chat**, a quick-reply button (`postback` — the direct path is allowed, the action and
  the record are already named);
- **dashboard**, a card on `/liff/sales/reports/ai`.

| # | key | คำถาม (th) | question (en) | source | definition |
|---|---|---|---|---|---|
| 1 | `pipeline_value` | "มูลค่าดีลทั้งหมด", "ยอดในท่อตอนนี้", "ดีลทั้งหมดมีมูลค่าเท่าไหร่" | "pipeline value", "total deal value" | `DealRepository.pipeline_summary` | every non-archived deal · value per §2 · broken down by stage (new/proposed/won/lost) + open value + closing this month |
| 2 | `won_this_month` | "ยอดปิดสำเร็จเดือนนี้", "เดือนนี้ปิดได้เท่าไหร่ เทียบเดือนที่แล้ว" | "won this month vs last month" | `deals` where `stage='won'` | two windows by **`closed_at`** (Bangkok months, via `phase17.date_window("this_month"/"last_month")`) · value per §2 · count + value + the difference and its percentage |
| 3 | `open_jobs_by_tech` | "งานซ่อมค้างแยกตามช่าง", "ช่างแต่ละคนมีงานค้างกี่งาน" | "open repair jobs by technician" | `ServiceTicket` grouped by `assigned_to_ref` | `status IN ('open','assigned','in_progress')` · no date window · unassigned rows are their own row ("ยังไม่มอบหมาย"), never dropped |
| 4 | `outstanding_invoices` | "ยอดค้างชำระ", "ลูกค้าค้างจ่ายเท่าไหร่" | "outstanding invoices" | `Invoice` | `status IN ('issued','partially_paid')` · `outstanding = total − paid_amount` · split **เลยกำหนด** (`due_date < today`, Bangkok) vs ยังไม่ถึงกำหนด · count + amount for each |
| 5 | `satisfaction_avg` | "คะแนนความพึงพอใจเฉลี่ย", "ลูกค้าให้คะแนนเท่าไหร่" | "average satisfaction" | `SatisfactionSurvey` | `avg(score)` over rows with `submitted_at IS NOT NULL` and a score · this month and last month, the same two Bangkok windows report #2 uses · the count of answers travels with the average, so a mean of two is not read as a verdict |

Every one of them is `license_id`-scoped and skips `archived_at IS NOT NULL` where the
model has that column — the same two rules `ReportQueryRepository.build_statement` already
applies (`phase17.py:225-231`).

### Response shape — one payload, two renderings

The Data tier returns, for every key, the same envelope:

```json
{"key": "outstanding_invoices",
 "title_th": "ยอดค้างชำระ", "title_en": "Outstanding invoices",
 "unit": "money",                       // money | count | score
 "headline": {"label_th": "ค้างชำระรวม", "label_en": "Outstanding", "value": 128400.0},
 "rows": [{"key": "overdue", "label_th": "เลยกำหนด", "label_en": "Overdue", "value": 41000.0, "count": 3},
          {"key": "not_due", "label_th": "ยังไม่ถึงกำหนด", "label_en": "Not yet due", "value": 87400.0, "count": 9}],
 "notes_th": [], "notes_en": [],
 "generated_at": "2026-09-23T04:00:00+00:00"}
```

- **chat** turns it into at most 8 lines of text (the 15-line ceiling
  `simulate-phrasings --real` measures is the hard limit) plus quick replies: the other
  four reports, and **"ดูเป็นรูป"** which is the metered road (§7).
- **the dashboard card** renders `headline` big and `rows` as labelled bars — the same
  markup `AiReports.tsx` already uses for a report's rows — with **"ดูเป็นรูป"** and
  **"ถามแบบอื่น"** (which drops into the existing question box).
- Both render from the **same envelope**, so they cannot disagree. Neither re-computes
  anything: no percentage, no total, no difference is calculated in TypeScript or in the
  reply builder. (Round 20K: a number converted for display and then used in arithmetic is
  how this breaks silently.)

### Credit rule

**None of the five ever spends a credit.** They make no model call for their numbers, and
they never call `chart_quota.spend_one`. Where the model is asked "which of the five?", the
call is the router's existing intent read — the one every chat sentence already pays for —
not a report call.

What the quota actually is today, since the plan must not guess: there is exactly one
counter, `license_settings.ai_chart_quota` + its month counter, spent by
`application/chann_app/services/chart_quota.py::spend_one` → `DataClient.consume_ai_chart_quota`,
called from exactly two places — `chat.py::_handle_ai_report` (~29878) and
`routers_phase2.py::_charge_for_the_picture` (~5117) — and **only when a picture actually
exists**. Nothing named `reports_ai` counts anything. So:

| road | model call | credit |
|---|---|---|
| one of the five, in words (chat or card) | none (beyond the router's read) | **free** |
| one of the five, "ดูเป็นรูป" | yes — the chart plan | **1** |
| ad-hoc question (Phase 17), in words | yes — the query spec | **1** |
| ad-hoc question, with a picture | yes — spec + chart plan | **1** (the request, not the calls) |
| the four fixed sales charts (`sales_charts.py`) | none | **free** |

The two existing charge points are **not edited** — `chat.py:29878` and
`routers_phase2.py:5137` keep the literal line `quota = await chart_quota.spend_one(`,
because `tests/unit/test_round20l_a_picture_is_a_picture.py` greps the source for exactly
that string. The ad-hoc question is charged by a **new** sibling,
`_charge_for_the_question(client, license_id, out)`, called once per answered ad-hoc
request (never for a `clarify`, never for a refused spec), spending the same counter.

The ad-hoc text road being charged is a **change** from the 17–18 ก.ย. rule ("running out
of chart quota must never stop a shop finding out its own numbers"), and it is only
defensible because the five free reports now exist and answer what that rule was protecting.
The counter keeps its column name `ai_chart_quota` (renaming a column for a wording change
is not worth a migration) but every string a person sees says **"เครดิตรายงาน AI"**. Over
the allowance, the ad-hoc road replies with the number, the date it resets, and buttons to
the five free reports — never a blank refusal.

---

## 6. Phase-17 fixes (the ad-hoc road keeps its job)

1. **The deal-value expression** — `phase17.NUMERIC_FIELDS["deals"]["amount"]` stops being
   `Deal.amount` and becomes the §2 expression, with the outer join, the
   `group_by(Deal.id)` and the subquery wrapper. `count` keeps its own path. The label the
   report prints for it stays "มูลค่า" (`reports_ai.FIELD_LABEL`), because that is what it
   has always claimed to be.
2. **Multi-value filters.** `filter` values become `str | list[str]`:
   - `phase17.validate_spec` accepts a list of 1–10 values, validates **each** against the
     same enum/uuid/`_VALUE_RE` rules, de-duplicates, and keeps order;
   - `build_statement` emits `col.in_(values)` for a list and `col == value` for a scalar
     (the scalar path is kept, not rewritten as a one-element `IN`, so every existing
     statement and its test are unchanged);
   - `data/chann_data/schemas.py::ReportQueryIn.filter` becomes
     `dict[str, str | list[str]]`;
   - `reports_ai.validate_query_spec` mirrors it exactly (it is a second copy of the same
     whitelist on the Application side — the two are kept in step by a test that runs every
     option `spec_options` offers through both validators);
   - `reports_ai.build_system_prompt` / the whitelist text tells the model it may send a
     list, with the two examples the prompt already promises and cannot deliver:
     `{"status": ["issued","partially_paid"]}` and
     `{"status": ["open","assigned","in_progress"]}`;
   - `_filter_words` (`reports_ai.py:138`) renders a list as `"ก · ข · ค"` so the report
     header says which values were counted.
3. **`closed_at` joins the whitelist** as a date field of `deals` (§4).
4. **The pinning test**: "AI report total == pipeline card total" (§2), plus "งานค้าง
   returns open+assigned+in_progress" and "ยอดค้างชำระ returns issued+partially_paid" —
   the two sentences the prompt has been promising since round 20V and silently
   undercounting.

Not changed: the whitelist's shape, the tenant filter, the `archived_at` rule, the model's
inability to see SQL or data. `phase17.py`'s docstring stays true.

---

## 7. AI-designed pictures

### 7.1 The rule

**The server computes every number. The model designs the picture and never touches the
arithmetic.** Rejected outright: an image-generation model drawing the chart. It does not
compute — it draws something chart-shaped, with bars out of proportion and labels that are
wrong in ways no test can see. That breaks CLAUDE.md §-1 rule 6 and the 20K lesson at once.

### 7.2 The chart plan

The model receives: the question as typed, and the **already-computed** result (labels and
numbers, ≤ 14 rows, as text). It returns JSON only:

```json
{"kind": "bar",
 "title": "ยอดค้างชำระแยกตามสถานะ",
 "subtitle": "23 ก.ย. 2569",
 "unit": "money",
 "series_label": "บาท",
 "highlight": "เลยกำหนด",
 "labels": ["เลยกำหนด", "ยังไม่ถึงกำหนด"],
 "note": "เลยกำหนดคิดเป็น 32% ของยอดค้าง"}
```

**`values` is not a field of the plan.** The model cannot send numbers, so it cannot send
wrong ones. Values come from the result, matched to `labels` by position after the
validator has checked that `labels` is a permutation of the result's own labels.

### 7.3 The validator — `application/chann_app/services/chart_plan.py`

`validate_chart_plan(plan: dict, result: dict, language: str) -> ChartPlan` refuses, with a
reason, anything that is not:

| rule | refusal |
|---|---|
| `kind ∈ {"bar","hbar","line","value","donut"}` — the four `charts.py` already draws, plus `donut`, which only the HTML road can render and which falls back to `bar` | `kind '<x>' is not allowed` |
| `labels` is exactly the result's labels, reordered — no additions, no renames | `labels do not match the result` |
| `len(labels) ≤ 14`, and ≤ 8 for `bar`, ≤ 10 for `hbar` (the caps `charts.py` already enforces) | `too many labels for <kind>` |
| `unit ∈ {"money","count","score","percent"}` and matches the metric (`count` may not be drawn as money) | `unit does not match the report` |
| `title`/`subtitle`/`note`/`series_label` ≤ 80 chars each, no URL, no HTML | `text too long` / `text may not contain markup` |
| `highlight`, when present, is one of `labels` | `highlight is not one of the labels` |
| `value` kind only when the result is a single number; any grouped kind needs ≥ 2 rows | `nothing to plot` |
| no key outside the schema | `unknown field '<x>'` |

A refusal is never fatal: the picture falls back (§7.5). `ChartPlan` is a frozen dataclass;
the renderer takes `ChartPlan` + `result`, never the raw model output.

### 7.4 The renderer, and the probe that decides it

`render_chart_html(plan: ChartPlan, *, values: list[float], company_name: str = "",
oa: str = "sales") -> str` produces **one self-contained HTML page in the OA theme** — the
values come from the result, positionally, after the validator has confirmed `plan.labels`
is a permutation of the result's own labels (Sale `#178a50` / Tech `#1f6fd6`
/ CS `#e8731a`, CLAUDE.md §-1 rule 5; the same `IBM Plex Sans Thai` stack
`reports_ai.report_html` already uses), which goes to
`get_renderer("smartbrowz").preview_image(html, PdfOptions())` →
`smart_browz.take_screenshot(html)` (`application/chann_app/services/pdf/smartbrowz.py:238`,
declared at `pdf/base.py:43`, **no production caller today**) → PNG →
`reports_ai.publish_chart(png, license_id=…)` → the existing signed asset link, the
existing LINE image + link, the existing quota. Nothing downstream of `publish_chart`
changes.

**One thing to fix on the way in:** `application/chann_app/services/pdf/__init__.py` is
**0 bytes**, so `reports_ai.py:586`'s `from .pdf import PdfOptions, get_renderer` raises
`ImportError`, is swallowed by the `except Exception` at :592, and the AI report's PDF has
silently never been produced. Every other caller imports `from .services.pdf.base import …`.
The chart renderer imports `from .pdf.base import PdfOptions, get_renderer` and the one-line
PDF bug is fixed in the same task, with a test that asserts the import works.

**Task zero is a probe, not a guess.** `take_screenshot(html)` takes no options — no
viewport, no wait-for-selector, no delay — so whether it waits for JavaScript to paint is
unknown and cannot be reasoned out from the SDK signature. The probe renders one page that
is **blank until JS runs** (a canvas filled by a script) and one that is **inline SVG with
no JS at all**, screenshots both against DEV, saves the PNGs, and **someone looks at them**.

- Canvas page paints → either renderer is available; we still choose inline SVG, and record
  that the option existed.
- Canvas page blank → **inline SVG, no JavaScript**, is the only renderer. This is the
  expected outcome and the one the design already assumes.

Either way the answer is written into the plan's task and into the handoff, with the PNG
paths. No CDN, no external font fetch, no network at render time: everything inline, which
is also why a screenshot service cannot leak the shop's numbers to a third party beyond
Zoho, which already renders every quote and invoice.

### 7.5 Fallback and cost

`charts.py` (Pillow, 532 lines, four kinds) **stays** and becomes the fallback, in this
order: chart plan invalid → deterministic plan built by code from the result (kind by
`reports_ai.CHART_KIND`, labels from the result) → SmartBrowz unavailable, times out, or
returns empty bytes → `charts.render_or_none(...)` as today → still nothing → the report
answers in words and says the picture could not be made (the sentence already exists,
`reports_ai.CHART_UNAVAILABLE`). **A picture is never worth losing the answer over.**

The credit is spent as it is today — after a picture exists, once per request (§5).
Latency: a SmartBrowz render is seconds; the reply says "กำลังวาดรูป…" on the dashboard and
the chat reply arrives with the picture as it does now.

### 7.6 What the model is told

A prompt of its own (not `INTENT_SYSTEM_PROMPT`, not the report-spec prompt): the question,
the rows, the allowed kinds, the caps, the unit, and the sentence **"ห้ามส่งตัวเลข"**.
Before it ships, the four sentences in `docs/MODEL_FIRST.md`'s order are measured with
`scripts/dev/ask-model.py` against the deployed DEV model and the answers pasted into the
handoff — including at least one where the model is expected to return a `kind` we refuse,
so the refusal path is proven against the real model rather than a fixture.

---

## 8. Surfaces (parity is a rule, not an aspiration)

`scripts/dev/check-parity.py` must still read *"every capability is reachable from both
surfaces"*. Anything deliberately one-sided goes in its `ACCEPTED` list **with a reason** (round 20K:
an entry that sits there without one becomes wallpaper). Note the key order: `ACCEPTED` is
`{(entity, action): "reason"}` (`scripts/dev/check-parity.py:30`) while `ACTION_PERMISSIONS`
is `{(action, entity): permission_key}` (`chat.py:103`) — the checker flips them at :297.

### Dashboard

- **`/liff/sales/reports/ai`** gains, above the question box, a row of **five cards** —
  one per basic report, Thai title + one-line "what this counts" + the number once it
  loads. A card is one tap to the answer; the question box below is unchanged. Designed
  with **ui-ux-pro-max** (owner's standing rule, 20 ก.ย.: every dashboard change, buttons
  and placement included — round 20P put import buttons in without it and they came back).
  `primary-action` (one CTA), the cards' numbers right-aligned and tabular, the "ดูเป็นรูป"
  button secondary and per card.
- **Invoice sheet** — the detail panel is **inline inside**
  `presentation/app/liff/sales/invoices/InvoiceList.tsx` (`<Sheet>` at :563–:789), not a
  separate component; the folder holds only `page.tsx`, `InvoiceList.tsx` and
  `_create-sheet.tsx`. It gains line-item editing that reuses the quote's own component,
  `presentation/app/liff/_product-line-form.tsx::ProductLineForm` (which
  `quotes/[id]/QuoteDetail.tsx` uses at :12/:732/:762), with the totals **recomputed by the
  server and re-read**, never in the browser. When the
  invoice is locked (`paid_amount > 0`) the editor is not rendered at all and the reason is
  shown in its place; when an edit happened after issue, the "needs re-issue" state and its
  button appear per §3.3.
- Strings in `presentation/lib/i18n/{th,en}.ts` under `dashboard.aiReports.basic.*` and
  `dashboard.invoices.*`; `check-i18n-usage.py` must stay clean.

### Chat

Model-first, measured with `scripts/dev/ask-model.py` **before and after**, per sentence,
never on totals (`docs/MODEL_FIRST.md`):

- the five report sentences (th + en, ≥ 3 phrasings each) → the right `report_key`;
- **"ดูเป็นรูป"** as a quick reply on every basic report, and as a sentence;
- invoice line editing: "แก้รายการในใบแจ้งหนี้ INV-2026-0003", "เปลี่ยนจำนวนแอร์เป็น 3 ตัว"
  → the invoice editor road, with the existing ambiguity buttons when a code is missing;
- the refusals said in words: locked invoice, no permission, nothing to plot, quota spent.

`ACTION_PERMISSIONS` (`chat.py:103`) needs **no new entry**: `("read","report"): "deal.read"`
covers the five reports, and `("update","invoice"): "invoice.update"` already carries the
line editor — as do `("issue","invoice")`, `("pay","invoice")`, `("receipt","invoice")`,
`("void","invoice"): "invoice.void"`. `check-parity` already has `("report","create")` and
`("report","read")` in `ACCEPTED`; the reason strings there are rewritten to name the five,
because a reason that no longer describes the code is the wallpaper the 20K lesson warns
about.

### Guide

`application/chann_app/services/guides.py`: the existing AI-report step is rewritten to
name the five reports and the picture button; `python3 scripts/dev/render-guides.py` after.
One image slot, `sales-ai-report`, is **redrawn** in
`scripts/dev/render-guide-images.py` to show the five cards — never edited as a PNG by hand
— and **looked at** (the round-20L rule: no test can see a clipped label).

---

## 8.5 Sending a document to the customer on LINE

**The owner, 23 ก.ย. 2569:** *"ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้วสำหรับใบเสนอราคาหรือ invoice
ต่างๆสามารถออกคำสั่งหรือกดปุ่มส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มีผูกก็แจ้งว่าไม่ได้หรือทำปุ่มเป็นไม่พร้อม
ใช้งาน"*

### What already exists, and is reused exactly

A receipt is already pushed to the customer:
`application/chann_app/services/invoices.py:537 notify_customer_receipt(client, *, license_id,
invoice, document) -> bool`. It reads `customer["customer_chann_uid"]`, builds the link with
`chat.document_download_url(license_id, document_id)`, resolves the LINE identity with
`client.line_target_of(uid)` and sends through
`services/notify.py:75 send_notification(..., type=…, entity_type=…, entity_id=…,
delivery_dashboard=False, oa="customer")`, which **records the notification row and then
pushes**, never raises, and returns the stored row.

This round generalises that one function into
`application/chann_app/services/document_send.py::send_document_to_customer(client, *,
license_id, kind, record, document_id, language, actor_id) -> dict`, with
`kind ∈ {"quote", "invoice", "receipt"}`. Same push, same signed link, same notification
row, same "never raises". `notify_customer_receipt` becomes a thin call into it, so there is
one road, not two.

### The rules

| situation | what happens |
|---|---|
| document issued **and** the customer has `customer_chann_uid` | pushed; the reply names the customer and says the link is good for 7 days |
| the customer has **no** `customer_chann_uid` | **nothing is sent.** Chat: *"ลูกค้า <ชื่อ> ยังไม่ได้ผูกไลน์กับร้าน จึงส่งให้ทางไลน์ไม่ได้ — ให้ลูกค้าเพิ่มเพื่อน OA ลูกค้าแล้วลงทะเบียน หรือกดคัดลอกลิงก์ส่งเอง"* + a button that copies/opens the link. Dashboard: the button renders **disabled** with that sentence as its helper text and `title` |
| the document has not been issued (no `generated_document_id`) | 422 `not_issued`; chat offers "ออกเอกสารก่อน" |
| sent before | allowed — a re-send is a legitimate thing to do. The reply says **"ส่งซ้ำ"** and names when it was last sent. Idempotency is **per document version**: the notification row carries `entity_id` = the record and the document id, so "has this version been sent?" is a query, not a flag |
| the shop is suspended | the same refusal the rest of the write surface gives (`refuse_if_suspended`) |

### Permission and parity

`ACTION_PERMISSIONS` (`chat.py:103`) gains exactly two entries:
`("send","quote"): "quote.update"` and `("send","invoice"): "invoice.update"` — the keys that
already govern issuing and re-issuing those documents. **Verify against the catalog before
writing them**: `grep -n "quote.update\|invoice.update" data/chann_data/permissions.py`.
`check-parity.py` reads `("quote","send")`/`("invoice","send")` from both surfaces, so both
must ship together in this round — the dashboard buttons are not a follow-up.

A receipt has no entity of its own; it is sent from the invoice it belongs to, so it needs
no third entry.

### Surfaces

- **Chat** (model-first): "ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า", "ส่งใบแจ้งหนี้ INV-2026-0003
  ให้ลูกค้าทางไลน์", "ส่งใบเสร็จให้ลูกค้า". The model proposes `{"action": "send", "entity":
  "quote"|"invoice", "fields": {"code": "…"}}` — measured with `ask-model.py` **before**
  the prompt is touched, exactly as `docs/MODEL_FIRST.md` orders it — and the handler
  resolves the code, checks the key, checks the link, and sends. No code in the sentence →
  the usual "which one?" with buttons, never a guess.
- **Dashboard**: a **secondary** action, not the page's primary CTA (ui-ux-pro-max
  `primary-action`: one CTA per screen, and issuing the document is it) —
  `presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx` next to the document buttons,
  and the invoice detail sheet in `presentation/app/liff/sales/invoices/InvoiceList.tsx`
  next to "เปิดเอกสาร". A disabled button always says **why** in visible helper text, not
  only in `title` (a tooltip does not exist on a phone) — ui-ux-pro-max
  `disabled-needs-a-reason`.
- Strings under `dashboard.documents.send*` in `presentation/lib/i18n/{th,en}.ts`.

### The record of it

The push is a **notification row** — the same row `notify_customer_receipt` has written since
round 20V, carrying `type="document_sent"`, the record's `entity_type`/`entity_id` and the
document id inside the message. That row is the platform's own count, it is queryable, and it
is what answers "has this version already gone out?" (the ส่งซ้ำ rule above).

No `audit_log` row is written, deliberately: nothing about the record itself changed, and
adding one would mean choosing a verb from a CHECK-constrained list that has none for this —
a constraint whose failure mode is that the audit row takes the change down with it.

---

## 9. Not in this round (recorded so nobody thinks they were forgotten)

- Credit notes / partial refunds; the road for a locked invoice stays void + re-raise.
- A sixth basic report, and per-shop custom fixed reports.
- Scheduled/emailed reports, and report subscriptions.
- The model writing a whole HTML page freehand (diagnosis §4's "open the door" idea) — the
  chart plan is deliberately narrow this round.
- Renaming the `ai_chart_quota` column, per-package allowances, and a credits screen.
- `closed_at` for quotes, tickets or invoices; only deals get it.
- Back-filling a *true* close date for deals closed before 0038 — `updated_at` is the
  approximation, and it is said out loud.

---

## 10. Testing

- **unit** — `deal_value` precedence (amount-only / lines-only / both / neither, and
  `amount = 0` which is a typed zero, not a missing value) · `validate_spec` with lists
  (good, empty, 11 values, a bad value inside a good list, a list for a date field) ·
  `validate_chart_plan` for every refusal in §7.3 · the five reports' envelope → chat text
  and → card props, from a fake Data client · the chat road for the five sentences and the
  invoice-edit sentences through the fake model · the settle write-back's four refusals ·
  the locked-invoice refusal.
- **boundary** — `chart_plan.py` imports nothing from `chann_data`; the basic-report
  envelope crosses the tier as it is declared (`check-fields.py`), which is how
  `Warranty.purchase_date` was caught.
- **integration (Postgres)** — the pinning test of §2 (three deals, three roads, one
  number) · `closed_at` set on won/lost, cleared on reopen, backfilled by 0038 · report #2
  counting by `closed_at` across a month boundary in Bangkok time · report #3 with three
  statuses and an unassigned ticket · report #4 with an overdue and a not-yet-due invoice ·
  the settle: payment → deal won, lines replaced, amount = total, `closed_at` = `paid_at`,
  audit rows `status` + `update`, and the second invoice on the same deal changing nothing ·
  tenant isolation on every one of the five.
- **agent-test** — `scripts/agent-test/scenarios/round21c-reports.yaml` (the five in chat,
  the picture button, over-quota) and `round21c-invoice-edit.yaml` (edit a draft, edit after
  issue → needs re-issue, pay in full → deal closed, try to edit → refused, send the invoice
  to a customer who has LINE, then to one who has not). **Shipped
  scenarios are the contract**: read the existing ones and the tester logs before changing
  a behaviour they pin.
- **the real model** — `scripts/dev/ask-model.py` for every new sentence, before and after;
  `scripts/dev/simulate-phrasings.py --real` before deploy because chat changed (CLAUDE.md:
  `--real` is not optional); `scripts/agent-test/run.py --real`;
  `~/stage-fix/tools/converse.py` for a real conversation per area.
- **the pictures** — every kind (`bar`, `hbar`, `line`, `value`, `donut`) × every edge case
  (a label of 40 characters · a 7-digit number · 8+ rows · a zero · a negative · one row ·
  a Thai label that Sarabun renders narrow) rendered through **both** the SmartBrowz road
  and the Pillow fallback, saved, and **looked at**. No test sees a clipped label.
- **the gate** — `~/stage-fix/tools/gate.sh` **pointed at this worktree**, not at
  `~/stage-fix/registry` where it defaults (it would test the previous round otherwise) ·
  `tests/integration` on `127.0.0.1:5435` · every `scripts/dev/check-*.py` clean ·
  `npm run typecheck && npm run build`.

## 11. Delivery

Round **21C**, on top of round 21B (`1c29bda`, which carries migration `0037_api_keys` and
the ext API) — 21C ships **after** 21B is deployed, one patch, one commit.

- **Migration `0038_deal_closed_at`** → the database image and
  `chann-crm-ai-dev-migrate` run **before** the data tier (the data image checks
  `EXPECTED_MIGRATION_HEAD` at boot).
- **Three tiers rebuilt** — Data (repositories, schemas, routes), Application (services,
  routers, chat, guides), Presentation (cards, invoice editor, i18n).
- Deploy script `~/round21c-deploy.sh`, copied from `~/round20z-deploy.sh` (the last round
  with a migration), with this round's symbol greps and commit message.
- Because chat changed: `scripts/agent-test/run.py --real` and
  `scripts/dev/simulate-phrasings.py --real` before deploy, and their output read, not just
  their exit code.
- Because pictures changed: every chart kind rendered and **looked at**.
- Handoff entry `รอบ 21C` above `รอบ 21B`; tester checklist section **VI (21C-1…)**;
  the release note says plainly that **pipeline numbers move** for deals that have both a
  typed amount and line items, and that **AI-report deal totals stop being 0**.
