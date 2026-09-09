# Customer language and dashboard review — 9 September 2026

Base: `2345073c5bd61bbaf7b2ea160c638d0babc5c081`, `Actvee/chann-crm-ai`.
This is a source patch and offline verification, **not a deployed release**.

## ผลที่พิสูจน์แล้ว

- Agent channel: 135 scenarios ผ่าน / 459 steps ผ่าน. ชุดที่เพิ่มรวม 132 scenarios, 287 ข้อความ, 419 steps; อีก 3 scenarios เป็นของเดิม
- Python unit + boundary: 1,840 ผ่าน; ไม่รัน 2 tests ที่เรียก Zoho endpoint จริง ไม่มีการปิด/ลบ test เหล่านั้นใน source
- simulate-day และ simulate-edge-cases: 0 FINDINGS
- simulate-phrasings: 488 cases / 11 not-as-expected / 0 long replies เท่ากับ baseline; 11 รายการยังเป็นข้อจำกัดเดิม ไม่ใช่ผ่านทุกภาษา
- check-* ทั้ง 12 scripts ออก 0; findings เทียบ baseline ไม่เพิ่ม นอกจากตัวนับ i18n ที่เพิ่มตามข้อความ UI. Script เหล่านี้เป็น heuristic: บางตัวออก 0 แม้พิมพ์ findings จึงไม่ใช่หลักฐานว่าปราศจากปัญหา
- check-parity ยังคง backlog `product.delete` 1 รายการ และข้อยกเว้นที่บันทึกไว้ 33 คู่
- Presentation typecheck และ production build ผ่าน; navigation logic 5 checks ผ่าน
- Real-model corpus 49 cases: dry run ผ่าน แต่ acceptance **NOT_RUN** เพราะ OPENROUTER_API_KEY / OPENROUTER_MODEL ยังไม่ได้ configure ที่นี่
- ไม่ได้รัน PostgreSQL backend, live LINE, authenticated UI, real PDF หรือ deploy

## แก้ข้อสรุปรอบแรกให้ถูกต้อง

รายงานเดิม 33 failures ประกอบด้วย 26 รูปแบบที่ทำซ้ำปัญหา 8 กลุ่ม, 1 false positive ของเครื่องมือทดสอบ และ 6 ช่องว่างที่ยังไม่พิสูจน์ด้วยโมเดลจริง ไม่ใช่ 9 บัคอิสระทั้งหมด

False positive: ผลค้นหาลูกค้ามีปุ่มใน `list_card` อยู่แล้ว แต่ harness ดูเฉพาะ quick replies. แก้ harness ให้เก็บ list_card และตรวจ `actions_include` จาก message payload จริง. มี test ยืนยันว่าข้อความ/label ที่ไม่มี action ไม่ผ่าน

3 เคสค้นชื่อ (`ambiguous-name-02/04/05`) ใช้ supplied intent อย่างชัดเจนเพื่อทดสอบการเลือกชื่อซ้ำหลัง parse. ประโยคค้นหายังต้องพิสูจน์ด้วยโมเดลจริง; ไม่เพิ่ม regex มาดักให้ชุดทดสอบดูผ่าน และไม่อ้างว่าโมเดลเข้าใจแล้ว. ส่วน "บวกพัดลมอีก 3 ตัว", "แก้เป็น 3", "ถึงละคับ" เพิ่มการรองรับในกติกาที่ตรงบริบท

## การแก้ chat

| กลุ่ม | การแก้ | หลักฐาน |
|---|---|---|
| Check-in ผิดเจตนา | กันคำปฏิเสธ คำถาม อนาคต และคำพูดอ้างอิง ก่อนสั่งงาน | ticket read-back ยัง assigned; คำสั่งยืนยันยังทำงาน |
| สร้างงานซ่อมผิด | กันปฏิเสธ/สมมติ และคำถามราคา รวม "จักบาท" ก่อนเติม slot | ไม่เกิด ticket ใหม่ในรายการ |
| เติมที่อยู่ผิด | คำทักทาย/วันเวลา/ยังไม่พร้อมบอก ไม่เป็นที่อยู่; ยกเลิกเข้ากระบวนการยืนยัน | รักษา pending แล้วรับที่อยู่จริงได้ |
| เวลาไทย | "บ่ายสอง", "บ่ายสองครึ่ง", เลขไทย และจำนวนคำติดหน่วยเวลา | อ่านกลับเวลาที่ถูกต้อง; ปฏิเสธบ่ายเก้า |
| จำนวน 0 | ไม่แปลง explicit zero เป็น 1; ปฏิเสธ add/decrement ที่ไม่เป็นบวก | มูลค่าดีลคงเดิม |
| อาการเสีย/ภาษาพูด | เพิ่ม "ไม่เยน", "ปั่นไม่ไป", "ละคับ" | positive fault/check-in cases ยังผ่าน |
| ยกเลิก slot | normalize "never mind" ให้ตรงกัน | ไม่สร้างลูกค้าจากคำยกเลิก |
| ตอบผิดลำดับ slot | เก็บชื่อเดิมเมื่อให้ที่อยู่/โน้ตก่อนเบอร์; ไม่รวมคนละชื่อ/คนละ action | regression pending-customer |

แก้ test isolation เพิ่มด้วย: 3 unit tests เดิมตกถึงโมเดลโดยไม่ได้ส่ง MockTransport;
ตอนนี้รับ offline fixture. Test ที่เรียก bootstrap คืนค่า settings หลังจบ และ
pytest ราย scenario ไม่เหมารวม exit code ของทั้ง batch ว่าแต่ละ scenario ล้มทั้งหมด

## Dashboard: ตรวจอะไรได้จริง

