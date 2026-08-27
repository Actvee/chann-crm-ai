# Chann CRM AI — Runtime Configuration Contract

## Purpose

เอกสารนี้กำหนด **ชื่อ/ประเภท configuration ที่ระบบต้องเตรียม** โดยไม่เก็บ secret จริงใน repository

สถานะหลักที่ใช้:

- `REQUIRED_NOT_CONFIGURED` — ต้องมีเพื่อให้ feature ทำงาน แต่ package นี้ไม่มีค่าจริง
- `DECISION_REQUIRED` — ต้องตัดสินใจภายหลังตาม Phase
- `DERIVED_AT_DEPLOY` — deployment script เป็นผู้หา/กำหนด
- `OPTIONAL_UNTIL_<PHASE>` / `REQUIRED_BY_<PHASE>` — ยังไม่ต้องมีจนถึง Phase/feature นั้น
- `REQUIRED_AFTER_PROVIDER_DECISION` — ต้องมีหลังเลือก external provider แล้ว
- `TO_DEFINE_DURING_<PHASE>` — contract รายละเอียดให้ล็อกใน Phase ที่เกี่ยวข้องก่อน implement

> โปรเจกต์นี้เลือก reduced-security posture โดยเจตนา: secret runtime ถูกส่งเป็น environment variable ตาม `CLAUDE.md`; อย่างไรก็ตามห้าม commit ค่าจริงลง Git

## Common runtime metadata

| Variable | Tier | Status | Purpose |
|---|---|---|---|
| `APP_ENV` | all runtime tiers | DERIVED_AT_DEPLOY | `dev` / `stage` / `production` |
| `PLATFORM_VERSION` | all | DERIVED_AT_DEPLOY | immutable release identity |
| `GIT_COMMIT` | all | DERIVED_AT_DEPLOY | source identity |

## Presentation

| Variable | Status | Purpose |
|---|---|---|
| `APPLICATION_BASE_URL` | DERIVED_AT_DEPLOY | Application Tier endpoint |
| `NEXT_PUBLIC_LIFF_CUSTOMER_ID` | REQUIRED_NOT_CONFIGURED | Customer LIFF |
| `NEXT_PUBLIC_LIFF_SALES_ID` | REQUIRED_NOT_CONFIGURED | Sales/CS/Owner/Admin LIFF |
| `NEXT_PUBLIC_LIFF_TECHNICIAN_ID` | REQUIRED_NOT_CONFIGURED | Technician LIFF |

## Application

| Variable | Status | Purpose |
|---|---|---|
| `DATA_BASE_URL` | DERIVED_AT_DEPLOY | internal Data Tier endpoint |
| `LINE_CUSTOMER_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Customer OA webhook |
| `LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Customer OA |
| `LINE_SALES_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Sales OA webhook |
| `LINE_SALES_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Sales OA |
| `LINE_TECHNICIAN_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Technician OA webhook |
| `LINE_TECHNICIAN_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Technician OA |
| `LINE_LOGIN_CHANNEL_ID` | REQUIRED_NOT_CONFIGURED | expected ID-token audience (`client_id`); Channel ID, not full LIFF app ID |
| `OPENROUTER_API_KEY` | REQUIRED_NOT_CONFIGURED | OpenRouter access |
| `OPENROUTER_MODEL` | REQUIRED_NOT_CONFIGURED | default Qwen model selector |
| `OPENROUTER_MODEL_REASONING` | OPTIONAL_UNTIL_PHASE_17 | DeepSeek reasoning model selector |
| `ADMIN_SECRET` | REQUIRED_NOT_CONFIGURED | reduced-security internal/admin header auth where specified |
| `CRON_SECRET` | OPTIONAL_UNTIL_SCHEDULED_JOBS | reduced-security scheduled endpoint auth |
| `SMARTBROWZ_RENDER_MODE` | REQUIRED_BY_PHASE_10 | baseline: application-managed HTML -> SmartBrowz PDF; optional predefined-template mode only after verified |
| `SMARTBROWZ_CATALYST_PROJECT_ID` | REQUIRED_BY_PHASE_10 | Zoho Catalyst project containing SmartBrowz |
| `SMARTBROWZ_CATALYST_ORG_ID` | REQUIRED_BY_PHASE_10 | Catalyst organization identifier when required by REST integration |
| `SMARTBROWZ_ACCOUNTS_URL` | REQUIRED_NOT_CONFIGURED_IN_TFVARS | datacenter-specific Zoho accounts host for the OAuth token exchange — confirmed `https://accounts.zoho.com` (US datacenter) from the real token response's `api_domain` field; Terraform variable now wired with this as its default |
| `SMARTBROWZ_CLIENT_ID` | REQUIRED_NOT_CONFIGURED_IN_TFVARS | from the Catalyst API Console's Self Client — owner has generated this; Terraform variable declared (`infrastructure/terraform/variables.tf`), needs the real value added to `terraform.tfvars` (never committed) |
| `SMARTBROWZ_CLIENT_SECRET` | REQUIRED_NOT_CONFIGURED_IN_TFVARS | from the Catalyst API Console's Self Client — owner has generated this; same as above |
| `SMARTBROWZ_REFRESH_TOKEN` | REQUIRED_NOT_CONFIGURED_IN_TFVARS | one-time grant token already exchanged for a refresh_token — owner has generated this; confirmed scope `ZohoCatalyst.pdfshot.execute ZohoCatalyst.dataverse.execute` (the first is what this project needs; `dataverse` — web-scraping/lead-enrichment — came along with Self Client's default scope set and isn't used, harmlessly). Same as above: needs the real value added to `terraform.tfvars` |
| `GCS_BUCKET_NAME` | REQUIRED_BY_FILE_FEATURES | PDF/photo/signature storage |

