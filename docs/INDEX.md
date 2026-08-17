# Chann CRM AI — AI Project Documentation Package

**Package status:** FINAL HANDOFF v3 — 2026-08-17

## Purpose

ชุดนี้พร้อมใช้เป็น handoff ให้ AI/coding agent เริ่มพัฒนา **Chann CRM AI จากศูนย์บน GCP infrastructure เดิม** โดยยึด 4-tier architecture:

`Presentation -> Application -> Data -> Database`

Chann1 เดิมเป็น reference สำหรับ infrastructure/process lessons เท่านั้น ไม่ใช่ application compatibility target

## Read first

1. `00_START_HERE.md`
2. `00_INFRASTRUCTURE_HANDOFF.md`
3. `ENVIRONMENT_RESOURCE_MAP.yaml`
4. `RUNTIME_CONFIG_CONTRACT.md`
5. `SMARTBROWZ_DOCUMENT_ENGINE.md`
6. `CLAUDE.md`
7. `AI_FIRST_TASK.md`
8. `GPT_NEW_CHAT_START_PROMPT.md`

ก่อน Infrastructure plan/apply ครั้งแรก ให้รัน:

`./scripts/infra-preflight.sh`

script เป็น read-only และไม่ inspect IAM/Secret Manager

## Product source of truth

`CHANN_CRM_AI_MASTER_SPEC.md`

Master Spec รวมแล้ว:

- Locked Scope
- 19 ADR
- detailed requirements
- Phase 16.5 PDPA
- Phase 17.5 Billing
- corrected Permission Keys appendix/default roles
- final document-generation decision: DOCX upload + AI-assisted template compilation + Zoho Catalyst SmartBrowz deterministic PDF rendering

ไม่ต้องหา/merge source documents แยกอีก

## Reference / process documents

1. `01_REFERENCE_ARCHITECTURE_INFRASTRUCTURE_BLUEPRINT.md`
2. `02_DEVELOPMENT_TESTING_RELEASE_OPERATIONS_PLAYBOOK.md`
3. `03_DATABASE_MIGRATION_CACHE_DATA_SAFETY_STANDARD.md`
4. `04_SECURITY_ENVIRONMENT_MODEL.md`
5. `05_FINAL_REFERENCE_PROJECT_REPORT.md`
6. `06_PROJECT_BOOTSTRAP_MASTER_INSTRUCTIONS_FOR_AI.md`

เอกสาร 01-06 ถ่ายทอดสิ่งที่พิสูจน์จาก Chann1 เช่น selective deployment, dependency-aware testing, migration/seed ordering, build-once promotion, runtime acceptance และ rollback discipline

## Existing infrastructure snapshot

Known persistent baseline:

- Project `chann1-1`
- Region `asia-southeast1`
- Cloud SQL: `chann1-dev-pg`, `chann1-stage-pg`, `chann1-prod-pg`
- Redis: `chann1-dev-cache`, `chann1-stage-cache`, `chann1-prod-cache`
- Artifact Registry known: `chann1-dev`, `chann1-stage`
- old application Cloud Run services: removed by intent
- old `chann1` application DB: removed by intent in all environments
- old application DB users: removed by intent
- legacy application images may still exist and are non-authoritative; verify with preflight

Live preflight output is authoritative over stale documentation.

## Critical interpretation

- 4-tier is **logical architecture**; it is retained even though the old application has been removed.
- Existing Cloud SQL/Redis/VPC/connectors/registries are infrastructure to reuse where suitable.
- New application schema starts clean; no old CRM migration is required.
- Supporting services such as Zoho Catalyst SmartBrowz, GCS, Redis, LINE, OpenRouter and payment provider are not additional business tiers.
- DOCX is an authoring input. AI is used only to compile/map a template version; runtime PDF generation is deterministic and AI-free.
- The v1 renderer baseline is application-managed HTML/CSS/Liquid-compatible output -> SmartBrowz PDF conversion, so the product does not depend on manual SmartBrowz console template creation for every customer template.
- Terraform must reconcile/adopt existing resources before first apply.
- Production changes remain explicit approval points.
- Reduced-security implementation posture remains an intentional, documented limitation under current scope.
