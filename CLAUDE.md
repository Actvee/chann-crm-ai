# Chann CRM AI — Instructions for Claude Code / Coding Agent

> ใช้ไฟล์นี้เป็น agent instruction ของ repo Chann CRM AI

## -1. อ่านสิ่งนี้ก่อน — สภาพจริง ณ 3 ก.ย. 2569 (ส่วนนี้ทับทุกอย่างด้านล่างเมื่อขัดกัน)

ส่วนที่เหลือของไฟล์นี้เขียนตอน bootstrap และยังถูกต้องเป็นหลักการ แต่โปรเจกต์
ผ่าน Phase 1–13 (+16, +14-A) มาแล้ว และมี**วิธีทำงานที่พิสูจน์แล้ว**จากการ deploy
จริงหลายสิบรอบ ทำตามนี้ก่อน:

### เอกสารที่ต้องอ่านเป็นอันดับแรก (ตามลำดับ)
1. `docs/SESSION_HANDOFF.md` — สถานะจริงล่าสุด: อะไร deploy แล้ว (SHA), patch ที่
   ค้าง, บทเรียนทุกข้อพร้อมเหตุผล อ่านส่วนบนสุดก่อน แล้วค่อยไล่ลง
2. `docs/AI_HANDOFF_START_PROMPT.md` — วิธีเริ่ม session ให้ต่อเนื่องจากคนก่อน
3. `docs/PLAN_3OA.md` — แผนงานที่เหลือของ 3 OA เรียงใหม่จาก spec (3 ก.ย. เย็น) — งานถัดไปอยู่ที่นี่
4. `docs/PHASE14_PLAN.md` — Phase 14 (A/B/C เสร็จ รอ runtime acceptance)
5. `docs/CRM_COMPLETENESS.md` — ช่องว่างเทียบ CRM ตลาด และลำดับที่เสนอ
6. `docs/CHANN_CRM_AI_MASTER_SPEC.md` — product source of truth (ยังใช่)

### สภาพแวดล้อมที่คุณอยู่
- คุณรันใน **Google Cloud Shell ของ release owner** — `gcloud` login ด้วยตัวตนของเขา
  ทุกอย่างที่คุณทำคือเขาทำ ดังนั้น: อ่านได้เสรี แต่**การเปลี่ยนสถานะ GCP ทำผ่าน
  deploy script ที่มี gate เท่านั้น** ไม่พิมพ์ `gcloud run services update` /
  `terraform apply` เอง (ยกเว้น fallback ที่ script พิมพ์ให้และเจ้าของสั่ง)
- Repo อยู่ที่ `~/chann-crm-ai` · เจ้าของโหลด patch/script ไว้ที่ `$HOME`
- `terraform.tfvars` (gitignored) ถือ digest ของ 3 tier + `git_commit` +
  `platform_version` — ห้าม commit, ห้าม cat ทั้งไฟล์ลง log (มี secret)
- **IPv6 บน Cloud Shell วูบเป็นพักๆ**: `dial tcp [2xxx::]:443: cannot assign
  requested address` ไม่ใช่ DNS, `GODEBUG=netdns=go` ช่วยไม่ครบ — รันซ้ำผ่าน
  แทบทุกครั้ง script มี retry 3 รอบอยู่แล้ว ถ้าล้มครบให้ใช้ fallback ที่ script
  พิมพ์ (ต้องอัป `GIT_COMMIT` env ด้วยเสมอ ไม่งั้น `/health` โกหก commit)
- ห้ามแตะ IAM / Service Account / Secret Manager โดยเด็ดขาด (เจ้าของยืนยันซ้ำ)

### วิธีส่งงาน — patch + deploy script (ทำตามเป๊ะ)
- ทุกการเปลี่ยนแปลงส่งเป็น `git diff origin/main` หลัง `git add -A` ชื่อ
  `<topic>-v<N>-<จำนวนบรรทัด>.patch` — จำนวนบรรทัดในชื่อคือ guard กันหยิบผิดไฟล์
- ก่อนสร้าง patch: `git fetch origin && git reset --hard origin/main` แล้วค่อยแก้
  (patch จาก base ผิดเกิดมาแล้ว 3 ครั้ง)
- **validate บน clone สดเสมอ**: `git clone /path/.git /tmp/fc && git checkout
  <base> && git apply --3way --check` แล้วรันเทสต์บนนั้น
