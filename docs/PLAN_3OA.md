# แผนงาน 3 OA — เรียงใหม่จาก Master Spec (3 ก.ย. 2569)

เจ้าของสั่ง (3 ก.ย. เย็น): "ลองดู Phase ใน master spec ที่เกี่ยวข้องกับงานที่ทำอยู่
ทั้งหมด แล้วเรียง Phase ใหม่ เอางานที่เกี่ยวกับ 3 OA นี้มาทำทั้งหมด" และ "นำ Phase 16
มาร่วมดูและทำด้วย" · ไม่ต้องทำใบรับประกัน PDF · **Service Report PDF ต้องมี** (แก้ 3 ก.ย. ค่ำ)

หลักการเรียง: **ทางเดินของงานซ่อมก่อน** (ลูกค้าแจ้ง → CS มอบหมาย → ช่างรับ/ปฏิเสธ →
เช็คอิน → ปิดงาน+รายงาน → CS อนุมัติ → ลูกค้าประเมิน) เพราะทุกอย่างในระบบพิสูจน์ได้
ก็ต่อเมื่อเดินสายนี้ได้จริงกับ OA จริง จากนั้นค่อยเป็นสิ่งที่ทำให้ 3 OA "ครบ" ตาม
spec (ทีม, ภาษา, ค้นสินค้า, คุยกับร้าน) แล้วปิดด้วย polish

สถานะใช้คำของ playbook: PROVEN (เดินกับ OA จริงแล้ว) · CODE_COMPLETE (มีโค้ด
+ เทสต์ + deploy แล้ว รอเดินจริง) · PARTIAL · NOT_STARTED · DEFERRED

## A. ทางเดินงานซ่อม (Phase 12 → 13 → 14) — ทำแล้ว รอเดินจริง

| ขั้น | Phase | สถานะ | หมายเหตุ |
|---|---|---|---|
| ลูกค้า add CS OA → ต้อนรับ → ผูกร้าน (รหัส/ชื่อร้าน/S/N) | 6.5, 16.1 | CODE_COMPLETE (`cdd2c1a`, `4b53f60`) | บัญชีหลายร้านเลือกได้ทั้งแชทและ home |
| ร้านบันทึกเครื่องที่ขาย → ลูกค้าผูกด้วย S/N | 7.5 | CODE_COMPLETE (`4b53f60`) | ใบรับประกัน PDF: ไม่ทำ (เจ้าของ) · แจ้งเตือนใกล้หมดอายุ: DEFERRED |
| ลูกค้าแจ้งซ่อม (ต้องมีเครื่อง) → ที่อยู่ → นัด → CS ได้ LINE | 12.4 | CODE_COMPLETE | แชท + home; guard ประโยคแจ้งซ่อมซ้อน (`tech-flow-v1`) |
| CS มอบหมายให้ช่าง/ทีม ผ่าน Dispatch Gate | 12.5 | CODE_COMPLETE (แชท + หน้า tickets ของ Sale: มอบหมาย/แก้/ยกเลิก, `dispatch-ui-v1`) | KNOWN_GAPS ของ ticket ใน check-parity ปลดแล้ว |
| ช่างรับ (public / มอบหมายตรง) หรือปฏิเสธ → CS รู้ | 12.4 | CODE_COMPLETE (`tech-flow-v1`) | ไม่ auto-reassign ตาม spec |
| ทีม: หัวหน้ารับแทนทีม → เปิดในทีม → สมาชิกรับ | 12.4 | CODE_COMPLETE (`team-flow-v1`) — ไม่ต้อง migration: ทีมรับ = accepted บน target ทีม | ทีมไม่มีหัวหน้า → สมาชิกคนไหนก็รับให้ทีมได้ |
| เช็คอิน (GPS/รูป) → ปิดงาน + รายงาน 3 ข้อ (gate) | 13.4 | CODE_COMPLETE — รับงาน = assigned, ปิดงานต้องเช็คอินก่อน (`tech-flow-v1`); รูปหน้างานส่งในแชท/ปุ่มบน home → GCS → `ticket_photos` (`photos-v1`) | GPS ยังไม่ส่งจากแชท (LINE ไม่ให้พิกัดกับรูป) |
| Service Report PDF (SmartBrowz, ลายเซ็นผู้อนุมัติ, รูปหน้างาน) | 13.4, 13.5 | CODE_COMPLETE (`report-pdf-v1` + `photos-v1`) — วาดลายเซ็นที่ หน้าจอ > ข้อมูลของฉัน > ลายเซ็น; รูปหน้างาน 4 รูปแรกลง PDF | — |
| CS อนุมัติ (ขั้นตอนตั้งได้) → ลูกค้าประเมิน 1–3 | 14 | CODE_COMPLETE (`1a3f515`, `34e464c`) | **รอ runtime acceptance §14.7 กับ OA จริง** |

