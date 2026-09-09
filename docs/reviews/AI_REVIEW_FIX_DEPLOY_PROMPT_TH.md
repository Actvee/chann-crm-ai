คุณรับช่วงงานใน GitHub repository `Actvee/chann-crm-ai` เพื่อ review แก้ระบบสนทนาและ Dashboard แล้วเตรียม/ดำเนิน DEV deployment ตามสิทธิ์และ release gates ที่มีอยู่

ไฟล์ ZIP ที่แนบมี patch `customer-language-ui-v2-<linecount>.patch`, CHECKSUMS.json,
รายงานและผลทดสอบ. การเชื่อม GitHub อย่างเดียวจะไม่เห็นการแก้ชุดนี้: ต้องรับ patch
จากไฟล์แนบก่อน เว้นแต่ตรวจแล้วพบว่า source changes ถูก merge เข้า repo แล้ว
Base ของ patch คือ `2345073c5bd61bbaf7b2ea160c638d0babc5c081`.
ยังไม่ได้ push, deploy, เรียกโมเดลจริง หรือรับรอง UI จากภาพหน้าจอในรอบผู้ส่ง

เป้าหมายผู้ใช้:
- จำลองข้อความลูกค้าไทยหลายแบบ รวมพิมพ์ผิด เว้นวรรค ภาษาพูด/ถิ่น อังกฤษปนไทย
  คำปฏิเสธ คำถาม สมมติ อ้างคำพูด เปลี่ยนใจ เติมข้อมูลผิดลำดับ ชื่อซ้ำและหลาย intent
- ระบบไม่ทำรายการผิดเจตนา ไม่เดาข้อมูลที่มีหลายตัวเลือก เก็บข้อมูลถูกต้อง
- ตรวจความถูกต้องและความง่าย/สวยของ Dashboard ทุก OA และ Platform Admin
- ทดสอบโมเดลที่ใช้จริง แล้วปรับแก้และ deploy ผ่าน flow ที่ repo กำหนด

เริ่มงาน:
1. อ่าน CLAUDE.md, AGENTS.md ถ้ามี, docs/00_START_HERE.md,
   docs/SESSION_HANDOFF.md, docs/AI_HANDOFF_START_PROMPT.md,
   docs/CHANN_CRM_AI_MASTER_SPEC.md และเอกสาร phase/infra ที่เกี่ยวข้อง
   คำสั่งผู้ใช้รอบนี้อนุญาตให้แก้ source code ไม่ได้จำกัดเป็น test-only ตาม PROMPT เดิม
2. ตรวจ git status, fetch และ SHA ล่าสุด. สร้าง isolated review branch/worktree.
   อย่า reset --hard ทับงานที่ค้าง; ถ้า main เปลี่ยน ให้ rebase/reconcile ตามจริง
3. ตรวจ SHA256 + linecount จาก CHECKSUMS.json แล้ว `git apply --3way --check`
   บน clean checkout ของ base ก่อน apply. ถ้าไฟล์/โค้ดมีอยู่แล้ว ให้เทียบเนื้อหาและ
   ข้ามส่วนที่นำเข้าแล้วอย่างมีหลักฐาน ไม่ apply ซ้ำหรือทับอย่างเดา
4. อ่าน docs/reviews/customer-language-dashboard-review.md และ
   scripts/agent-test/MODEL_EVALUATION.md. แยกข้อเท็จจริงจาก backlog และ NOT_RUN

สิ่งที่ patch นี้ทำ:
- กัน check-in จากคำถาม/ปฏิเสธ/อนาคต/คำอ้างอิง
- กันการเปิด ticket เมื่อยังไม่ได้แจ้งซ่อม/ถามราคา; ไม่เก็บคำทักทายและวันเป็นที่อยู่
- แก้เวลาไทย, explicit quantity 0, never mind, เติม customer slot ผิดลำดับ
- เพิ่มภาษาพูดแคบ ๆ และ regression แบบมี read-back
- แก้ harness ให้เห็น list_card actions, ผล pytest ราย scenario และ offline isolation
- เพิ่ม 132 language scenarios รวมของเดิมเป็น 135 scenarios / 459 steps
- เพิ่ม real-model parser corpus 49 cases พร้อม opt-in evaluator และรายงานรายเคส
- ปรับ Sales/CS shortcuts, pipeline loading/empty/error/retry, approval empty-state,
  technician report HTTP-error และอธิบาย Admin KPI หลัง filter