- deploy script มาตรฐาน 9 stage (ดูตัวอย่างล่าสุดใน `docs/SESSION_HANDOFF.md` และ
  script ที่เจ้าของเคยรัน): SYNC+linecount → APPLY+grep symbol เฉพาะของ patch →
  TEST (unit+boundary+sims+**check-parity**+typecheck/build ถ้าแตะ UI) → COMMIT+PUSH
  (`git add -A`, ตรวจ tree สะอาด) → BUILD image เฉพาะ tier ที่แตะ →
  UPDATE tfvars (anchor-edit ด้วย regex ต่อบรรทัด) → PLAN (retry 3, die ถ้ามี
  destroy) → APPLY เฉพาะเมื่อ `ALLOW_APPLY=YES` ซึ่ง**เจ้าของเป็นคนพิมพ์** →
  RUNTIME CHECK `/health` ต้องตอบ SHA เต็ม, presentation ผ่าน `/api/ready`
- script ต้อง**รันซ้ำได้**: ถ้า HEAD subject == COMMIT_SUBJECT ให้ข้าม apply/commit
- **มี migration** = build `database` image → `gcloud run jobs update
  chann-crm-ai-dev-migrate --image` → `execute --wait` **ก่อน** deploy data
  (data image ตรวจ `EXPECTED_MIGRATION_HEAD` ตอน boot)
- **ห้ามอ้างว่า deploy สำเร็จ**จนกว่า `/health` ตอบ `git_commit` ตรง SHA ที่ push

### กติกาที่เจ้าของตั้ง (บังคับกับทุกฟีเจอร์)
1. แชท = ผู้ช่วยที่เข้าใจความหมาย ไม่ใช่ command parser · เวลาไม่ระบุ = 09:00
2. **Parity: ทำได้ในแชทต้องทำได้ใน UI และกลับกัน** — `scripts/dev/check-parity.py`
   ต้องอ่าน "every capability is reachable from both surfaces" ก่อน deploy
   ฟีเจอร์ใหม่ต้องมาทั้งสองฝั่งตั้งแต่ patch แรก (ลงทะเบียนใน `ACTION_PERMISSIONS`
   แล้ว checker จะบังคับ UI ให้เอง)
3. ระบุตัวตนได้ทุกทาง: รหัส / ชื่อในประโยค / บริบทที่เพิ่งดู / reply ข้อความเก่า
   และ**ชื่อซ้ำต้องได้ปุ่มเลือกคน** (`_AmbiguousName` → `_name_choice`) ห้ามเดา ห้ามเงียบ
4. ไม่มี key ดิบ (`due_time`) โผล่ให้คนเห็น · ไม่ถามสิ่งที่ระบบตอบเองได้
5. ธีมสีต่อ OA: Sale เขียว `#178a50` · Tech น้ำเงิน `#1f6fd6` · CS ส้ม `#e8731a`
   (`[data-theme]` ใน `presentation/app/globals.css`)
6. **วิธีใช้ต้องอัปเดตตลอด** (เจ้าของ 3 ก.ย.): คำสั่งใหม่/เปลี่ยนชื่อ → แก้
   `application/chann_app/services/guides.py` แล้วรัน `python3 scripts/dev/render-guides.py`
   (`tests/unit/test_guides.py` บังคับ) · คำแนะนำที่ควรมีรูป → ใส่ slot + prompt ในขั้นนั้น
   เจ้าของจะ generate รูปแล้วใส่ URL ใน `help_images.json` ทั้งสองไฟล์ · ข้อความสิทธิ์ต้อง
   จัดหมวด/อ่านง่าย และบอกว่าขอสิทธิ์อะไรจากใคร

### เครื่องมือตรวจที่ต้องรันก่อนบอกว่า "เสร็จ"
```
/tmp/dv/bin/python -m pytest tests/unit tests/boundary -q     # ~650
TEST_DATABASE_URL=... pytest tests/integration -q             # ~266 ต้องมี Postgres
python scripts/dev/simulate-day.py ; simulate-edge-cases.py   # ต้อง "0 FINDINGS"
python scripts/dev/check-parity.py                            # ต้องสะอาด
python scripts/dev/check-{routes,client,perms,triggers,i18n-usage,chat-format,auth,placeholders,fields,methods}.py
cd presentation && npm run typecheck && npm run build         # ถ้าแตะ UI
```
เทียบผล check-* กับรอบก่อน: ควรต่างแค่ตัวเลข ถ้ามีบรรทัดใหม่ต้องอธิบายได้

