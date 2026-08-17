# Chann CRM AI — Bootstrap Instructions for Claude Code / Coding Agent

> ใช้ไฟล์นี้เป็น agent instruction ของ repo Chann CRM AI ใหม่

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

## 15. เริ่มงานจริง

ก่อน Phase 1 ให้ทำ **Infrastructure + Source-of-Truth Readiness Report** สั้น ๆ:

- preflight result
- resources ที่จะ reuse
- Terraform adoption/import strategy
- new DB strategy
- config ที่ `REQUIRED_NOT_CONFIGURED`
- config/decisions ที่ `DECISION_REQUIRED`
- planned Phase 1 files/tests/deploy scope

จากนั้นเริ่ม **Phase 1 — Architecture & Security Foundation** ตาม Master Spec และปิด Phase ด้วย executable/runtime evidence ไม่ใช่เพียง code completion