## B. งานที่เหลือของ 3 OA — เรียงตามลำดับที่จะทำ

1. ~~Sale dashboard: มอบหมาย/แก้/ยกเลิก ticket จากหน้าจอ~~ — **ทำแล้ว** (`dispatch-ui-v1`,
   3 ก.ย. ค่ำ): มอบหมายให้ช่าง/ทีมผ่าน gate เดิม + แจ้งช่างทาง LINE, แก้ชื่อ/เบอร์/ที่อยู่/นัด/S/N,
   ยกเลิกพร้อมแจ้งช่าง
2. ~~ทีมช่างตาม 12.4 เต็มรูป~~ — **ทำแล้ว** (`team-flow-v1`): หัวหน้ารับให้ทีม (ทีมไม่มีหัวหน้า → สมาชิกรับให้
   ทีมได้) → สมาชิกเห็น "งานของทีม" แล้วรับต่อ · หัวหน้าปฏิเสธแทนทีมได้ → CS · ไม่ต้อง migration
3. ~~รูปหลักฐาน + ลายเซ็น (Phase 13.1, 13.5)~~ — **ทำแล้ว** (`photos-v1`): ส่งรูปใน LINE (ช่าง → งานที่
   กำลังทำ, ลูกค้า → งานที่เปิดอยู่) หรือปุ่ม "ถ่าย/แนบรูป" บน home ช่าง → GCS → `ticket_photos`;
   หน้ารายงานโชว์รูป; PDF แปะรูป 4 รูปแรก · ลายเซ็น: วาดบนหน้า `/liff/{oa}/signature` → identity → PDF
4. ~~Phase 16 ให้ครบ~~ — **ทำแล้ว** (`phase16-v1`): `auto_accept_new_customers` มีผลจริง (ลูกค้าผูกร้าน
   → เข้ารายชื่อทันที หรือแจ้ง CS; ตั้งได้ในแชท "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด" และหน้าข้อมูลบริษัท) ·
   รูปแบบวันที่/เขตเวลาต่อผู้ใช้ มีผลกับทุกวันที่ที่ระบบพิมพ์ (แชท "รูปแบบวันที่ …", "เขตเวลา …" + การ์ดโปรไฟล์)
5. ~~Customer OA ตาม spec หน้า 1–2~~ — **ทำแล้ว** (`customer-home-v1`, 3 ก.ย. ดึก): ค้นหาสินค้า/
   สินค้าทั้งหมดบน home และในแชท ("สินค้าทั้งหมด" บน CS OA = storefront ทุกร้าน ไม่ใช่รายการสินค้า
   ของร้าน), กด "สนใจ"/พิมพ์เลขข้อ → lead ในร้านนั้น + เจ้าของ/แอดมิน/เซลได้ LINE, ประวัติการซื้อ
   (ดีลของลูกค้าคนนี้ในร้านนี้) ทั้ง home และแชท "ประวัติการซื้อ" · ใบรับประกัน/สลับภาษา มีอยู่แล้ว ·
   "คุยกับร้าน" = ข้อ 6