### บทเรียนที่จ่ายแพงแล้ว (อย่าจ่ายซ้ำ)
- **tier-seam bug มองไม่เห็นจาก log ของ tier ที่กำลังดู** — MemberOut ไม่ส่ง `id`,
  `Warranty.purchase_date` ที่ไม่มีจริง (ชื่อจริง `warranty_start/_end`):
  เทียบ contract จริงด้วย `check-fields` และเทสต์ HTTP end-to-end เสมอ
- helper ที่เป็น local ของ router อื่น (`_joined`) → `NameError` ตอนรันจริง
  ทั้งที่เทสต์เขียว — ทุก branch ใหม่ต้องมีเทสต์ที่วิ่งผ่านมันจริง
- อักษรไทยเป็น word character: `\b` ใน regex มองรหัสที่ติดกับ "ของ" ไม่เห็น
- `audit_log.action` มี CHECK constraint — ใช้ verb ที่มี (`create update delete
  assign transfer status reject claim check_in check_out ...`) ห้ามคิดใหม่
- regex ที่ capture context แบบ consuming จะกลืน match ถัดไป — ใช้ lookahead

---

## 0. Operating context — อ่านก่อนทุกอย่าง

โปรเจกต์นี้คือ **Greenfield Application บน Existing GCP Infrastructure**

- ห้ามอ้างอิง business application เดิมเป็น compatibility target
- ห้าม copy source/API/schema/seed จาก Chann1 CRM เดิม
- ให้ reuse เฉพาะ Infrastructure ที่ยังมีอยู่จริง และ reuse architecture/process lessons ที่เหมาะกับ requirement ใหม่นี้
- ก่อน Infrastructure change ครั้งแรก ต้องอ่าน `00_INFRASTRUCTURE_HANDOFF.md`, `ENVIRONMENT_RESOURCE_MAP.yaml` และรัน `scripts/infra-preflight.sh`
- ห้าม assume ว่า Terraform state รู้จัก existing resources อยู่แล้ว

**4-tier architecture ถูกล็อกสำหรับโปรเจกต์นี้:**

`Presentation -> Application -> Data -> Database`

Supporting services เช่น Redis, GCS, Zoho Catalyst SmartBrowz, LINE, OpenRouter, payment provider และ cron **ไม่ใช่ tier เพิ่ม**

## 1. บทบาทของคุณ

คุณเป็น architecture / infrastructure / development / testing / release / operations copilot ไม่ใช่แค่ code generator

เป้าหมายคือให้ architecture, tests, runtime behavior, deployment, rollback และ documentation ตรงกันตลอด

**ห้ามอ้างความสำเร็จจาก design intent:** เขียนโค้ดเสร็จ, build ผ่าน หรือ deploy command สำเร็จ ไม่เท่ากับ feature complete จนกว่าจะมี runtime business evidence ตาม acceptance criteria

## 2. Source of truth และลำดับการอ่าน

อ่านตามลำดับ:

1. `00_START_HERE.md`
2. `00_INFRASTRUCTURE_HANDOFF.md`
3. `ENVIRONMENT_RESOURCE_MAP.yaml`
4. `RUNTIME_CONFIG_CONTRACT.md`
5. `01_REFERENCE_ARCHITECTURE_INFRASTRUCTURE_BLUEPRINT.md`
6. `02_DEVELOPMENT_TESTING_RELEASE_OPERATIONS_PLAYBOOK.md`
7. `03_DATABASE_MIGRATION_CACHE_DATA_SAFETY_STANDARD.md`
8. `04_SECURITY_ENVIRONMENT_MODEL.md`
9. `05_FINAL_REFERENCE_PROJECT_REPORT.md`
10. `06_PROJECT_BOOTSTRAP_MASTER_INSTRUCTIONS_FOR_AI.md`
11. `SMARTBROWZ_DOCUMENT_ENGINE.md`
12. `CHANN_CRM_AI_MASTER_SPEC.md`

เอกสาร 01-06 คือ reference/process lessons ไม่ใช่ product requirement

**Product Source of Truth เดียวคือ `CHANN_CRM_AI_MASTER_SPEC.md`**

Master Spec ได้รวมเนื้อหาเดิมของ SCOPE_LOCKED, ADR, REQUIREMENTS, Phase 16.5/17.5 addendum และ Permission Keys audit แล้ว

ดังนั้น:

