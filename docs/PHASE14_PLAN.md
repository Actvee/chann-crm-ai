# Phase 14 — Approval Workflow + Satisfaction Survey: แผนสร้าง (3 ก.ย. 2569)

## การตัดสินใจของเจ้าของโปรเจกต์ (ล็อกแล้ว)

1. **Default workflow = CS อนุมัติขั้นเดียว** — ไม่มีขั้น admin ต่อท้ายแบบใน
   spec §14.4; ขั้นเดียวจบ
2. **ส่งทันที** — ช่างกด "ปิดงาน+ส่งรายงาน" → คำขออนุมัติไปถึง CS เจ้าของ
   ticket ในวินาทีนั้น (ไม่รอรอบ digest) → CS อนุมัติ → survey ไปถึงลูกค้าทันที
   *(ผมตีความ "ส่งทันทีหลังช่างปิดงาน" ว่าหมายถึงคำขออนุมัติ ส่วน survey ไป
   หลัง CS อนุมัติตาม spec §14.5 — ถ้าตั้งใจให้ survey ไปทันทีที่ปิดงานโดยไม่รอ
   CS บอกได้ เปลี่ยนแค่จุดเดียวใน executor)*
3. **ปรับ flow ได้ด้วยการพิมพ์ในแชท Sale OA** — เช่น "ตั้งการอนุมัติ service
   report ให้ CS แล้วต่อด้วย admin" → AI แปลงเป็น rules_json → บันทึกเป็น
   workflow ใหม่ของ tenant · gate ด้วย permission ตาม concept เดิม:
   `approval.manage` (มีใน catalogue อยู่แล้ว: `approval.view / approve /
   reject`; เพิ่ม `manage` สำหรับแก้ flow — Owner/Admin ถือโดย default)

## สิ่งที่มีอยู่แล้วและจะต่อยอด (ไม่สร้างซ้ำ)

- `service_reports.status` มี `submitted / approved / rejected` แล้ว และ
  **check-out ตั้ง `submitted` อยู่แล้ว** (phase13 repo) — trigger ของ workflow
  จึงมีอยู่ในระบบ แค่ยังไม่มีใครฟัง
- `approval.view / approve / reject` อยู่ใน permission catalogue แล้ว และ
  เมนูแชทแสดงหมวด "การอนุมัติ" อยู่แล้วโดยไม่มี handler (จอ 12:08)
- Notification pipeline ของ Phase 6 (LINE push + digest) และ Customer OA
  quick reply สำหรับ survey
- แดชบอร์ดช่างมีปุ่มปิดงานแล้ว (3 ก.ย.) — UI ฝั่งช่างไม่ต้องแตะ

## แตกเป็น 3 patch — deploy แยกกัน แต่ละอันมีค่าในตัวเอง

### Patch 14-A — Data tier: ตาราง + repo + routes (migration `0021`)
- ตาราง 3 ตัวตาม spec §14.3 (`approval_workflows`, `approval_steps`,
  `satisfaction_surveys`) — แก้ FK ผิดใน spec: `satisfaction_surveys.license_id
  → licenses.id` ไม่ใช่ `services.id`
- Repo `phase14.py`: `ensure_default_workflow(license)` (CS-only, สร้างตอนใช้
  ครั้งแรก), `open_steps_for(entity)`, `act_on_step(step, approve|reject,
  actor)` ที่ตรวจ tenant + ตรวจว่า actor คือ approver ที่ถูกต้อง,
  `create_survey(ticket)`, `submit_survey(survey, score)`
- Routes internal: CRUD ตามนั้น + `GET /approvals/pending?for=<chann_uid>`
- **Domain rule ใน data tier ไม่ใช่ application**: อนุมัติครบทุกขั้น →
  `service_reports.status = approved` ใน transaction เดียวกับ step สุดท้าย
  (บทเรียน check-out: สองสถานะที่ต้องตรงกันต้องเขียนพร้อมกัน)
- Tests: `test_approval_workflow` + `test_multi_tenant_approval` ตาม §14.6
- Audit: ใช้ verb `update` ที่มีอยู่ (ไม่เพิ่ม verb ใหม่ — บทเรียน Phase 3)