6. ~~Phase 15 Live Chat "คุยกับร้าน" + SLA~~ — **ทำแล้ว** (`live-chat-v1`, migration
   `0022_chat_sessions`): "คุยกับร้าน" ในแชท/บน home → session เดียวต่อ (ร้าน, ลูกค้า) → เจ้าของ/
   แอดมิน/เซล/CS ได้ LINE → ตอบที่ หน้าจอ > แชทลูกค้า → push ไป CS OA → คนแรกที่ตอบเป็นเจ้าของ ·
   ระหว่าง session ข้อความอิสระ = บรรทัดในแชท ไม่เปิดงานซ่อม (คำสั่ง/ค้นหาสินค้ายังใช้ได้) · SLA
   (`chat_sla_minutes` ค่าเริ่มต้น 30) เลย → เตือนเจ้าของ/ทุกคน ครั้งเดียว · ไม่มีข้อความ
   (`chat_timeout_minutes` ค่าเริ่มต้น 120) → ปิดเอง + บอกลูกค้า · sweep: ทุกครั้งที่เปิดหน้าแชท,
   `/platform/reminders/sweep`, และ `/platform/chat/sweep` สำหรับ Scheduler ทุก 5 นาที (ยังไม่มี job)
7. ~~Rich menu หน้าที่ 2 ต่อ OA (Phase 19)~~ — **ทำแล้ว** (`richmenu-pages-v1`): `generate.py` ออก
   2 หน้าต่อ OA (หน้าหลัก = 6 ช่องเดิม, เพิ่มเติม = แชท/สินค้า/ประวัติ/โปรไฟล์/สิทธิ์/สลับภาษา) แท็บบน
   หัวเมนูสลับด้วย rich menu alias; `richmenu-apply.sh` สร้าง 2 เมนู + alias + ตั้งหน้าหลักเป็น default,
   uri `{LIFF_X}/path` แทนค่าได้ · แชท "สลับภาษา" สลับ TH/EN · **เจ้าของรัน `bash ~/rm.sh`** ให้เมนูขึ้นจริง
8. **Phase 20 polish** — **i18n ทำแล้ว** (`phase20-i18n-v1`): LINE push อ่านภาษาของ*ผู้รับ*
   (display preference) ไม่ใช่ของผู้ส่ง; ทุก notification ของ 3 OA flow มี EN (แจ้งซ่อมใหม่/มอบหมาย/
   ทีมรับงาน/รออนุมัติ/อนุมัติ-ตีกลับ/แชท/SLA/ลูกค้าใหม่/lead); push ถึงลูกค้าใน live chat ตามภาษาลูกค้า;
   หน้า LIFF ไม่มีไทย hardcode (เทสต์กัน) · ข้อความบอทในแชท 347 ชุดมี TH/EN ครบอยู่แล้ว ·
   **ยังไม่ทำ:** ticket_changed (ข้อความมาจากผู้เรียก ภาษาเดียว), reminder sweep (รวมหลายร้าน ไทย),
   performance p95 / cache hit (20.4), accessibility pass (20.5 test_accessibility), evidence ปิด phase

## C. รายการแก้จากการทดสอบของเจ้าของ (4 ก.ย.)

1. ~~(C1) บั๊ก/logic~~ — **ทำแล้ว** (`fixes-3oa-v1`): "รายการสินค้า" โชว์ชื่อสินค้า · ข้อความในแชทกับร้าน
   ไม่ตอบ "ส่งถึงร้านแล้ว" ทุกครั้ง (เงียบ = สำเร็จ, ผิดพลาดจึงบอก) · ปิดแชทเองเมื่อเงียบ 1 ชม.
   (ค่าเริ่มต้น `chat_timeout_minutes` 60) และ sweep ทำงานทุกข้อความบน CS/Sale OA · ลูกค้ากลับมา
   "คุยกับร้าน" = แชทเดิมต่อ (ประวัติอยู่ครบ ตาม LINE ID) · นัดหมายไม่มีสถานะ: แชทบอกเฉพาะที่ยังไม่ถึง
   record เก่าเก็บไว้ · เช็คอินจาก UI บอกเหตุผลจริง (เช็คอินไว้แล้ว / ไม่ใช่งานของคุณ / งานปิดแล้ว)