- ไม่ต้องหา `SCOPE_LOCKED.md`, `ARCHITECTURE_DECISIONS.md`, `REQUIREMENTS.md`, `REQUIREMENTS_addendum_16.5_17.5.md`, `PERMISSION_KEYS_AUDIT.md` เป็นไฟล์แยก
- ห้าม merge addendum ซ้ำ
- ห้าม replace permission appendix ซ้ำ
- Permission Keys ฉบับที่อยู่ท้าย Master Spec เป็น source of truth

## 3. Existing Infrastructure rule

Known baseline:

- Project: `chann1-1`
- Region: `asia-southeast1`
- Cloud SQL: `chann1-dev-pg`, `chann1-stage-pg`, `chann1-prod-pg`
- Redis: `chann1-dev-cache`, `chann1-stage-cache`, `chann1-prod-cache`
- old Cloud Run application services: intentionally removed
- old database `chann1`: intentionally removed on DEV/Stage/Production
- old application DB users: intentionally removed
- Artifact Registry repositories known: `chann1-dev`, `chann1-stage`; legacy images may still exist but are non-authoritative

Before first Terraform apply:

1. run read-only preflight;
2. inventory live resources;
3. classify each persistent resource as `ALREADY_MANAGED_BY_STATE`, `IMPORT_OR_ADOPT_REQUIRED`, `REFERENCE_ONLY_NOT_MANAGED`, or `NEW_RESOURCE_REQUIRED`;
4. only then produce/apply Terraform plan.

**Do not recreate Cloud SQL, Redis, VPC, VPC Connector, or Artifact Registry just because application source is new.**

## 4. Environment และ 4-Tier Architecture

มี 3 environments เท่านั้น:

- DEV
- Stage/Test
- Production

Build once / promote exact immutable artifact; ห้ามมี source variant ต่อ environment

Logical boundaries:

- Presentation -> Application only
- Application -> Data only
- Data -> PostgreSQL / Redis
- Presentation ห้าม import/call Data, SQLAlchemy, PostgreSQL, Redis โดยตรง
- Application ห้าม access PostgreSQL/Redis โดยตรง
- Database เป็น source of truth
- Redis เป็น supporting cache ของ Data Tier เท่านั้น

Technology baseline ตาม Master Spec:

- Presentation: Next.js
- Application: FastAPI ตัวเดียว รวม webhook + AI intent/orchestration + domain services
- Data: FastAPI + SQLAlchemy ตัวเดียว
- Database: PostgreSQL + Alembic

Zoho Catalyst SmartBrowz เป็น supporting external integration ไม่ใช่ business tier ที่ 5

Boundary ต้องมี executable tests ตั้งแต่ Phase 1

## 5. Security posture — intentional reduced security

ตาม scope ปัจจุบัน โปรเจกต์นี้จงใจใช้ reduced-security implementation:

- runtime secrets ผ่าน environment variables; ห้าม commit secret จริงลง Git
- ไม่ใช้ Secret Manager ใน scope ปัจจุบัน
- ไม่ทำ IAM/per-service-account least-privilege ใน scope ปัจจุบัน
- internal/admin/cron endpoint ใช้ shared-secret header ตาม design ที่กำหนด

ห้าม inspect/modify IAM, Service Account permissions, IAM Policy Troubleshooter หรือ Secret Manager เว้นแต่ user เปลี่ยน scope ชัดเจน

ทุก Release Manifest ต้องระบุ security limitation อย่างตรงไปตรงมา ห้ามเรียก deployment นี้ว่า secure-production security model

## 6. New database baseline

ไม่มี application schema เดิมที่ต้อง preserve

Phase 1 ให้เริ่ม database ใหม่บน existing Cloud SQL instances:

1. create new application database/user/config ตาม environment;
2. Alembic initial baseline ใหม่;
3. idempotent reference seed ใหม่;
4. fixture เฉพาะ environment เมื่อจำเป็น;
5. deploy runtime;
6. functional acceptance.

Recommended logical DB name คือ `chann_crm_ai` เว้นแต่ implementation plan มีเหตุผลชัดเจนให้ใช้ชื่ออื่น

ตอนนี้ยังไม่ต้อง migrate schema จาก Chann1 เดิม

เมื่อมี real Production data แล้ว ให้ schema evolution ใช้ compatibility/migration discipline จากเอกสาร 03

## 7. Selective deployment + dependency-aware validation

สร้างตั้งแต่ Phase 1:

- `scripts/detect-impact.sh`
- `scripts/dev-validate.sh`
- `scripts/dev-deploy.sh <presentation|application|data|database|auto|all>`

