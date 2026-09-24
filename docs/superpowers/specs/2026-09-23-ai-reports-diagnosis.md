# AI reports — ทำไม "ยอดมูลค่าดีลทั้งหมด" ขึ้น 0 และจะทำต่อยังไง

Repo: `/home/thanawinmax2/chann-crm-ai` @ `c0a71a3` · read-only investigation, 23 ก.ย. 2569

---

## 1 Root cause (with evidence)

**One sentence:** the Phase-17 report engine sums `deals.amount` only —
`data/chann_data/repositories/phase17.py:104` — while everywhere else in the product a
deal's value is *line items first, `amount` only as a fallback*, so every deal whose value
lives in `deal_products` contributes **NULL → 0**.

### The chain, proved step by step

**a. The model reads the question correctly.** Real DEV model
(`google/gemini-3.1-flash-lite`), through `reports_ai.generate_query_spec`:

```
ขอดูยอดมูลค่าดีลทั้งหมด -> {"entity":"deals","metric":"sum","field":"amount","filter":{},
                            "group_by":null,"date_range":null,"date_field":"created_at"}
มูลค่าดีลรวมเท่าไหร่     -> same
ยอดดีลทั้งหมด            -> same
```

All four phrasings pass `validate_query_spec` unchanged. **The model is not the bug** —
`reports_ai.py:279` even teaches it this exact mapping ("ยอดขายรวม/มูลค่ารวม = metric sum of
field amount on deals").

**b. The SQL that spec becomes** (`ReportQueryRepository.build_statement`, printed live):

```sql
SELECT sum(deals.amount) AS value
FROM deals
WHERE deals.license_id = :license_id_1 AND deals.archived_at IS NULL
```

No join to `deal_products`.

**c. `deals.amount` is nullable and is *not* the deal's value.** `data/chann_data/models.py:1003`:

```python
# User review (4 Sep 2026): a deal's own value. The pipeline still
# sums line items where they exist; this is what the salesperson said
# the deal is worth before any line item exists.
amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
```

`DealRepository.create` (`phase9.py:614-628`) writes `amount=amount` and then adds the
products separately — it never derives one from the other. A deal opened from a quote or
with line items therefore has `amount IS NULL`.

**d. Three other implementations of "a deal's value" all get it right** — this one is the
outlier:

| where | expression | file:line |
|---|---|---|
| pipeline card / สรุปการขาย | `coalesce(sum(qty*price), Deal.amount, 0)` | `data/chann_data/repositories/phase9.py:878-882` |
| chat deal queries | line items if any, else `amount` | `application/chann_app/services/chat.py:18618-18635` (`_deal_value`) |
| the 4 sales charts | same | `application/chann_app/services/sales_charts.py:126` (`deal_value`) |
| dashboard deal list | `amount>0 ? amount : Σ lines` | `presentation/app/liff/sales/deals/DealList.tsx:39-45` |
| **AI report (Phase 17)** | **`sum(Deal.amount)`** | **`data/chann_data/repositories/phase17.py:104`** |

`_num(None)` → `0` at `phase17.py:277-279`, so the reply is literally `รวม 0`.

**e. "รูปแบบใหม่" = the AI-report road.** Same question, two different roads, two different
answers:

```
_is_ai_report_request("ขอดูยอดมูลค่าดีลทั้งหมด") -> False    # AI_REPORT_TRIGGERS has "ดูยอด",
_is_ai_report_request("ดูยอดมูลค่าดีลทั้งหมด")   -> True     # matched with startswith(), so "ขอ" breaks it
```

So typed plainly in chat it goes model-intent → `{action:read, entity:report, fields:{type:sales}}`
→ `_handle_report_intent` → falls through to `_handle_sales_summary` (`chat.py:15972`) — the
**old** format, which is correct. The same question through **"สร้างรายงานด้วย AI: …"** or the
**new dashboard page** `presentation/app/liff/sales/reports/ai/AiReports.tsx` (`POST /reports/ai`,
`routers_phase2.py:5084`) goes to Phase 17 → **0**. That is exactly the owner's "ในรูปแบบใหม่".

**f. Nothing tests it.** `tests/unit/test_ai_reports.py` and `tests/integration/test_ai_reports_data.py`
contain no assertion on a deals `sum`/`amount` against seeded line items. The whitelist is
tested; the arithmetic is not.

### Two more defects found on the same road (same class of bug)

- **Filters are equality-only** — `phase17.py:232-233` (`stmt.where(col == value)`), and
  `filters[key] = value` is one string. But the prompt (`reports_ai.py:283`) tells the model
  "ยอดค้าง = sum of outstanding with filter **status issued or partially_paid**" and
  "งานค้าง = status open **or** assigned **or** in_progress". The model can only send one, so
  **ยอดค้างชำระ and งานค้าง silently undercount.**
- **No `closed_at` on a deal.** "won this month" can only be approximated by
  `expected_close_date ?? created_at` (`sales_charts.py:166-172`). Any won-revenue-by-month
  report is a forecast date, not a close date — it must say so.

---

## 2 Current machinery

### What can be answered today

**Fixed / deterministic (chat, `chat.py`)** — สรุปการขาย (pipeline summary), 7 deal queries
(`DEAL_QUERY_PHRASES`, `chat.py:18599`: closing this month/week, overdue, undated, biggest,
won, lost), "ดีลเกิน N บาท", งานค้าง/ticket list, the day's/week's work list, appointment
diary, audit log, invoice payment history.

**Fixed pictures (`sales_charts.py`)** — exactly four: `pipeline` (bar by stage),
`monthly` (line, 6 months of won value), `products` (hbar, top N), `owner` (hbar, per person).
Chosen by keyword in `_chart_request` (`chat.py:30048`). Numbers come from
`pipeline_summary` + `list_deals` — **never** from Phase 17.

**Ad-hoc AI reports (`reports_ai.py` + `phase17.py`)** — 7 entities × 5 metrics × optional
group-by × 9 date ranges:
deals (sum/avg/min/max of `amount`), customers (count), tickets (count), quotes
(`discount_amount`), warranties (count), surveys (avg `score`), invoices
(`total`/`paid_amount`/`outstanding`). group_by ∈ owner_member_id, stage, status, product_id,
assigned_to. Outputs: text, CSV, printable HTML, PDF (SmartBrowz), and one PNG chart.

### How the picture is made today

`application/chann_app/services/charts.py` — **hand-written Pillow drawing**, 532 lines, pure
function (bytes in/out, no I/O). Canvas 1040×780 (value card 520). Palette and Sarabun font
copied from the guide renderer. Four kinds: `bar`, `hbar`, `line`, `value` (a KPI tile, added
in 20L because a single-number report drew nothing at all). Caps: 8 bars / 10 hbars / 14
points, tail folded into "อื่น ๆ". Round 20L rewrote the design after the owner said it looked
bad, and the handoff records **three bugs found only by opening the PNG** (a `1,340,000` label
outside the card, 8 rows overlapping the footer, "เสนอราคาแล้ว" clipped to "เสนอราคาแล้" + "ว").
Standing rule since: touching `charts.py` means rendering every kind and every edge case and
**looking** at them.

`reports_ai.chart_for` (`reports_ai.py:485`) maps spec → Chart; `publish_chart`
(`reports_ai.py:520`) stores the PNG at `reports/{license_id}/charts/{uuid}.png` in the
document store and returns a signed `asset_link` (TTL 1 h). `chat.py:_handle_ai_report`
attaches it as a LINE image message **and** repeats it as a URL, because LINE fetches the
image itself and shows nothing when the fetch fails. Charts are metered:
`chart_quota.spend_one`, charged only after a picture actually exists.

### SmartBrowz (Zoho Catalyst, `services/pdf/smartbrowz.py`)

- `render(html, options, idempotency_key)` → **PDF bytes** (`convert_to_pdf`). Already used
  in production for quote/report PDFs, with retry + timeout handling.
- **`preview_image(html, options)` → screenshot bytes** (`smart_browz.take_screenshot`),
  `smartbrowz.py:238`, declared in the protocol at `pdf/base.py:43`. **It has no production
  caller** — only `tests/unit/test_smartbrowz_pdf_renderer.py:95`. So HTML → PNG is already
  built, paid for, and unused.

---

## 3 The five basic reports

Picked for what a repair/sales shop asks daily **and** what the schema actually supports.

| # | คำถาม (th) | Data source | Definition | Current state |
|---|---|---|---|---|
| 1 | "มูลค่าดีลทั้งหมด" / "ยอดในท่อตอนนี้" | `DealRepository.pipeline_summary` (`phase9.py:854`) | ทุกดีลที่ยังไม่ถูก archive · มูลค่า = line items ถ้ามี ไม่งั้น `amount` · แยกตาม stage (ใหม่/เสนอราคาแล้ว/ปิดสำเร็จ/ไม่สำเร็จ) | **ผิดใน AI report (= 0)** — ถูกในแชทเดิมและในกราฟ pipeline |
| 2 | "ยอดปิดสำเร็จเดือนนี้ เทียบเดือนที่แล้ว" | `list_deals` + `sales_charts.deal_value/deal_closed_on` | stage = `won` · เดือนตาม `expected_close_date ?? created_at` (ไม่มี `closed_at` ในฐานข้อมูล — ต้องเขียนกำกับไว้) | **ยังไม่มี** — Phase 17 ตอบได้ทีละช่วง เทียบสองช่วงไม่ได้ · `monthly` chart มีแต่รูป ไม่มีตัวเลขเทียบ |
| 3 | "งานซ่อมค้างแยกตามช่าง" | `ServiceTicket` group by `assigned_to_ref` | status ∈ open, assigned, in_progress (**สามค่า**) · ไม่จำกัดช่วงเวลา | **ผิด/ไม่ครบ** — filter เป็น `==` ค่าเดียว (`phase17.py:232`) จึงนับได้แค่ `open` |
| 4 | "ยอดค้างชำระ" | `Invoice`, `outstanding = total − paid_amount` (`phase17.py:112`) | status ∈ issued, partially_paid · ตาม `issue_date` · เกินกำหนด = `due_date < today` (derived, ไม่ใช่ status — `models.py:1228`) | **ผิด/ไม่ครบ** — ข้อจำกัด `==` ค่าเดียวเหมือนข้อ 3 · แยก "เลยกำหนด" ยังทำไม่ได้เลย |
| 5 | "คะแนนความพึงพอใจเฉลี่ย" | `SatisfactionSurvey.score` (`models.py:1379`) | avg ของใบที่ตอบแล้ว (`submitted_at` ไม่ว่าง) · ช่วงเวลาตาม `submitted_at` | **ใช้ได้** — ทางเดียวในห้าข้อที่ถูกตอนนี้ (มีหน้า `reports/satisfaction` ด้วย) |

ห้าข้อนี้ควรเป็น **เส้นทาง deterministic** — โค้ดคิดเลข ไม่ผ่าน spec ของโมเดล — และหน้าจอ/แชท
มีปุ่มเรียกตรง (postback) เพื่อให้ "ถูกเสมอ" ตามที่เจ้าของขอ

---

## 4 Rendering options + recommendation

| | ทำยังไง | ข้อดี | ข้อเสีย | effort |
|---|---|---|---|---|
| **(a)** | โมเดลเขียนหน้า HTML (Chart.js/ECharts หรือ inline SVG) จากตัวเลขที่เซิร์ฟเวอร์คำนวณ → SmartBrowz `preview_image` → PNG | ใช้ของที่มีอยู่แล้วและ**ยังไม่มีใครเรียก** (`smartbrowz.py:238`) · ตัวเลข deterministic · โมเดลทำแค่หน้าตา · เลิกดูแล Pillow 532 บรรทัด | เพิ่มค่า render ต่อรูป + latency วินาที · ต้องพิสูจน์ก่อนว่า `take_screenshot` รอ JS วาด canvas จบ (Chart.js ใช้ canvas) — ถ้าไม่รอ ให้โมเดลเขียน **inline SVG/CSS ล้วน ไม่มี JS** ซึ่งปลอดภัยกว่าและยัง "สวยตามที่โมเดลออกแบบ" | M |
| (b) | ส่ง spec ให้บริการรูปภายนอก (QuickChart ฯลฯ) | เร็ว, งานน้อยที่สุด | dependency ใหม่นอก GCP · **ตัวเลขของร้านออกนอกระบบ** (PDPA) · ค่าใช้จ่าย/ความพร้อมนอกมือเรา | S |
| (c) | image-generation model วาดกราฟเอง | "สวย" ที่สุดในแง่ภาพ | **ตัวเลขเชื่อไม่ได้** — โมเดลภาพไม่ได้คำนวณ มันวาดสิ่งที่ดูเหมือนกราฟ แท่งไม่ตรงสัดส่วน ตัวเลขบนป้ายผิดได้เงียบ ๆ · ขัดกฎ model-first โดยตรง (CLAUDE.md ข้อ 6: "โมเดล*เสนอ* โค้ด*ตรวจแล้วทำ*") และขัดบทเรียน dumped-arithmetic | **ไม่แนะนำ** |
| (d) | เซิร์ฟเวอร์ถือตัวเลข · โมเดลเลือกแค่ชนิดกราฟ + ป้าย + สี → render ผ่าน (a) | ผลลัพธ์ตรวจสอบได้ · prompt สั้น ถูก และล้มยาก · รองรับ "รายงานที่เหลือให้ AI ช่วยดู" ได้จริง | ยังต้องมี template HTML ของเราเป็นฐาน (แต่เป็น HTML/CSS ที่แก้ง่าย ไม่ใช่ Pillow) | M |

**แนะนำ: (d) วางบนท่อของ (a)** — โมเดลรับ *ตัวเลขที่คำนวณเสร็จแล้ว* + คำถามเดิม แล้วคืน
"chart plan" (kind, ป้าย, การจัดเรียง, หน่วย, highlight) เป็น JSON ที่ validate ได้ เรา render
เป็น HTML/inline-SVG แล้วส่งเข้า SmartBrowz `preview_image` → PNG → document store → LINE
(ท่อเก็บ/ส่งรูปเดิมทั้งหมดใช้ต่อได้ ไม่ต้องแตะ) เปิดช่องให้โมเดลเขียน HTML เองได้เต็มใบเมื่อคำถาม
แปลกจนแผนสำเร็จรูปไม่พอ — แต่**ยังส่งตัวเลขที่เราคำนวณไปแล้วเท่านั้น**

**สิ่งที่ห้ามมอบให้โมเดลเด็ดขาด: การคำนวณ.** `reports_ai.py:1-8` เขียนไว้เองว่า "The model never
sees SQL and never sees data"; `phase17.py:1-6` ว่าทุก statement สร้างจาก whitelist และ filter
ด้วย `license_id` เสมอ; บทเรียน round 20K (`scripts/dev/check-dumped-arithmetic.py` +
`docs/SESSION_HANDOFF.md:545+`) คือ **ตัวเลขที่ผ่านการแปลงร่างแล้วเอาไปบวกต่อ พังเงียบ** — กฎเดิมยังใช้:
ตัวเลขมาจาก SQL ที่ tenant-scoped, โมเดลได้เห็นแค่ผลรวมที่จะพิมพ์ลงรูปอยู่แล้ว

---

## 5 Proposed plan

**ขั้นที่ 1 — หยุดเลือดก่อน (ตัวเลข 0)** · effort S · *นี่คือของที่เจ้าของบ่น*
1. `data/chann_data/repositories/phase17.py:104` — `deals.amount` → นิพจน์เดียวกับ
   `pipeline_summary`: `coalesce(sum(DealProduct.qty*quoted_unit_price), Deal.amount, 0)`
   พร้อม `outerjoin(DealProduct)` + `group_by(Deal.id)` แล้วห่อเป็น subquery ก่อนรวม
   (ระวัง: การ join ทำให้ `count()` นับซ้ำ — ต้องแยก path ของ count ออก)
2. เอานิยาม "มูลค่าดีล" ไปไว้ที่เดียว (helper ใน `phase9.py`) แล้วให้ทั้ง `pipeline_summary` และ
   `phase17` เรียกตัวเดียวกัน — ตอนนี้มีสี่สำเนา และสองสำเนาไม่ตรงกันด้วยซ้ำ
   (pipeline = line items ก่อน; DealList/chat = `amount>0` ก่อน) **ต้องถามเจ้าของว่าอันไหนถูก (Q1)**
3. Tests: integration ที่ seed ดีล 3 ใบ — ใบมี line items อย่างเดียว / ใบมี amount อย่างเดียว /
   ใบมีทั้งสอง — แล้ว assert ว่า AI report กับ `pipeline_summary` **ได้เลขเท่ากัน**
4. `scripts/dev/simulate-phrasings.py` + `scripts/agent-test/run.py` ตัวที่แตะ report
   Risk: ตัวเลขของร้านจริงจะ "กระโดด" จาก 0 ขึ้นมา — ตั้งใจ ต้องบอกเจ้าของก่อน

**ขั้นที่ 2 — filter หลายค่า** · effort S/M
5. `phase17.validate_spec` + `build_statement`: รับ list → `col.in_(values)` ·
   `reports_ai.validate_query_spec` + prompt + `spec_options` (หน้าจอสร้างจาก whitelist
   ตัวเดียวกันอยู่แล้ว) · ทดสอบสองทิศ: "งานค้าง" ได้ open+assigned+in_progress, "ยอดค้างชำระ"
   ได้ issued+partially_paid

**ขั้นที่ 3 — รายงานพื้นฐาน 5 อัน เป็นเส้นทางตายตัว** · effort M
6. โมดูลใหม่ `application/chann_app/services/basic_reports.py` — ห้าฟังก์ชัน ไม่ผ่านโมเดลเลย
   (โมเดลยัง**อ่าน**ประโยคเพื่อเลือกว่าอันไหน ตาม model-first — แต่ไม่ได้คิดเลข)
7. ทางเข้า: ปุ่มในแชท + การ์ดบนหน้า `reports/ai` (ใช้ ui-ux skill ตามกฎเจ้าของ 20 ก.ย.) +
   ประโยคที่ `_is_ai_report_request` ปล่อยผ่านอยู่แล้ว
8. #2 (เทียบเดือน) ต้องมีประโยคกำกับว่าใช้วันคาดว่าจะปิด ไม่ใช่วันปิดจริง จนกว่าจะมี `closed_at` **(Q2)**

**ขั้นที่ 4 — รูปที่ AI ออกแบบ** · effort M/L
9. **พิสูจน์ก่อนเขียน**: เรียก `SmartBrowzPdfRenderer.preview_image` กับหน้า Chart.js หนึ่งใบบน DEV
   แล้ว**เปิดรูปดู** — ถ้า canvas ว่าง แปลว่า `take_screenshot` ไม่รอ JS → เปลี่ยนเป็น inline SVG
10. `services/chart_plan.py` — schema ของ "chart plan" + validator (kind ∈ whitelist, ป้ายไม่เกิน N,
    ตัวเลขมาจาก result เท่านั้น ห้ามโมเดลส่งตัวเลขมา) · renderer HTML/SVG ที่ใช้ธีม OA เดิม
11. `reports_ai.publish_chart_for` เปลี่ยนท่อภายใน — path เก็บไฟล์, asset link, quota,
    ข้อความใน LINE ไม่ต้องแตะ · `charts.py` (Pillow) เก็บไว้เป็น fallback เมื่อ SmartBrowz ล่ม
12. Render ทุก kind ทุกเคสขอบ (ชื่อยาว / 0 / 7 หลัก / 8+ แถว / ค่าติดลบ) แล้ว **ดูด้วยตา** —
    กฎ round 20L; เทสต์มองไม่เห็นป้ายที่ล้นการ์ด
    Risk: SmartBrowz เป็น dependency ภายนอก + มี quota — ต้องมี fallback และวัด latency

**ความเสี่ยงรวม:** แตะ chat = ต้องรัน `scripts/agent-test/run.py --real` ก่อน deploy (CLAUDE.md) ·
`gate.sh`/`converse.py` default ไปที่ `~/stage-fix/registry` ต้องชี้มาที่ tree ที่แก้จริง ·
ร้านที่เห็น 0 มาตลอดจะเห็นเลขเปลี่ยน — เป็นการแก้ ไม่ใช่ regression แต่ต้องแจ้ง

---

## 6 Open questions for the owner

1. **ดีลที่มีทั้ง `amount` ที่พิมพ์ไว้ และ line items — เอาอันไหน?** ตอนนี้ระบบตอบไม่ตรงกันเอง:
   การ์ด pipeline ใช้ line items ก่อน แต่หน้ารายการดีลกับแชทใช้ `amount` ก่อน ต้องเลือกหนึ่งอัน
   แล้วใช้ทั้งระบบ
2. **"ยอดปิดสำเร็จเดือนนี้" ให้นับตามวันไหน?** ฐานข้อมูลไม่มีวันที่ปิดจริง มีแต่ "วันที่คาดว่าจะปิด"
   กับวันที่สร้าง — จะให้ใช้วันคาดการณ์ไปก่อน หรือให้เพิ่มคอลัมน์ `closed_at` (ดีลเก่าจะไม่มีข้อมูลย้อนหลัง)
3. **รูปที่ AI ออกแบบ ยอมให้ใช้ SmartBrowz (Zoho) ต่อรูปไหม?** มีค่าใช้จ่ายและ quota ต่อการเรนเดอร์
   และช้ากว่าวาดเองประมาณวินาทีต่อรูป — หรือจะจำกัดให้ใช้เฉพาะรายงานนอก 5 อันพื้นฐาน
