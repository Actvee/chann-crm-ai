# Chann CRM AI — Existing Infrastructure Handoff

## 1. Intent

Chann CRM AI เป็น **application ใหม่จากศูนย์บน infrastructure เดิม**

เป้าหมายคือ reuse infrastructure ที่มีอยู่ก่อน และไม่ carry-over business application เดิม

## 2. Known GCP baseline

- GCP Project: `chann1-1`
- Primary Region: `asia-southeast1`
- Environments: `DEV`, `Stage/Test`, `Production`

Known persistent infrastructure from the Chann1 reference project:

| Environment | Cloud SQL | Redis | Cloud Run application | old `chann1` DB |
|---|---|---|---|---|
| DEV | `chann1-dev-pg` | `chann1-dev-cache` | intentionally removed | intentionally removed |
| Stage | `chann1-stage-pg` | `chann1-stage-cache` | intentionally removed | intentionally removed |
| Production | `chann1-prod-pg` | `chann1-prod-cache` | intentionally removed | intentionally removed |

Application DB users from the old proof were also removed.

Artifact Registry repositories known from the latest cleanup session:

- `chann1-dev`
- `chann1-stage`

At the time of handoff, legacy Docker packages named `application`, `data`, and `presentation` had been observed in those repositories. A cleanup attempt using the wrong `gcloud artifacts packages delete --delete-tags` syntax failed, so **their current existence must be verified by `scripts/infra-preflight.sh`**. They are legacy/non-authoritative even if still present.

Network and VPC Connector resources are expected to remain, but their exact current names and state must be discovered by preflight rather than assumed from historical documentation.

## 3. What must be reused by default

Before proposing new infrastructure, inspect and prefer reuse of:

- GCP project
- regional VPC/network resources
- Serverless VPC Access connectors
- Cloud SQL PostgreSQL instances
- Redis instances
- Artifact Registry repositories
- existing environment topology and naming conventions where suitable

Do not recreate these automatically.

## 4. What is intentionally absent

The following should be treated as absent / new-build scope:

- Presentation runtime service
- Application runtime service
- Data runtime service
- application database/schema
- application DB users
- application migrations
- reference seed
- environment fixtures
- Chann CRM AI container images

The new system therefore starts with a **clean application state**, not a migration from the old CRM.

## 5. Four-tier architecture vs infrastructure

The four-tier model is a **logical architecture**, not a list of GCP resources.

The required logical flow is:

`Presentation -> Application -> Data -> Database`

Typical infrastructure mapping:

- Presentation -> Cloud Run service
- Application -> Cloud Run service
- Data -> Cloud Run service
- Database -> existing Cloud SQL PostgreSQL instance + a new application database
- Redis -> supporting cache used only by Data Tier
- Artifact Registry -> immutable container storage
- VPC/Connector -> network path to private supporting resources

Zoho Catalyst SmartBrowz is an external/supporting document-rendering service. It does not create a fifth logical business tier and does not replace the Application/Data boundaries.

## 6. Terraform adoption gate

Infrastructure exists independently of the new application's Terraform state.

Before the first `terraform apply`, AI must inventory each persistent resource and classify it:

- `ALREADY_MANAGED_BY_STATE`
- `IMPORT_OR_ADOPT_REQUIRED`
- `REFERENCE_ONLY_NOT_MANAGED`
- `NEW_RESOURCE_REQUIRED`

A Terraform configuration that declares an existing resource without state reconciliation must not be applied blindly.

## 7. Database strategy

Because the old application DB was intentionally deleted, Phase 1 should create a fresh application database/schema for Chann CRM AI inside each existing Cloud SQL instance.

Recommended logical DB name: `chann_crm_ai` unless the implementation plan chooses another consistent name.

Initial schema is a new Alembic baseline. There is no requirement to preserve compatibility with the old Chann1 CRM schema.

After real Production data exists, normal schema evolution must follow the compatibility/migration rules in the reference standards.

## 8. Security scope

This package preserves the project's explicitly chosen reduced-security implementation posture documented in `CLAUDE.md` and the Master Spec.

Do not inspect or modify IAM, Service Account permissions, IAM Policy Troubleshooter, or Secret Manager unless the user explicitly changes that scope.

Do not misrepresent reduced-security deployment as a secure production-ready security design.

## 9. Destructive-operation rule

Preflight is read-only.

Do not delete or recreate persistent infrastructure, databases, Redis instances, networks, connectors, repositories, or Production resources without showing the exact impact and obtaining explicit approval.