หลักบังคับ:

- deployment scope = smallest changed runtime set
- validation scope = ทุก dependency ที่อาจได้รับผลกระทบ

ตัวอย่าง:

- Presentation change -> deploy Presentation; validate browser-relevant scope
- Application change -> deploy Application; validate Application + Presentation + cross-tier
- Data change -> deploy Data; validate Data + Application + Presentation + cross-tier
- Database change -> migration gate + validate Database upward ทุก tier

ระวัง `gcloud run deploy --set-env-vars`: consolidate environment variables เป็น flag เดียวด้วย delimiter เมื่อจำเป็น; อย่า assume ว่าหลาย flag จะ merge ตามที่ต้องการ

## 8. Database / seed discipline

บังคับ environment init order:

`migration -> idempotent reference seed -> environment fixture (ถ้ามี) -> runtime deploy/readiness -> functional acceptance`

Seed ต้อง idempotent ตาม business key เช่น `permission_key`, `plan_code` ไม่ใช่ rely แค่ UUID

ห้ามพึ่ง manual SQL ที่ไม่ได้อยู่ใน version-controlled process

## 9. Testing pyramid และ Definition of Done

ทุก feature ใช้ตามความเหมาะสม:

1. Unit tests
2. Boundary/contract tests
3. Database integration tests จาก empty database
4. Cross-tier HTTP integration/smoke โดย Application <-> Data จริง
5. Browser E2E
6. Runtime acceptance ใน target environment

**CI PASS != Feature Complete**

Feature จบเมื่อ requirement -> implementation -> automated tests -> environment reproducibility -> runtime business acceptance trace กันได้

## 10. Release / Stage / Production

ทุก release มี immutable manifest อย่างน้อย:

```json
{
  "platform_version": "...",
  "phase": 0,
  "git_commit": "...",
  "presentation_artifact": "...",
  "application_artifact": "...",
  "data_artifact": "...",
  "pdf_service_artifact": "...",
  "database_migration_head": "...",
  "environment": "dev|stage|production",
  "verification_status": {},
  "known_limitations": [
    "reduced-security posture",
    "backup restore drill: NOT_PROVEN_DEFERRED"
  ],
  "security_mode": "PRODUCTION_PROOF_REDUCED_SECURITY"
}
```

ใช้ evidence vocabulary:

- `PROVEN`
- `PROVEN_WITH_LIMITATIONS`
- `NOT_VERIFIED`
- `NOT_PROVEN_DEFERRED`
- `REQUIRES_EXPLICIT_APPROVAL`

Stage ต้องใช้ artifact ที่ตั้งใจ promote ไป Production และ Production ต้อง promote exact Stage-proven artifact โดยไม่ rebuild

Production change ต้องมี explicit approval ก่อน execute

## 11. Backup / Restore truth

Backup configured ไม่เท่ากับ recovery proven

จนกว่าจะมี isolated restore drill จริง ให้ระบุ recovery เป็น `NOT_PROVEN_DEFERRED`

## 12. Phase sequence

ทำตาม dependency/acceptance ใน `CHANN_CRM_AI_MASTER_SPEC.md`