2. ~~(C2) UI~~ — **ทำแล้ว** (`ui-polish-v1`): หน้าแชทลูกค้าเป็น inbox แบบ live-chat console (รายการซ้าย |
   บทสนทนาขวา, บนมือถือเป็น 2 จอ, bubble ลูกค้า/ร้าน, แถบวัน, composer ติดล่าง, Enter ส่ง) · "วิธีใช้"
   อยู่บนแถบหัวทุกหน้าทุก OA ตำแหน่งเดียวกัน · ตัวอักษรไม่เคลื่อน: ฟอนต์ `display=optional` + แถบสถานะจองที่ไว้
   + รายการ/บทสนทนา re-render เฉพาะที่เปลี่ยน + เลื่อนลงล่างเฉพาะเมื่อผู้อ่านอยู่ล่างสุด · แถบส่วน (section strip)
   เพิ่ม แชทลูกค้า และ รออนุมัติ; เมนูหลักมีครบทุกหน้า (ตรวจแล้ว)
3. ~~(C3) import CSV + คู่มือเป็นไฟล์~~ — **ทำแล้ว** (`csv-import-v1`): หน้า รายการสินค้า และ ทะเบียนสินค้า
   มีส่วน "นำเข้าจากไฟล์ CSV" (ไฟล์ตัวอย่างดาวน์โหลดได้ที่ `/samples/products.csv`, `/samples/warranties.csv`;
   หัวคอลัมน์ไทย/อังกฤษ; ผลรายแถว; S/N ซ้ำถูกข้ามพร้อมบอกแถว; ≤500 แถว) · หน้า วิธีใช้ ทุก OA มีปุ่ม
   ดาวน์โหลดคู่มือเป็น .html / .md (ช่องรูป + prompt ทุกขั้น) · `GET /api/v1/liff/{oa}/guide?format=md|html`

4. ~~(C4) กติกาแชทและเวลา~~ — **ทำแล้ว** (`chat-sla-v1`): ร้านต้องตอบใน 15 นาที (ค่าเริ่มต้น) → เกินแล้วแจ้ง
   ลูกค้า "จะติดต่อกลับ" และพักการสนทนา (`unanswered`) · ร้านตอบทีหลังจากหน้าแชทลูกค้าได้ → ลูกค้าได้ LINE
   พร้อมปุ่ม "คุยกับร้าน" → เปิดแล้วเห็นข้อความที่ร้านตอบ แล้วคุยต่อปกติ · ลูกค้าเงียบ 60 นาทีปิด+แจ้ง ·
   ทั้งสองค่าตั้งเองได้: แชท `ตั้งค่าเวลาตอบแชท 15` / `ตั้งค่าปิดแชทเมื่อเงียบ 60` / `ตั้งค่าแชท` และหน้าข้อมูลบริษัท
   · เช็คอินบันทึกตำแหน่ง: หน้าจอขอ GPS (ปฏิเสธก็เช็คอินได้ บอกว่าไม่มีพิกัด), แชทส่งตำแหน่ง LINE = เช็คอินพร้อมพิกัด

5. ~~(C5) ไฟล์ใน LINE~~ — **ทำแล้ว** (`liff-files-v1`): ตัวอย่าง CSV แสดงเป็นตารางในหน้า + ปุ่มคัดลอก + เปิดใน
   เบราว์เซอร์ของเครื่อง · คู่มือเปิดในเบราว์เซอร์ของเครื่อง (`/api/guide/{oa}?format=html|md` ไม่ต้องมี session)
   แทนดาวน์โหลดแบบ blob ที่ LINE บล็อก · แถบหัวไม่ใช้ backdrop-filter (ตัวอักษรสั่นบน WebKit ใน LINE)

6. ~~(C6) งานค้าง~~ — **ทำแล้ว** (`leftovers-v1`): แจ้งเตือน "งานเปลี่ยน" (ลูกค้ายกเลิก/เลื่อนนัด, ช่างปฏิเสธ,
   ร้านยกเลิก) มี EN · เทสต์ 20.5 ที่ทำได้โดยไม่ต้องเปิดเบราว์เซอร์ (`tests/boundary/test_a11y.py`: ทุก
   input/select/textarea มี label, คู่สีข้อความ ≥4.5:1 ทั้ง 3 ธีม, สี accent ดิบไม่ใช้เป็นตัวอักษร) · เคสทดสอบ
   D-01…D-16 สำหรับ C1–C5 · handoff "Immediate next actions" เขียนใหม่ตามสภาพจริง