## Data

| Variable | Status | Purpose |
|---|---|---|
| `DATABASE_URL` | DERIVED_AT_DEPLOY | new Chann CRM AI database on existing Cloud SQL |
| `REDIS_URL` | DERIVED_AT_DEPLOY | existing environment Redis instance |
| `ADMIN_SECRET` or internal shared secret | REQUIRED_NOT_CONFIGURED | if Data internal endpoint protection uses shared-secret pattern |

## Document rendering / Zoho Catalyst SmartBrowz

| Variable | Status | Purpose |
|---|---|---|
| `GCS_BUCKET_NAME` | REQUIRED_BY_PHASE_10 | original DOCX, compiled template source, generated PDF and evidence storage |
| `SMARTBROWZ_RENDER_MODE` | REQUIRED_BY_PHASE_10 | `html_convert` is the v1 baseline; `predefined_template` is optional after management automation is verified |
| `SMARTBROWZ_CATALYST_PROJECT_ID` | REQUIRED_BY_PHASE_10 | Catalyst project identifier |
| `SMARTBROWZ_CATALYST_ORG_ID` | REQUIRED_BY_PHASE_10_WHEN_REST_USED | Catalyst org identifier for REST calls when required |
| `SMARTBROWZ_ACCOUNTS_URL` / `SMARTBROWZ_CLIENT_ID` / `SMARTBROWZ_CLIENT_SECRET` / `SMARTBROWZ_REFRESH_TOKEN` | REQUIRED_NOT_CONFIGURED_IN_TFVARS | token-refresh mechanism built and tested (`smartbrowz_auth.py`); Terraform wiring now added (`infrastructure/terraform/variables.tf` + `cloud_run.tf`); real credentials generated by the owner, not yet added to `terraform.tfvars`; not yet wired to a real SmartBrowz render call, which is separate, later work — see `docs/SESSION_HANDOFF.md` |

Authoring rule: Word/DOCX is an input to the AI-assisted template compiler, not the runtime rendering format. Published Chann CRM template versions are immutable application records. Runtime PDF generation must be deterministic and must not call the LLM.

## Billing — Phase 17.5

Billing provider is intentionally not locked in the current source material.

| Item | Status |
|---|---|
| Provider (`Omise` or `2C2P` candidate) | DECISION_REQUIRED |
| Provider API credentials | REQUIRED_AFTER_PROVIDER_DECISION |
| Webhook signing secret | REQUIRED_AFTER_PROVIDER_DECISION |

Do not invent final payment-provider configuration before Phase 17.5 decision work.

## Infrastructure-derived configuration

The deployment tooling should discover or produce these values rather than hardcoding historical values:

- Cloud Run service URLs
- Cloud SQL IP/connection target
- Redis host/port
- VPC Connector name
- Artifact image digest
- GCS bucket name if created by infrastructure phase

## Secret handling rule

- Never place real secret values in this package, source control, logs, release manifests, or AI output.
- The project's current reduced-security design permits runtime env vars but that exception must remain explicit in release manifests.