ทำ route inventory ครบ 38 `page.tsx` ใน `dashboard-route-inventory.json`. อ่าน shared
navigation/theme/session และหน้า Sales, Customer, Technician, Approvals, Admin ที่ใช้
ประกอบ findings ด้านล่าง. Route inventory ไม่ใช่หลักฐานว่าทุก control ถูกคลิกหรือทุก
หน้าสวยแล้ว: browser เปิด localhost ไม่ได้ (`ERR_BLOCKED_BY_CLIENT`) และไม่มี session
DEV ที่ล็อกอิน จึงไม่มี screenshots หรือผล visual/accessibility runtime ในรอบนี้

โค้ดมีพื้นฐานด้านความสม่ำเสมอดี: ธีมแยก OA, typography ไทย, sidebar แบ่งกลุ่มงาน,
active navigation, mobile drawer พร้อม Escape/focus trap, focus-visible และ reduced
motion. ต้องดูหน้าจอจริงเพื่อยืนยัน spacing, contrast, ความแน่น และขนาดเป้าสัมผัส

| หน้าหรือกลุ่ม | สิ่งที่พบจากโค้ด | สถานะ / acceptance ที่เหลือ |
|---|---|---|
| Sales/CS home | ทางลัดหยิบเฉพาะกลุ่มขาย ทำให้ CS ไม่เห็นงานบริการหลัก | แก้ให้เลือกตาม permissions; test sales/CS/admin/owner ผ่าน |
| Sales pipeline | loading / error / ไม่มีดีล ดูเป็นช่องว่างเหมือนกัน | เพิ่มข้อความ TH/EN และ retry; ซ่อนเมื่อ API 403 หรือไม่มี session |
| Approval queue | ขึ้น "ไม่มีรายการ" ก่อนโหลดหรือเมื่อโหลดครั้งแรกพัง | แสดง empty เฉพาะโหลดสำเร็จ; ต้องตรวจ slow/403/500 บน DEV |
| Technician home | service-reports HTTP error ถูกแปลงเป็นไม่มีรายงาน | เพิ่ม failed-state แยกจาก empty; งานช่างหลักยังแสดงได้เมื่อ report HTTP error |
| Customer home | 4 requests รวม Promise.all; failure เดียวทำให้ไม่อัปเดตทุกส่วน | backlog P2: แยก error/retry รายส่วน และตรวจหน้าเล็กที่มีหลายฟอร์ม |
| Admin tenants | KPI คำนวณจากรายการหลัง filter แต่ label สื่อร้านทั้งหมด | เพิ่มคำอธิบายว่าตัวเลขตามตัวกรองเมื่อกำลังกรอง |
| Customers/deals/quotes/products | มี list/detail/form routes และ shared UI | ตรวจข้อมูลยาว, 0/1/50/500 rows, search/filter, keyboard, sort/pagination และผลบันทึกจริง |
| Tickets/teams/warranties/reports | มีช่องทางทำงานและ routes; source parity ไม่มี gap ใหม่ | ตรวจสถานะ/สิทธิ์แต่ละ role, assignment, team lead/member, report required fields |
| Chats | ใช้ layout/composer เฉพาะ พร้อม CSS สำหรับหน้าจอแคบ | ตรวจข้อความยาว, unread, รูป, offline/retry, สลับร้าน, scroll และเปิด keyboard มือถือ |
| AI reports/templates | มีหน้าเฉพาะสำหรับรายงาน AI และเอกสาร | ตรวจ reasoning model/timeout และ PDF จริงแยกจาก intent suite นี้ |
| Members/roles/company/approval settings | shared session/shell และ permission-aware navigation | ตรวจเจ้าของ/CS/คนไม่มีสิทธิ์, save failure และ destructive confirmation |
| Customer/Technician ticket lists, reports, signatures | มี route ครบและเมนู OA | ตรวจ deep links ใน LINE, photo permission/rotation, signature touch+keyboard และมือถือ |
| Admin login/audit/PDPA/tenant detail | มี filter, table overflow, responsive CSS | ตรวจ session expired/locked, empty/error และ PDPA บนข้อมูลสังเคราะห์เท่านั้น |
| Guides/root | อยู่ใน inventory | ตรวจลิงก์, ภาษา TH/EN และ back navigation |

ไม่ให้คะแนนความสวยจาก source. ก่อน release ให้ screenshot ที่ 390px และ 1440px
ครบหน้าใน inventory (เพิ่ม 320px และ zoom 200% สำหรับหน้าที่แน่น) โดยใช้ fixture เดิม
และบันทึก role/OA/tenant/state. แยก "ตรวจแล้ว", "พบปัญหา", "ยังไม่ได้ตรวจ" ให้ชัด

## ข้อจำกัด / งานก่อน deploy

1. ใช้ `MODEL_EVALUATION.md` รัน configured real model ซ้ำ 3 รอบ; ตรวจ expected/actual,
   unintended mutation, missing slots, latency และผลที่แกว่ง ไม่ใช่ดูเฉพาะข้อความตอบ
2. รัน DB backend บน PostgreSQL สำหรับ test เท่านั้น และทำ DEV end-to-end read-back
3. ตรวจ UI ตาม inventory ด้วย session จริงและข้อมูลสังเคราะห์; ทำ backlog ด้านบน
4. Apply patch บน base ที่ถูกต้องผ่าน review branch, run gates และให้ release owner
   ใช้ deployment flow ที่มี gate ของ repo. Scope runtime: Application + Presentation
5. `/health` ของ runtime ต้องตรง full pushed SHA และ `/api/ready` ต้องผ่าน พร้อม business
   evidence. ยังไม่ผ่านขั้นตอนเหล่านี้ = ยังไม่เสร็จ release
