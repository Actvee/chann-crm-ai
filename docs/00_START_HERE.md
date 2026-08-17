# Chann CRM AI — START HERE

## สถานะของโปรเจกต์นี้

นี่คือ **Greenfield Application บน Infrastructure ที่มีอยู่แล้ว**

ให้ถือว่าโปรแกรม Chann1 CRM เดิม **ไม่มีผลต่อการออกแบบระบบใหม่** ทั้ง source code, API contract, database schema, seed data, business entity และ release compatibility เดิมไม่ใช่ข้อบังคับของ Chann CRM AI

สิ่งที่นำมาจาก Chann1 มีเพียง 2 กลุ่ม:

1. **Infrastructure ที่ยังคงอยู่จริงบน GCP** และต้องพยายาม reuse ก่อนสร้างใหม่
2. **Architecture / delivery / testing / operations lessons** ที่พิสูจน์แล้วและอยู่ในเอกสาร 01-06

## Architecture ที่ล็อกสำหรับ Chann CRM AI

ใช้ logical architecture 4 tiers เหมือนเดิม:

`Presentation -> Application -> Data -> Database`

- Presentation: Next.js / LIFF / Dashboard UI
- Application: FastAPI business logic, LINE webhook, AI orchestration, authorization policy
- Data: FastAPI + SQLAlchemy, database/cache access boundary
- Database: PostgreSQL + Alembic migrations

**Supporting services / external integrations ไม่ใช่ tier เพิ่ม** เช่น Zoho Catalyst SmartBrowz, GCS, Redis, LINE OA/LIFF, OpenRouter, payment provider, scheduled jobs

ห้ามเพิ่ม tier ที่ 5 โดยอัตโนมัติ หากจำเป็นต้องเปลี่ยน logical architecture ต้องทำ ADR ใหม่และได้รับการยอมรับก่อน

## Product Source of Truth

ใช้ไฟล์นี้เป็น source of truth เดียวของ product requirement:

`CHANN_CRM_AI_MASTER_SPEC.md`

ไฟล์นี้ได้รวมเนื้อหาที่เดิมเคยอยู่ใน:

- SCOPE_LOCKED.md
- ARCHITECTURE_DECISIONS.md
- REQUIREMENTS.md
- REQUIREMENTS_addendum_16.5_17.5.md
- PERMISSION_KEYS_AUDIT.md

**ไม่ต้องหาไฟล์เหล่านั้นแยก และห้าม merge addendum / permission audit ซ้ำ**

## ลำดับที่ AI ต้องอ่านก่อนเริ่มเขียนโค้ด

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
13. `CLAUDE.md` หรือ equivalent agent instruction file
14. `GPT_NEW_CHAT_START_PROMPT.md` (ใช้เมื่อเริ่มแชทใหม่ใน ChatGPT Project)

จากนั้นให้รัน `scripts/infra-preflight.sh` ก่อนเสนอ Terraform/app deployment plan ครั้งแรก

## กฎ Infrastructure สำคัญ

- ห้าม assume ว่า Infrastructure ไม่มีอยู่
- ห้าม `terraform apply` ครั้งแรกจนกว่าจะ resolve ว่า resource ที่มีอยู่จริงถูก Terraform state manage อยู่แล้วหรือจำเป็นต้อง import/adopt
- ห้ามสร้าง Cloud SQL, Redis, VPC, VPC Connector หรือ Artifact Registry ซ้ำเพียงเพราะ Terraform state ว่าง
- Cloud Run application เดิมถูกล้างออกแล้วโดยเจตนา
- application database เดิมถูกล้างออกแล้วโดยเจตนา
- application DB users เดิมถูกล้างออกแล้วโดยเจตนา
- legacy container images อาจยังคงอยู่ใน Artifact Registry; ถือว่า non-authoritative และตรวจด้วย preflight ก่อนตัดสินใจ cleanup

## Definition of Ready ก่อน Phase 1

AI ต้องส่งรายงานสั้นก่อนลงมือจริงว่า:

- Product Source of Truth อ่านครบ
- live Infrastructure inventory ผ่าน preflight
- existing-resource <-> Terraform-state strategy ชัดเจน
- 4-tier boundaries ชัดเจน
- application database ใหม่ที่จะสร้างถูกระบุ
- runtime config ที่ยังขาดถูกระบุเป็น `NOT_CONFIGURED` / `DECISION_REQUIRED`
- ไม่มี destructive operation ที่ยังไม่ได้รับอนุมัติ

เมื่อครบจึงเริ่ม Phase 1 ได้