7. ~~(C7) Cloud Scheduler~~ — **ทำแล้ว** (`scheduler-v1`, infra): 3 job เตือน 08:00 / หมดอายุใบเสนอราคา 00:30 / sweep แชททุก 5 นาที (`scheduler.tf`)

8. ~~(C8) สรุปตอนเปิดแชทกลับมา~~ — **ทำแล้ว** (`catch-up-v1`): "ล่าสุดที่คุยกับร้านไว้:" = ข้อความล่าสุดของลูกค้า
   → ตามด้วยข้อความฝั่งร้านหลังจากนั้นจนถึงล่าสุด (ไม่ใช่เฉพาะฝั่งร้าน)

## D. งานค้างที่เหลือ (4 ก.ย. ดึก)

- เจ้าของเดินเคสทดสอบ (รวม Phase 14.7) · ใส่รูปคู่มือลง `help_images.json` ทั้งสองไฟล์
- หมุน secret LINE Sales (เจ้าของ) · Phase 16.5/17/17.5/18 · 20.4 performance · CI

## B++. เคสทดสอบ (4 ก.ย.)

`docs/TEST_CASES_3OA.md` — เคสทดสอบตาม workflow ข้าม OA (X-), ตามฟังก์ชันของแต่ละ OA
(S-/T-/C-: แชท, หน้าจอ, rich menu) และ UI/UX ตามสกิล ui-ux-pro-max (U-) พร้อมช่องกรอกผล
ใช้เดินกับ OA จริงหลัง B1–B8 ขึ้น dev ครบ; เคสที่ ❌ คืองานถัดไป

## B+. งานคู่ขนานที่ทำแล้ว (3 ก.ย. ค่ำ)

- **คู่มือวิธีใช้แหล่งเดียว** (`help-guide-v1`): `services/guides.py` → แชท "วิธีใช้", หน้า
  `/liff/{oa}/guide`, handout `docs/guides/*.md` พร้อมช่องรูป + prompt; เทสต์บังคับให้ตรงกับ
  คำสั่งจริงและ docs ไม่ค้าง · รูป: เจ้าของใส่ URL ใน `help_images.json` (2 ไฟล์)
- **ข้อความสิทธิ์อ่านง่าย**: บอกสิทธิ์ที่ต้องขอเป็นภาษาคน + ใครให้ได้ + จัดหมวด

## C. สิ่งที่ตัดออกตามเจ้าของ

- ใบรับประกัน PDF (7.5) — เจ้าของบอกไม่ต้องทำ (3 ก.ย.) · Service Report PDF **ทำ** (แก้คำสั่ง 3 ก.ย. ค่ำ)

## D. วิธีปิดแต่ละข้อ

ทุกข้อส่งเป็น patch + deploy script ตาม CLAUDE.md §-1, มีเทสต์ทั้งสองฝั่ง (แชท/UI)
ตั้งแต่ patch แรก, check-parity สะอาด, และ**ปิดได้เมื่อเดินกับ OA จริงแล้ว** — ไม่ใช่
เมื่อ deploy ขึ้น


## D1 — Phase 16.5 PDPA (4 Sep 2026)

**ขอบเขต (Master Spec 16.5):** ความยินยอมครั้งแรกก่อนลงทะเบียนทุก OA · "ขอข้อมูลของฉัน" = หน้าสรุปทุกร้าน (ลิงก์ 24 ชม. ผ่าน document store; ถ้าไม่มี bucket สรุปในแชท) · "ขอลบข้อมูล" → ยืนยัน → anonymise ทุก tenant (ลูกค้า/ใบงาน/รูป/แชท/identity ลายเซ็น) + ลบไฟล์ GCS · ทุก tenant ที่แตะได้ audit row `pdpa_erasure`/`pdpa_export` cross_tenant=true · Platform admin: `GET/POST /platform/pdpa/requests`, `/process`, `/reject`.