### Patch 14-B — Application: executor + แชท + survey sender
- `services/approval.py` — domain service **ตัวเดียว** ที่ทั้งแชทและ
  dashboard เรียก (§14.6 `test_approval_chat_vs_dashboard` บังคับ):
  `on_report_submitted(report)` → สร้าง steps → push LINE ถึง CS เจ้าของ
  ticket ทันที (ข้อ 2) · `approve(step, actor)` / `reject(step, actor,
  reason)` → ครบ = ส่ง survey ผ่าน Customer OA เป็น quick reply 1–3
  (`scale_config_json` default `{1:"ไม่ดี",2:"พอใช้",3:"ดีเยี่ยม"}`)
- Hook: route check-out ที่เพิ่ง เพิ่มเมื่อ 3 ก.ย. + เส้นแชท check-out เรียก
  `on_report_submitted` — ที่เดียวกัน
- แชท (Sale OA): `รายการรออนุมัติ`, `อนุมัติ SR-2026-0001`, `ไม่อนุมัติ
  SR-2026-0001 <เหตุผล>`, reply-to ข้อความแจ้งเตือนแล้วพิมพ์ "อนุมัติ" ต้อง
  ใช้ได้ (ใช้ `_record_scope` + message-entity map ที่มีอยู่) · ชื่อ/รหัสซ้ำ
  ต้องได้ตัวเลือก (กติกา 2 ก.ย.)
- แชท (Sale OA, `approval.manage`): "ตั้งการอนุมัติ <entity> ให้ <ขั้น1>
  แล้ว <ขั้น2>" → AI → rules_json → ยืนยันเป็นภาษาคน → บันทึก · "ดูการ
  อนุมัติปัจจุบัน" แสดง flow เป็นข้อความ
- Customer OA: รับคำตอบ survey (quick reply postback หรือพิมพ์ 1/2/3) →
  `submit_survey`; ไม่ตอบ = ไม่บังคับ
- Tests: `test_survey`, chat-vs-dashboard parity, AI policy → rules_json
  (ป้อน intent ปลอมแบบเดียวกับเทสต์อื่น)
- ACTION_PERMISSIONS: `("approve","approval")`, `("reject","approval")`,
  `("read","approval")`, `("update","approval")` → check-parity จะบังคับให้
  14-C มาครบ

### Patch 14-C — Presentation: approval queue + config + survey
- Sale dashboard: หน้า **รอการอนุมัติ** (list + ปุ่มอนุมัติ/ไม่อนุมัติ + ช่อง
  เหตุผล) เรียก `services/approval.py` ผ่าน route เดียวกับแชท
- หน้า **ตั้งค่าการอนุมัติ** (Owner/Admin): แสดง flow ปัจจุบันเป็นขั้น +
  ช่องพิมพ์นโยบายภาษาคน (ผ่าน AI ตัวเดียวกับแชท) — parity กับข้อ 3
- Customer home: การ์ด "ประเมินความพึงพอใจ" เมื่อมี survey ค้าง (parity
  กับ quick reply ในแชท)
- ธีมสีตาม OA ที่ทำไว้แล้ว

## ลำดับ/เวลา
14-A → 14-B → 14-C ตามลำดับ (แต่ละอันมี deploy script ของตัวเอง) · fix จาก
การใช้จริงยังเป็น patch แยกเสมอ ไม่ปนกับ phase

## เกณฑ์ปิด Phase (ตาม §14.7 + กติกาของโปรเจกต์)
- [ ] runtime จริง: ช่าง check-out → CS ได้ LINE ทันที → อนุมัติในแชท → ลูกค้า
      ได้ survey ทันที → ตอบ → score บันทึก
- [ ] ทำซ้ำเส้นเดียวกันผ่าน dashboard ได้ผลเหมือนกัน
- [ ] แก้ flow ด้วยแชทแล้วเส้นถัดไปเดินตาม flow ใหม่
- [ ] multi-tenant isolation test PASS · check-parity สะอาด · sims 0

---

## สถานะ 14-A (3 ก.ย.) — deploy แล้ว (`5598ad1`)

