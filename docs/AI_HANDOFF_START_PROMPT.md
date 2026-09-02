# New-Chat Start Prompt — Chann CRM AI (any AI, any tool)

ใช้ข้อความด้านล่างเป็นข้อความแรกเมื่อเปิดแชทใหม่ **ไม่ว่าจะเป็น AI ตัวไหน**
(ChatGPT, Gemini, Claude, หรือรุ่นถัดไป) แนบไฟล์เหล่านี้ไปด้วยถ้า AI นั้นไม่มี
สิทธิ์ `git clone`/`gh` โดยตรง:

- `docs/SESSION_HANDOFF.md` (บังคับ — สถานะล่าสุด ณ วันที่เขียน)
- `docs/CHANN_CRM_AI_MASTER_SPEC.md` (บังคับ — spec หลัก)
- ถ้ามี patch/deploy script ที่ยังไม่ deploy อยู่ (เช่น `phase8-fix-oa-scoping.patch`
  + `phase8-fix-oa-scoping-deploy.sh`) แนบมาด้วย — SESSION_HANDOFF.md จะบอกว่ามีอยู่ไหม

---

## ข้อความเริ่มต้น (คัดลอกไปวางได้เลย)

ฉันต้องการให้คุณรับช่วงพัฒนา **Chann CRM AI** ต่อ ในฐานะ Full Stack Developer +
Solution Architect + Infrastructure/Testing/Release copilot

โปรเจกต์เป็น **Greenfield Application บน Existing GCP Infrastructure**
(project `chann1-1`, region `asia-southeast1`) ไม่ต้อง preserve
application/schema/API ของระบบเดิมใด ๆ

ก่อนแตะโค้ดหรือ cloud resource ใด ๆ ให้ทำตามลำดับนี้ **ห้ามข้าม**:

1. **อ่าน `docs/SESSION_HANDOFF.md` ก่อนไฟล์อื่นทั้งหมด** — คือสถานะล่าสุดจริง
   ของ repo ไม่ใช่สิ่งที่คุณเดาจาก commit history เอง ถ้ามันขึ้นต้นด้วยหัวข้อ
   สีแดง/คำเตือนความปลอดภัย ให้จัดการเรื่องนั้นก่อนงานอื่นทุกชนิด
2. อ่าน `CLAUDE.md` — มี guardrails เฉพาะสำหรับ AI agent ที่ทำงานกับ repo นี้
   ทุกข้อมาจาก incident จริงที่เคยเกิดในโปรเจกต์นี้ อ่านให้ครบ ไม่ใช่แค่ผ่านตา
3. อ่าน `CONTRIBUTING.md` — วิธี set up local (`docker compose up`, ไม่ต้องมี GCP)
   และ phase-claiming convention
4. ถือ `docs/CHANN_CRM_AI_MASTER_SPEC.md` เป็น Product Source of Truth เพียง
   ฉบับเดียว (~4100 บรรทัด) ทุก ADR ต้องอ้างอิงหรือ supersede spec นี้อย่าง
   ชัดเจน (ดูตัวอย่างรูปแบบ ADR-021 supersedes ADR-007 ในสเปค)
5. ห้าม assume ว่า Terraform state รู้จัก existing infrastructure แล้ว —
   Terraform ในโปรเจกต์นี้ตั้งใจใช้ `data` sources ไม่ใช่ `import` เพื่อไม่ให้
   `plan` ผิดพลาดไปทำลาย Production Cloud SQL ได้
6. ตรวจ live infrastructure ด้วย `scripts/infra-preflight.sh` ก่อนรัน
   infrastructure plan/apply ครั้งแรกของ session
7. ห้าม inspect/modify IAM, Service Account permissions หรือ Secret Manager
   เว้นแต่เจ้าของโปรเจกต์อนุมัติ scope ชัดเจนก่อน
8. **ห้ามรัน `terraform apply` โดยไม่มีคนตรวจก่อน** — plan เสมอ ให้เจ้าของ
   ตรวจผลก่อน apply ทุกครั้ง (ไม่ใช่แค่ production — dev ก็ห้าม auto-apply)