| ชั้น | สิ่งที่เพิ่ม |
|---|---|
| Data | migration `0023_pdpa` (identity consent_*/anonymized_at, ตาราง `data_subject_requests`, audit actions ใหม่) · `repositories/phase165.py` · routes `/identities/{uid}/consent`, `/platform/pdpa/requests…` (ล้าง cache identity) |
| Application | `services/pdpa.py` (consent gate + export HTML + erase) · gate ใน `handle_registration` (พัก message ที่พิมพ์ไว้, ยอมรับแล้วทำต่อ) · chat phrases ทุก OA ก่อน help hook · LIFF `/liff/{aud}/consent`, `/pdpa/{export|erase}` · `DocumentStore.delete` |
| Presentation | proxy `/api/liff/[audience]/consent`, `/pdpa/[action]` · ProfileCard แถว "ความยินยอม" + "ข้อมูลของฉัน" (เปิดสำเนาแบบ external, ยืนยันก่อนลบ) |
| Guides | ขั้น "ข้อมูลส่วนตัว (PDPA)" ใน customer + slot `customer-pdpa` |
| Tests | `tests/unit/test_pdpa.py` (gate/export/erase/chat) · `tests/integration/test_pdpa_data.py` (16.5.6 ทั้ง 6 ข้อ) |

**ค้าง (Phase 18):** หน้า admin สำหรับคิวคำขอ PDPA อยู่ใน Platform Admin Dashboard.


## D2 — Phase 18 Platform Admin Dashboard (4 Sep 2026)

**ขอบเขต (Master Spec 18):** เว็บแยก `/admin` (ไม่ใช่ LIFF) login ด้วย username/password · รายชื่อ tenant ทั้งหมด ค้นหา/กรอง พร้อมขนาดการใช้งาน (สมาชิก ลูกค้า งาน/ค้าง ดีล ใช้งานล่าสุด) · หน้าร้าน: สมาชิก ระงับ/เปิดใช้งาน break-glass โอนสิทธิ์เจ้าของ (ยืนยัน + audit cross_tenant + แจ้งเจ้าของใหม่ทาง LINE) · Audit ข้ามร้าน กรองตามร้าน/ผู้กระทำ/การกระทำ · คิวคำขอ PDPA (Phase 16.5) ดำเนินการ/ปฏิเสธ/สร้างแทน.

| ชั้น | สิ่งที่เพิ่ม |
|---|---|
| Data | `repositories/phase18.py` (tenants + counts + owner, tenant detail) · `AuditRepository.list_platform` · routes `GET /platform/tenants`, `/platform/tenants/{id}`, `/platform/audit` · `MembershipOut.license_status` |
| Application | `routers_admin`: `GET/PATCH /platform/tenants…`, `GET /platform/audit`, `POST /platform/break-glass/transfer-owner` (require_admin + break_glass permission) · แชท: ร้านที่ถูกระงับตอบ "ระงับ" แทนทำรายการ (สิทธิ์ PDPA ยังใช้ได้) · `/liff/{aud}/me` ส่ง `license_status` |
| Presentation | `app/admin/*` (layout + rail nav, tenants, tenant detail + actions, audit, pdpa, login) · `admin.css` (dark operations palette, Fira Sans/Code, ไม่ใช้ backdrop-filter) · proxies `/api/admin/{logout,tenants/[id]/status,break-glass,pdpa/[id]/[action]}` |
| Tests | `tests/unit/test_platform_admin.py` (18.5 ทุกข้อ + suspended chat) · `tests/integration/test_platform_admin_data.py` |

**ค้าง:** หน้าจอ LIFF ยังไม่โชว์ป้าย "ร้านถูกระงับ" (มี `license_status` ใน `/me` แล้ว) · usage/billing เชิงเงิน (Phase 20).


## D3 — Phase 17 Ad-hoc AI Report Engine (4 Sep 2026)

**ขอบเขต (Master Spec 17):** Sale/CS ถามรายงานเป็นภาษาคนในแชท Sale OA หรือหน้า "รายงาน AI" · AI (reasoning model) แปลงเป็น query spec JSON เท่านั้น · โค้ดตรวจกับ whitelist (entity/field/metric/group_by/date_range) ทั้งฝั่ง Application และ Data · Data tier สร้าง SQLAlchemy statement เดียว parameterized + filter license_id เสมอ · ผลลัพธ์ 3 แบบ: ข้อความสรุป, ตาราง+กราฟแท่ง (หน้าจอ/หน้าเว็บ), ไฟล์ CSV (+PDF เมื่อ renderer พร้อม) ผ่าน document store ลิงก์ 7 วัน · สิทธิ์ `view_reports` (member/sales มี, cs ไม่มี default).