ลำดับปัจจุบัน:

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 7.5 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 16.5 -> 17 -> 17.5 -> 18 -> 19 -> 20`

อย่าปิด Phase จนกว่า Acceptance Criteria ของ Phase นั้นผ่านครบใน environment ที่กำหนด

## 13. Permission keys

ใช้ Permission Keys Appendix **ภายใน `CHANN_CRM_AI_MASTER_SPEC.md`** เป็น source of truth

- ห้าม hardcode role name เพื่อ authorization
- Phase 19 Rich Menu ต้องตัดสิน visibility/action จาก permission key
- default role templates ต้องอ้างอิง permission keys ฉบับปัจจุบัน

## 14. Guardrails

- ห้าม copy old Chann1 business code/schema/API
- ห้ามสร้าง infrastructure ซ้ำโดยไม่ inventory ของเดิม
- ห้าม Terraform apply จน existing-resource/state mapping ชัดเจน
- ห้าม hard delete PDPA erasure records; ทำตาม anonymization requirement ใน Phase 16.5
- ห้าม enforce AI monthly quota ก่อน requirement ที่ล็อกไว้ให้ทำจริง
- เมื่อ CI fail ให้อ่าน log จริงก่อนแก้
- ห้ามปิด/ลบ test เพื่อให้ CI เขียว
- ถ้า long workflow fail ให้ resume จาก safe point; ไม่ restart/destructively clean โดยไม่จำเป็น
- destructive persistent-infrastructure changes ต้องแสดง impact และขอ explicit approval

### 14.1 Guardrails เพิ่มเติมเมื่อมีหลายคน / หลาย agent

โปรเจกต์นี้มีหลายคนช่วยเขียน และแต่ละคนใช้ AI agent ของตัวเอง ข้อต่อไปนี้บังคับ
กับทุก agent ไม่ว่าใครเป็นคนสั่ง:

- **ห้าม agent รัน `terraform apply` เอง** — หยุดที่ plan เสมอ แล้วให้คนตัดสินใจ
  deploy ทำได้เฉพาะ release owner ที่ถือ `terraform.tfvars` (ดู `CONTRIBUTING.md` §0)
- **ห้ามรันคำสั่ง `gcloud` ที่เปลี่ยนสถานะโปรเจกต์** รวมถึงการตอบ `y` ให้ prompt
  อย่าง "enable this API?" — เคยเกิดขึ้นจริงจากสคริปต์ที่ประกาศตัวว่า read-only
  ถ้าสคริปต์อ้างว่า read-only ต้องใส่ `--quiet` หรือ redirect stdin ให้ prompt
  ตอบ "no" โดยปริยาย
- **`git add -A` เสมอ ห้ามใช้รายชื่อไฟล์** และต้องยืนยันว่า `git status` สะอาด
  หลัง commit — โปรเจกต์นี้เสียหายจากเรื่องนี้มาแล้ว 3 ครั้ง (ไฟล์ใหม่ตกหล่นจน
  `main` import โมดูลที่ไม่มีใน repo, และครั้งหนึ่งทั้ง feature หายไปทั้ง phase)
  อาการที่ปิดบังปัญหาคือ DEV ยังทำงานปกติ เพราะ image ถูก build จาก working tree
- **ก่อนแก้ไฟล์ที่ทุก phase ใช้ร่วมกัน** (`models.py`, `routers/internal.py`,
  `schemas.py`) ต้องเช็คก่อนว่ามีคนอื่นถือ phase ที่แตะไฟล์เดียวกันอยู่หรือไม่
- **patch ที่จะส่งให้คนอื่นรัน ต้อง validate ด้วยการ apply บน clone สดก่อน**
  ไม่ใช่แค่ syntax check
- **การตัดสินว่า "patch apply ไปแล้ว" ห้ามใช้แค่ว่าไฟล์มีอยู่หรือไม่** —
  file-existence ไม่ใช่ version check; patch ที่แก้ใหม่จะถูกข้ามเงียบๆ
- **ตรวจ config ด้วย `grep -n <ชื่อ>` ที่โชว์ว่ามีบรรทัดจริงหรือไม่** ห้ามใช้
  `grep -c` กับ pattern เชิงลบ — `grep -c '^key *= *""'` คืน 0 ทั้งกรณีที่มีค่า
  และกรณีที่ไม่มีบรรทัดนั้นเลย เคยทำให้ทั้ง phase เดินบนสมมติฐานผิดมาแล้ว
- **test ต้องยิงของจริง** อย่าง Postgres ผ่าน `docker compose` ไม่ใช่ mock ทุกชั้น
  — bug 2 ตัวที่เจอในโปรเจกต์นี้ถูกจับได้เพราะทดสอบกับ runtime จริงเท่านั้น
  (member cache ไม่ถูก invalidate, และ role name ที่ hardcode)

## 15. เริ่มงานจริง

> ส่วนนี้เป็นของยุค bootstrap — Phase 1–13 เสร็จแล้ว งานปัจจุบันอยู่ใน §-1 ด้านบน
> และ `docs/PHASE14_PLAN.md` เก็บไว้เป็นประวัติ

ก่อน Phase 1 ให้ทำ **Infrastructure + Source-of-Truth Readiness Report** สั้น ๆ:

- preflight result
- resources ที่จะ reuse
- Terraform adoption/import strategy
- new DB strategy
- config ที่ `REQUIRED_NOT_CONFIGURED`
- config/decisions ที่ `DECISION_REQUIRED`
- planned Phase 1 files/tests/deploy scope

จากนั้นเริ่ม **Phase 1 — Architecture & Security Foundation** ตาม Master Spec และปิด Phase ด้วย executable/runtime evidence ไม่ใช่เพียง code completion
