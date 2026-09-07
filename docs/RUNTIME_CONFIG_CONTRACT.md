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

ตารางนี้ต้องตรงกับ `application/chann_app/config.py` ทุกตัว (`tests/unit/test_config_contract.py` บังคับ) — ตัวแปรที่ config ไม่อ่านห้ามอยู่ในตาราง และ field ใน config ทุกตัวต้องมีแถวที่นี่

| Variable | Status | Purpose |
|---|---|---|
| `DATA_BASE_URL` | DERIVED_AT_DEPLOY | internal Data Tier endpoint |
| `ADMIN_SECRET` | REQUIRED_NOT_CONFIGURED | shared secret for the internal Data Tier API (`X-Internal-Secret`) |
| `REMINDER_SWEEP_SECRET` | REQUIRED_FOR_SCHEDULED_JOBS | static `X-Sweep-Secret` Cloud Scheduler sends to the sweep endpoints (reminders, quotes/warranties expiry, chat SLA, trials); unset = every sweep refuses (`routers_admin.require_scheduler`) |
| `LINE_CUSTOMER_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Customer OA webhook |
| `LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Customer OA, rich-menu linking |
| `LINE_SALES_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Sales OA webhook |
| `LINE_SALES_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Sales OA, rich-menu linking |
| `LINE_TECHNICIAN_CHANNEL_SECRET` | REQUIRED_NOT_CONFIGURED | verify Technician OA webhook |
| `LINE_TECHNICIAN_CHANNEL_ACCESS_TOKEN` | REQUIRED_NOT_CONFIGURED | push/reply Technician OA, rich-menu linking |
| `LINE_LOGIN_CHANNEL_ID` | REQUIRED_NOT_CONFIGURED | expected ID-token audience (`client_id`); Channel ID, not full LIFF app ID |
| `JWT_SECRET` | REQUIRED_NOT_CONFIGURED | signs platform-admin session tokens AND the one-object links sent into LINE (`/api/v1/documents/{token}`, `/api/v1/assets/{token}`) |
| `JWT_TTL_S` | OPTIONAL (default 86400) | platform-admin session lifetime, seconds |
| `OPENROUTER_API_KEY` | REQUIRED_NOT_CONFIGURED | OpenRouter access |
| `OPENROUTER_MODEL` | REQUIRED_NOT_CONFIGURED | chat-tier model selector (thinking off) |
| `OPENROUTER_MODEL_REASONING` | OPTIONAL_UNTIL_PHASE_17 | reasoning-tier model selector (ad-hoc reports) |
| `CATALYST_API_DOMAIN` | OPTIONAL (default `https://api.catalyst.zoho.com`) | Catalyst API host (datacenter-specific); Terraform also exports it to the SDK as `X_ZOHO_CATALYST_CONSOLE_URL` |
| `CATALYST_PROJECT_ID` | REQUIRED_BY_PHASE_10 | Zoho Catalyst project containing SmartBrowz |
| `CATALYST_ENVIRONMENT` | OPTIONAL (default `Development`) | Catalyst environment name |
| `CATALYST_ZAID` | REQUIRED_BY_PHASE_10 | Zoho Account ID of the Catalyst project environment (mandatory in the SDK's `ICatalystOptions`) |
| `SMARTBROWZ_ACCOUNTS_URL` | OPTIONAL (default `https://accounts.zoho.com`) | datacenter-specific Zoho accounts host for the OAuth token exchange; Terraform also exports it to the SDK as `X_ZOHO_CATALYST_ACCOUNTS_URL` |
| `SMARTBROWZ_CLIENT_ID` | REQUIRED_BY_PHASE_10 | Self Client id from the Catalyst API Console |
| `SMARTBROWZ_CLIENT_SECRET` | REQUIRED_BY_PHASE_10 | Self Client secret |
| `SMARTBROWZ_REFRESH_TOKEN` | REQUIRED_BY_PHASE_10 | the long-lived refresh token (scope `ZohoCatalyst.pdfshot.execute`); the zcatalyst-sdk refreshes access tokens from it per process — there is no project-side token cache |
| `GCS_BUCKET_NAME` | REQUIRED_BY_FILE_FEATURES | PDF / photo / signature / export storage; unset = `NullDocumentStore` refuses every write loudly |
| `GCP_PROJECT_ID` | DERIVED_AT_DEPLOY | project for the GCS client (ADC on Cloud Run; no key file) |
| `LIFF_SALES_ID` / `LIFF_TECHNICIAN_ID` / `LIFF_CUSTOMER_ID` | REQUIRED_NOT_CONFIGURED | deep links from chat into each OA's LIFF app; empty = the link is omitted |
| `PUBLIC_BASE_URL` | DERIVED_AT_DEPLOY | this tier's own externally reachable origin, used to build links sent into LINE and handed to the PDF renderer (documents, photos, signatures, exports); empty = such links are omitted (routes that have the request fall back to its origin) |

ไม่มี `PDF_RENDERER` (SmartBrowz ถูกเลือกในโค้ด), ไม่มี `CRON_SECRET` (ใช้ `REMINDER_SWEEP_SECRET`), ไม่มี `SMARTBROWZ_RENDER_MODE` / `SMARTBROWZ_CATALYST_*` (ชื่อจริงคือ `CATALYST_*`) — ลบออก 6 ก.ย. 2569 (review E12)

## Data

| Variable | Status | Purpose |
|---|---|---|
| `DATABASE_URL` | DERIVED_AT_DEPLOY | new Chann CRM AI database on existing Cloud SQL |
| `REDIS_URL` | DERIVED_AT_DEPLOY | existing environment Redis instance |
| `ADMIN_SECRET` or internal shared secret | REQUIRED_NOT_CONFIGURED | if Data internal endpoint protection uses shared-secret pattern |

## Document rendering / Zoho Catalyst SmartBrowz

The Application variables above (`CATALYST_*`, `SMARTBROWZ_*`, `GCS_BUCKET_NAME`) are the whole contract; the SDK is initialised in "third-party application" mode from them (`services/pdf/smartbrowz.py`). Links to stored objects are served by the Application tier itself (`/api/v1/documents/{token}`, `/api/v1/assets/{token}`, signed with `JWT_SECRET`) — GCS signed URLs are not used because the runtime service account is deliberately not granted `signBlob`.

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