Review อย่างมีวิจารณญาณ:
- ตรวจ regex ไม่ให้กว้างจนกันคำสั่งจริง เช่น "แอร์ไม่เย็น", "เครื่องไม่ทำงาน",
  คำสั่งสุภาพ, อาการเสียพร้อม BTU/ราคา, คำว่าไม่เอาที่เป็นส่วนหนึ่งของคำอื่น
- ไม่ถือว่าเวลาที่ parser เดาได้คือถูก: echo เวลา, ตรวจ Thai digits/คำ/half-hour,
  invalid time, past date, Asia/Bangkok และการอ่านกลับจาก DB
- ตรวจ quantity 0/negative/decimal และ context ที่ไม่มี/มีหลาย line items
- ห้ามเปลี่ยน expected เพื่อให้เขียวโดยไม่มีเหตุผลทางธุรกิจและหลักฐาน
- ปุ่มลูกค้าชื่อซ้ำอยู่ใน list_card แล้ว: bug เดิมส่วนนี้เป็น false positive ของ harness
- ambiguous-name-02/04/05 ใช้ supplied intent: ผ่านไม่ได้แปลว่าโมเดล parse ได้
- กติกาไม่ใช่ auth boundary. ทดสอบ server/Data permission + tenant isolation เสมอ

ทดสอบ offline:
- ติดตั้ง dependencies ตาม repo ใน env แยก; ใช้
  `PYTHON_BIN=.venv/bin/python bash scripts/agent-test/review-before-deploy.sh`
  หรือคำสั่งเดียวกันทีละขั้นพร้อมเก็บ logs
- baseline ผู้ส่ง: unit+boundary 1,840 passed / 2 live-Zoho tests deselected,
  agent channel 135/135, day+edge 0 FINDINGS,
  phrasings 488 / 11 not-as-expected / 0 long, UI typecheck/build ผ่าน
- check-* exit 0 ไม่เท่ากับไม่มี findings: เปรียบเทียบ log baseline ใน ZIP
  check-parity มี product.delete backlog 1 รายการและ accepted exceptions 33 คู่
- สอง Zoho tests ต้องรันต่างหากใน environment ที่อนุญาตก่อน claim PDF runtime
- เพิ่ม negative tests ทุกบัคและ positive neighboring cases ป้องกัน overblocking

ทดสอบโมเดลจริง (จำเป็นก่อนรับรองความเข้าใจภาษา):
1. ใช้ OPENROUTER_MODEL, provider policy และ prompt เดียวกับ DEV ที่จะ deploy
   ห้ามเดาชื่อโมเดลจากความจำ และไม่แสดง API key/secret ใน output, commit หรือ prompt
2. Review oracle ใน model-cases.json ให้ตรง requirement ก่อนรัน เก็บ original corpus
3. ค่า config ต้องมีอยู่ผ่านกลไกที่อนุญาตแล้ว; ห้ามค้น/แก้ Secret Manager หรือ IAM
4. รัน smoke:
   `python scripts/agent-test/evaluate-model.py --run --limit 5 --repeat 1 --output /tmp/model-smoke.json`
   แล้ว full:
   `python scripts/agent-test/evaluate-model.py --run --repeat 3 --output /tmp/model-acceptance.json`
   49 cases × 3 = 147 evaluations, สูงสุด 294 HTTP attempts ตาม retry ปัจจุบัน
   ค่าใช้จ่ายดู provider-reported usage; ถ้าไม่มี cost ให้รายงาน unknown
5. รายงาน expected/actual, category, failures, instability, p50/p95 และจำนวน HTTP calls
   หากไม่เรียกได้ให้ระบุ BLOCKED/NOT_RUN ห้ามใช้ผล mock แทน
6. ต่อด้วย DEV end-to-end ผ่าน Application→Data→DB บน synthetic tenant/identities
   ไม่ส่ง LINE ให้ลูกค้าจริง. ถ้า outbound แยกไม่ได้ ให้หยุดเฉพาะขั้นนั้นและแจ้ง blocker
   ตรวจทั้งข้อความและ actual writes/read-back: ticket status, service_address,
   due date/time, customer fields, deal quantity/amount, approval/audit events
7. Parser-only evaluator ไม่พิสูจน์ OA routing, DB transactions, LINE, UI, PDF หรือ
   reasoning model. อย่ารวม pass rate เหล่านี้เป็นตัวเลขความแม่นยำเดียว