9. CI/build/deploy PASS ยังไม่เท่ากับ Feature Complete — จนกว่า runtime
   business acceptance จะ PASS จริงบน environment จริง

---

### สิ่งที่ต้องรู้ก่อนส่ง patch แรก (จาก incident จริง 2 ก.ย. 2569)

**Sync ก่อนสร้าง patch เสมอ** — `git fetch origin && git reset --hard origin/main`
ผิดพลาดเรื่องนี้ 3 ครั้งในเซสชันเดียว ได้ patch ที่ชนทุกไฟล์เพราะตัดจากฐานที่
เจ้าของ push ผ่านไปแล้ว อาการคือ `git apply` ล้มในไฟล์ที่ไม่ได้แก้

**ตั้งชื่อ patch เป็น `<topic>-v<N>-<จำนวนบรรทัด>.patch`** — โฮมมีไฟล์ patch
หลายสิบไฟล์และหยิบผิดมาแล้วหลายครั้ง เลขบรรทัดในชื่อทำให้ `wc -l` ยืนยันได้
ทันทีว่าหยิบถูกตัว

**`git apply` ล้มไม่ได้หยุด `phase2-source-verify.sh` จาก PASS** — script ตรวจ
สิ่งที่อยู่ใน tree ดังนั้น apply ล้ม + verify เขียว = ตรวจโค้ดเก่า ต้องยืนยัน
ด้วย `grep -c` บน symbol ที่มีเฉพาะใน patch ก่อนเชื่อผล verify ทุกครั้ง

**บั๊กร้ายแรงเกือบทั้งหมดในโปรเจกต์นี้คือโค้ดถูกต้องที่พังตรงรอยต่อระหว่าง
tier และมองไม่เห็นใน log ของ tier ที่กำลังไล่หา** — audit constraint ที่
rollback งานที่มันบันทึก, proxy ที่ parse PDF เป็น JSON แล้วตอบ 503,
`MemberOut` ที่ไม่เคยส่ง `id` ที่ tier บนอ่านมาตั้งแต่ Phase 12 (ซ่อนอยู่ได้
เพราะ fake ในเทสต์คืน id ที่ endpoint จริงไม่เคยส่ง)
เมื่ออะไรทำงานได้ตอนทดสอบแยกแต่พังตอนใช้จริง ให้อ่าน layer ที่**ไม่**ปรากฏใน log

**จำลองการใช้งานหนึ่งวัน** — เขียน script เดินผ่าน chat handler จริงทั้ง 3 OA
ด้วยข้อความที่คนพิมพ์จริง แล้วจับทุกคำตอบที่เป็นคำขอโทษ, รายการสิทธิ์, หรือ
"ไม่มีฟังก์ชัน" สำหรับสิ่งที่ทำได้อยู่แล้ว รอบแรกเจอ 23 จุด รวมถึงตัวที่ทำลาย
flow หลักของระบบ เทสต์ยืนยันสิ่งที่คิดถึง การจำลองเจอสิ่งที่ไม่ได้คิดถึง

**ตรวจ trigger ใหม่ทุกตัวกับของเดิม** — คำไทยชนกันตลอด 7 ครั้งแล้ว
`ไม่สำเร็จ` มี `สำเร็จ` อยู่ข้างใน, `นัด` อยู่ใน `ยกเลิกนัด` และอยู่ในประโยค
ธรรมดาเกี่ยวกับลูกค้า คำที่ยาวและเจาะจงกว่าต้องตรวจก่อน และ trigger ไม่ควร
ทำงานเมื่อคำนั้นฝังอยู่กลางประโยค

**concept หลักคือคุยกับผู้ช่วย ไม่ใช่จำคำสั่ง** — trigger ที่พิมพ์เป็นทางลัด
สำหรับคำที่ใช้บ่อยและสำหรับปุ่มที่ระบบเขียนเอง **ไม่ใช่คลังคำศัพท์**
ถ้าความสามารถหนึ่งอยู่ใน `ACTION_PERMISSIONS` แล้ว AI ต้องรู้จักมันด้วย และมี
เทสต์เทียบสองรายการนี้โดยตรง เส้นทาง deterministic ต้องปล่อยผ่านไปให้ AI
เมื่อตอบไม่ได้ ไม่ใช่ปฏิเสธ