| ชั้น | สิ่งที่เพิ่ม |
|---|---|
| Data | `repositories/phase17.py` (whitelist, `validate_spec`, `ReportQueryRepository.build_statement/run`, Bangkok date windows) · `POST /licenses/{id}/reports/query` |
| Application | `services/reports_ai.py` (prompt จาก whitelist เดียวกัน, `generate_query_spec`, `validate_query_spec`, text/CSV/HTML, `publish_files`) · chat: `AI_REPORT_TRIGGERS` + `_handle_ai_report` (หลังคำสั่งรายงานเดิม, Sale OA) · `POST /licenses/{id}/reports/ai` และ `/reports/ai/run` (require view_reports) |
| Presentation | `/liff/sales/reports/ai` (AiReports: ช่องถาม, ตัวอย่าง, ตาราง+แท่ง, ปุ่มไฟล์ผ่าน openExternal) · เมนู "รายงาน AI" · CSS `.report-*` |
| Guides | ขั้น "ถามรายงานด้วย AI" ใน sales + slot `sales-ai-report` |
| Tests | `tests/unit/test_ai_reports.py` (17.5 ครบ: spec, injection, whitelist, output, permission) · `tests/integration/test_ai_reports_data.py` |

**ค้าง:** ตัวเลขเชิงเงิน (sum ยอดขาย) ยังไม่มีเพราะ Deal/Quote ไม่มี amount ใน schema — ขยาย `NUMERIC_FIELDS` เมื่อมี · Excel แท้ (.xlsx) ใช้ CSV แทน (Excel เปิดได้) · PDF ต้อง SmartBrowz พร้อม.

## D4 — "ทำอะไรได้บ้าง" ตอบด้วยคู่มือ (4 Sep 2026)

**คำสั่งเจ้าของ:** คำตอบแบบ "คุณสามารถทำสิ่งเหล่านี้ได้…" (รายการสิทธิ์/คำสั่ง) อ่านแล้วไม่เข้าใจ ให้ยกเลิกทั้งหมด และตอบด้วยคู่มือการใช้งานแทน.

- แชททุกแบบที่ถามว่าใช้ยังไง/ทำอะไรได้บ้าง/ตัวอย่างคำสั่ง/สิทธิ์ของฉัน → `render_help_text` (คู่มือขั้นตอน + รูป + ปุ่มเปิดคู่มือ) ไม่ต่อท้ายรายการคำสั่งอีก; คนไม่มีสิทธิ์เลยได้บรรทัด "ติดต่อเจ้าของบริษัท" นำหน้าคู่มือ.
- `suggest_what_you_can_do` (ใช้ตอนปฏิเสธ/ไม่เข้าใจ) เหลือแค่เหตุผล (ไม่มีฟังก์ชันนี้ / ยังไม่มีสิทธิ์ «…» ขอจากเจ้าของร้าน / ยังไม่แน่ใจ ลองพิมพ์ให้ชัด) + `GUIDE_POINTER` + ปุ่มเปิดคู่มือ; ไม่มีรายการสิทธิ์อีก.
- ข้อความที่เคยชี้ไป "พิมพ์ ทำอะไรได้บ้าง เพื่อดูสิ่งที่ทำได้" เปลี่ยนเป็น "พิมพ์ วิธีใช้ เพื่อเปิดคู่มือ".
- เทสต์ 6.9 และ TestUsageHelp เขียนใหม่ตามพฤติกรรมนี้.


## E1 — User review fixes (4 Sep 2026)