Data tier ทั้งหมดของ Phase 14: migration `0021_approvals`, model 3 ตัว,
`repositories/phase14.py`, internal routes 9 เส้น, integration tests 12
ตัว (รวม HTTP และ multi-tenant) — ทั้งหมดผ่านบน Postgres จริง
`EXPECTED_MIGRATION_HEAD` ของ data tier เป็น `0021_approvals` แล้ว จึงต้อง
**รัน migration job ก่อน deploy data image** (script จัดลำดับให้)
ชื่อจริงใน repo ต่างจากแผน: `active_workflow` / `open_steps_for_report` /
`act` / `_ensure_survey` / `submit_survey` — ดู `phase14.py`

## สถานะ 14-B (3 ก.ย.) — เสร็จ (`phase14b-v1`)

Application tier ครบตามแผน โดยยึด **domain service ตัวเดียว**
`application/chann_app/services/approval.py` ที่ทั้งแชทและ route เรียก:

- `on_report_submitted` — hook หลัง check-out สำเร็จ **ทั้ง 2 ทาง** (route
  `tickets/{id}/check-out` และแชท 2 จุดใน `_handle_check_out`): เปิด steps
  แล้ว push LINE ถึงผู้อนุมัติขั้นแรก **ทันที** (ข้อ 2) ข้อความที่ push ถูก
  map ใน `line_message_entity_map` (notify.py บันทึก id ที่ `push_text` คืน
  มา) จึง **reply ข้อความแจ้งเตือนแล้วพิมพ์ "อนุมัติ" ได้**
- `act` — อนุมัติ/ไม่อนุมัติ ผ่าน `act_on_approval_step` ของ data tier (กฎ
  อยู่ฝั่งนั้น) แล้วทำผลลัพธ์: rejected → แจ้งช่างทาง Technician OA พร้อม
  เหตุผล · approved (ขั้นสุดท้าย) → `send_survey` quick reply 1–3 ไปที่
  Customer OA (message action ส่งตัวเลข ไม่ใช่ postback — webhook เดิมรับได้)
  → `mark_survey_sent` · ยังมีขั้นต่อ → แจ้งผู้อนุมัติขั้นถัดไป
- แชท Sale OA (longest-first: ตั้งการอนุมัติ → ยืนยันการอนุมัติ → ดูการอนุมัติ
  ปัจจุบัน → รายการรออนุมัติ → ไม่อนุมัติ → อนุมัติ): ระบุรายงานได้ 4 ทาง
  รหัส `SR-…` / บริบทที่เพิ่งดู / reply ข้อความแจ้งเตือน / มีรายการเดียว —
  หลายรายการได้ปุ่มเลือก (กติกา 3) · ไม่อนุมัติต้องมีเหตุผล · 409/404 เป็นไทย
- นโยบายด้วยภาษาคน: `services/ai/approval_policy.py` (prompt ปิด vocab,
  validate โครงสร้างเหมือน `replace_workflow` + บทบาทต้องมีจริง) → โชว์กลับ
  → `ยืนยันการอนุมัติ` → `replace_approval_workflow` · gate `approval.manage`
  (key ใหม่ใน catalogue — data tier; owner/admin/cs ได้ตาม template)
- Customer OA: ตอบ survey ด้วยเลข 1–3 หรือคำในสเกล ก่อน catch-all แจ้งซ่อม;
  ไม่มี survey ค้าง = ข้อความเดิมเป็นอะไรก็เป็นอย่างนั้น
- Routes สำหรับ 14-C: `GET approvals/pending`, `POST approvals/{step}/approve|
  reject`, `GET|PUT approval-workflows/{entity}` (PUT รับ `policy` ข้อความ
  หรือ `rules_json`), `GET surveys/pending`, `POST surveys/{id}/answer`
- `ACTION_PERMISSIONS` ลงทะเบียน read/approve/reject/update ของ `approval`;
  check-parity ถือเป็น KNOWN_GAPS จนกว่า 14-C จะมา
- Tests: `tests/unit/test_phase14_chat.py` (รวม chat-vs-dashboard parity:
  route กับ handler บันทึก call เดียวกัน) · simulate-day มี scene
  `approval_day` (check-out → approve → survey → reject → ตั้ง flow)

**ยังไม่ทำใน 14-B**: ลายเซ็นบน PDF ตอน approve (spec 14.5 บรรทัดสุดท้าย —
ต่อกับ Phase 13 PDF ซึ่งยังไม่มี renderer จริง); AI ตัดสินอนุมัติเอง
(`ai_reasoning` วางคอลัมน์ไว้เฉยๆ ตามแผน)