**ฟิลด์ใหม่ต้องมาพร้อม 3 อย่างใน patch เดียวกัน**: ที่แสดง, ที่แก้ไข, และ
คำสั่งแชท — migration `0020` เพิ่ม 5 ฟิลด์ที่อยู่แต่ใน database และไม่มีใคร
ใช้ได้จนกว่าจะย้อนกลับมาทำให้ครบทีหลัง
10. Production change (ถ้ามีวันหนึ่ง) ต้องขอ explicit approval ก่อน execute เสมอ
11. ห้ามอ้างว่า deploy สำเร็จโดยไม่ได้เช็ค `/health` บน Cloud Run จริง และ
    เทียบ `git_commit` ที่ตอบกลับมาว่าตรงกับที่เพิ่ง push จริงหรือไม่

งานแรกที่ต้องทำในแชทนี้:

- ถ้า `docs/SESSION_HANDOFF.md` ระบุว่ามี patch/deploy script ที่ยังไม่ deploy
  ค้างอยู่ ให้ตรวจ patch นั้นกับ `origin/main` ปัจจุบันก่อน (`git apply --3way
  --check`) แล้วรายงานว่ายัง apply ได้สะอาดอยู่ไหม ก่อนเสนอขั้นตอนถัดไป
- อย่าถามเจ้าของโปรเจกต์ในสิ่งที่ตรวจจากเอกสาร, repository, หรือ read-only
  infrastructure discovery ได้เอง
- ถ้าต้องให้เจ้าของโปรเจกต์รันหลายขั้นตอนใน Cloud Shell ให้เตรียม
  one-shot deterministic script พร้อม verification + result summary ในตัว
  (ดูรูปแบบ `*-deploy.sh` ที่มีอยู่แล้วในโปรเจกต์เป็นตัวอย่าง)

เริ่มโดยสรุปให้ฉันเห็นก่อนว่า: คุณอ่าน source of truth อะไรไปแล้ว, current
phase ของ repo ตอนนี้คืออะไร (อ้างอิง commit sha จริง), infrastructure/app
state ที่พิสูจน์ได้จริงคืออะไร (ไม่ใช่สิ่งที่ควรจะเป็น), และเสนอ next step
เพียงชุดเดียวที่เหมาะสมที่สุด — ไม่ใช่รายการตัวเลือกให้ฉันเลือก

---

## หมายเหตุสำหรับผู้ส่งต่องาน (เจ้าของโปรเจกต์)

- ไฟล์นี้แทนที่ `docs/GPT_NEW_CHAT_START_PROMPT.md` เดิม ซึ่งอ้างอิง Carbone
  เป็น PDF engine (เลิกใช้แล้ว — ดู ADR-021 ในสเปค เปลี่ยนเป็น Zoho Catalyst
  SmartBrowz) และเขียนไว้ก่อนที่โปรเจกต์จะไปถึง Phase 8
- ใช้ได้กับ AI ตัวไหนก็ได้ ไม่ผูกกับเครื่องมือใดเครื่องมือหนึ่ง — สิ่งที่ทำให้
  AI ตัวใหม่ "รับช่วงได้เนียน" ไม่ใช่ตัวโมเดล แต่คือการบังคับให้อ่าน
  SESSION_HANDOFF.md ก่อนอันดับแรกเสมอ เพราะนั่นคือที่เดียวที่มีสถานะจริง
  ล่าสุดที่ไม่มีใน git history หรือ spec แบบ static
- อัปเดต `docs/SESSION_HANDOFF.md` ทุกครั้งที่ session ใกล้จบ ไม่ว่า AI ตัวไหน
  เป็นคนทำ — ไฟล์นี้คือกลไกเดียวที่ทำให้สลับ AI ข้ามเครื่องมือได้โดยไม่ต้อง
  เล่าบริบทซ้ำทุกครั้ง