ตรวจ UI จริงครบ:
- ใช้ docs/reviews/dashboard-route-inventory.json: 38 routes รวม dynamic details,
  Sales, Customer, Technician, guides, signature, Admin tenants/login/audit/PDPA
- ใช้ session ที่ได้รับอนุญาตและข้อมูลสังเคราะห์: owner, sales, CS, technician lead,
  technician member, customer, user ที่ไม่มีสิทธิ์; สลับร้าน/หมด session/ร้าน suspended
- ภาพที่ 390px mobile และ 1440px desktop ทุกหน้า; 320px/zoom 200% สำหรับหน้าหนาแน่น
- ตรวจ loading/empty/error/403/500/slow request, retry, duplicate taps, stale response,
  long Thai names/text, 0/1/50/500 rows, search/filter/sort/pagination, TH/EN
- ตรวจ keyboard Tab/Escape/focus return, accessible label, contrast, target size,
  horizontal overflow, mobile keyboard, safe-area, upload/photo/GPS และ signature
- ตรวจ Dashboard metric ตรง DB รวม filtered Admin metrics และ pipeline overdue/value
- ตรวจ LIFF deep links + back navigation ใน LINE จริง; อย่าแก้ auth ด้วย mock/bypass
- Customer home ยังโหลด 4 ส่วนแบบ all-or-nothing: แยก failed section/retry ถ้ายืนยัน
  failure UX. Technician report error ที่แก้ครอบคลุม HTTP non-OK; ตรวจ transport/JSON
  errors เพิ่ม. ตรวจเอกสาร/AI report ด้วยระบบจริงของแต่ละ feature
- เก็บ before/after screenshots และผลรายหน้า: PASS / FAIL / BLOCKED พร้อม role,
  viewport, state. ยังไม่มี screenshot = ยังให้คะแนนความสวยหรือรับรองทุกหน้าไม่ได้

แก้แล้วนำส่ง/deploy:
- รักษา 4-tier, shared session, permission checks, OA separation, TH/EN และ parity
- ห้ามแตะ IAM, Service Account, Secret Manager หรือทำ destructive data change
- runtime scope ของ patch ปัจจุบัน: Application + Presentation; ไม่มี migration/Data change
  ถ้าคุณแก้เพิ่มให้คำนวณ impact ใหม่และ validate dependency tiers
- อ่าน latest owner-approved deployment script จาก handoff ก่อน. ระวัง
  `scripts/dev-deploy.sh` ใน base นี้เป็น dry-run scaffold พิมพ์คำสั่งเท่านั้น!
  ห้ามรันแล้วอ้างว่า deploy สำเร็จ
- สร้าง/ใช้ owner-gated script ตาม CLAUDE.md: SYNC+linecount → APPLY+symbol checks →
  TEST → COMMIT+PUSH → BUILD เฉพาะ tiers → UPDATE tfvars แบบ anchor-edit →
  PLAN (retry ตาม script, หยุดถ้ามี destroy) → APPLY เมื่อเจ้าของให้ ALLOW_APPLY=YES
  ตาม gate ที่มีอยู่ → RUNTIME CHECK. ไม่ข้าม gate/ไม่เรียก gcloud update ตรง ๆ
- user ต้องการ Dev + Production ไม่เพิ่ม Stage. ทำ DEV ตาม authorization ที่มี;
  Production ต้องมี approval และ artifact/runtime acceptance ตาม release flow
- ทดสอบ patch บน fresh clone เสมอ. ส่ง patch ชื่อ topic-vN-linecount.patch + SHA256,
  release script ที่ review ได้, logs, screenshots, rollback revision และผลจริง
- สำเร็จ deployment ต่อเมื่อ `/health` รายงาน full SHA ตรง commit ที่ push,
  Presentation `/api/ready` ผ่าน และ business/UI acceptance ผ่าน ไม่ใช่แค่ build ผ่าน

คำตอบส่งผู้ใช้เป็นภาษาไทย: อะไรแก้แล้ว/ยังมีปัญหา, ผล offline/model/DB/UI แยกกัน,
SHA/PR/revision/URL ที่พิสูจน์แล้ว, screenshots, สิ่งที่ยัง BLOCKED และขั้นตอนถัดไป
ห้ามอ้างว่า deployed หรือ model/UI ผ่านเมื่อไม่มีหลักฐาน