| ข้อ | สาเหตุจริง | แก้ |
|---|---|---|
| 1 ระบบทำอะไรได้บ้าง | ตอบได้แค่คู่มือรวม ไม่มีคำตอบเชิงลึกต่อหมวด/ต่อสิทธิ์ | `capability_detail/permission_summary` ใน chat.py: อ่านจาก permission catalogue + `HELP_SECTIONS` ที่คนนั้นถือสิทธิ์ ตอบ "ทำอะไรกับ Lead/ดีล/… ได้บ้าง", "ฉันมีสิทธิ์ทำอะไร"; คู่มือรวมต่อท้ายบรรทัดหมวดที่ใช้ได้ + ปุ่มเปิดหน้าจอจริง (`dashboard_link`) |
| 2 ลูกค้าซ้ำ | 409 duplicate ถูกจับเป็น "กรุณาระบุ…" วนซ้ำ; Data tier เช็คแค่เบอร์ | Data: เช็คอีเมล (normalise) + `field` ใน error; chat: `customer_duplicate` pending → ใช้เดิม / อัปเดต (เติมช่องว่าง, ข้อมูลขัดกันถามก่อนแทนที่) / ยกเลิก |
| 3 ลบ Lead | ไม่มีทางลบจากแชท/หน้าจอ | ใช้ soft delete เดิม (`archived_at`, สิทธิ์ `customer.archive`): แชท "ลบ Lead สมชาย"/"ลบ Lead นี้" + ยืนยัน; ปุ่มบนหน้ารายชื่อ; route `POST customers/{id}/archive`; ลบอัตโนมัติ `lead_auto_archive_days` (ปิดเป็นค่าเริ่มต้น) ตั้งได้ในแชท/หน้าข้อมูลบริษัท รัน sweep เช้าใน platform reminders |
| 4 สร้างดีลผิดคน/ตัวเลข | ไม่มีชื่อจาก AI → ใช้ "ลูกค้าล่าสุด" เงียบ ๆ; Deal ไม่มี amount; วันที่ "อาทิตย์" ถูกอ่านเป็นวันอาทิตย์ | `services/deal_fields.py` อ่านมูลค่า/วันปิดจากข้อความ (ตรวจค่า AI); Deal.amount/currency (migration 0024); fallback ลูกค้าล่าสุดต้องยืนยัน; `parse_thai_date` ไม่อ่านชื่อคนเป็นวัน และ "วันที่ 15 ต.ค." ถูกต้อง; ถามเฉพาะช่องที่กำกวม |


## F1 — User review batch 2 (4 Sep 2026)

- **ป้ายร้านถูกระงับบน LIFF:** `SuspendedNotice` บนหน้าแรกทั้ง 3 OA (`AppShell notice` + `SalesSuspended`) จาก `license_status` ใน `/liff/{aud}/me`.
- **มูลค่าดีล:** รายการดีลโชว์ `amount` (ถ้ามี ใช้แทนผลรวมรายการสินค้า); รายงาน AI นับ `sum/avg amount` ของ deals ได้ (whitelist ทั้ง 2 ชั้น + prompt hint "ยอดขายรวม").
- **เบอร์โทรต้องเป็นตัวเลข:** กฎเดียว `services/phone.py` ใช้ในแชท (สร้าง/แก้ไข ถามเบอร์ใหม่พร้อมเหตุผล), CSV import, `CustomerWriteIn` (app) และ `CustomerIn` (data) → 422; ฟอร์มบนหน้าจอเช็คก่อนส่ง.
- **เพิ่มลูกค้าหลายราย:** แชท "เพิ่มลูกค้าหลายคน" + หนึ่งคนต่อบรรทัด (สร้าง/ข้ามซ้ำพร้อมรหัสเดิม/ไม่สำเร็จพร้อมเหตุผล) และ `POST customers/import` + ปุ่ม "นำเข้า CSV" บนหน้ารายชื่อลูกค้า (ไฟล์ตัวอย่าง `samples/customers.csv`).
- **รูปประกอบคู่มือ:** 21 รูป (แชท/แดชบอร์ดจำลองตามสีแต่ละ OA) สร้างด้วย `scripts/dev/render-guide-images.py` (ฟอนต์ใน `scripts/dev/guide-fonts/`) → `chann_app/static/help/*.png` เสิร์ฟที่ `GET /api/v1/guide/images/{slot}.png`; แชทส่งเป็น URL เต็มผ่าน `PUBLIC_BASE_URL`, หน้าคู่มือ proxy ผ่าน `/api/guide-image/[slot]`.
- **เก็บกวาด:** ลบ `usage_help()`/`HELP_INTRO`/`HELP_OUTRO` (ไม่มีผู้เรียกตั้งแต่ D4).
