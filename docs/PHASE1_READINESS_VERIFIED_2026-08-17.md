# Chann CRM AI — Verified Phase 1 Readiness Report

**Date:** 2026-08-17 (Asia/Bangkok)  
**Evidence run:** `20260817T034823Z`  
**Current phase:** Pre-Phase 1 / Source and infrastructure planning  
**Overall status:** `SOURCE_REVIEW_READY / INFRASTRUCTURE_APPLY_BLOCKED`

No cloud mutation, Production change, IAM inspection, Service Account permission inspection, Secret Manager inspection, Terraform plan/apply/import, deployment, database creation, or cleanup was executed.

## 1. Source-of-truth status

The full required documentation sequence has been read. The product authority remains `CHANN_CRM_AI_MASTER_SPEC.md`; the accepted document-engine decision remains `SMARTBROWZ_DOCUMENT_ENGINE.md`.

The implementation remains a greenfield application. The old Chann1 business application, schema, API, seed, and compatibility contract are not implementation inputs.

Infrastructure reference repository evidence:

- Repository origin: `https://github.com/Actvee/chann1-platform.git`
- Verified `origin/main` commit: `76a66bc964c9a51a6a2859771540ea05b9fec4ea`
- Repository use: infrastructure/state reference only
- User working tree/branch changed: no

The Chann CRM AI greenfield scaffold has not yet been inspected in this chat.

## 2. Verified live infrastructure

| Resource | DEV | Stage/Test | Production | Evidence status |
|---|---|---|---|---|
| Cloud SQL PostgreSQL 16 | `chann1-dev-pg` RUNNABLE | `chann1-stage-pg` RUNNABLE | `chann1-prod-pg` RUNNABLE | `PROVEN` |
| Application DB | only `postgres` exists | only `postgres` exists | only `postgres` exists | `chann_crm_ai` absent by evidence |
| Redis | `chann1-dev-cache` READY | `chann1-stage-cache` READY | `chann1-prod-cache` READY | `PROVEN` |
| VPC | `chann1-dev-net` | `chann1-stage-net` | `chann1-prod-net` | `PROVEN` |
| VPC connector | `chann1-dev-vpc` READY | `chann1-stage-vpc` READY | `chann1-prod-vpc` READY | `PROVEN` |
| Cloud Run application services | absent | absent | absent | `PROVEN_ABSENT` |

Additional evidence:

- Artifact Registry `chann1-dev` and `chann1-stage` exist as Docker repositories.
- Both repositories currently contain no listed packages; no legacy application images were found.
- No GCS buckets were listed in project `chann1-1`.
- No dedicated Production Artifact Registry repository was found.

## 3. Terraform state finding

A verified Terraform 1.15.8 binary was downloaded temporarily from HashiCorp and validated by SHA-256 before use.

All three environment roots were found:

- `infra/terraform/environments/dev`
- `infra/terraform/environments/stage`
- `infra/terraform/environments/prod`

`terraform init` failed consistently because the configured GCS backend bucket does not exist. Consequently:

- usable Terraform state: `NOT_AVAILABLE`
- existing resources proven managed by state: none
- blind Terraform apply: prohibited
- old Terraform configuration: desired-state/reference evidence only, not ownership evidence

## 4. Final adoption strategy for the greenfield repository

The conservative ownership boundary is selected to prevent accidental adoption or recreation of persistent infrastructure.

| Resource group | Classification | Implementation rule |
|---|---|---|
| Existing Cloud SQL instances | `REFERENCE_ONLY_NOT_MANAGED` | Resolve through Terraform data/reference inputs; do not declare ownership |
| Existing Redis instances | `REFERENCE_ONLY_NOT_MANAGED` | Same |
| Existing VPC networks | `REFERENCE_ONLY_NOT_MANAGED` | Same |
| Existing VPC connectors | `REFERENCE_ONLY_NOT_MANAGED` | Same |
| Existing Artifact Registry repositories | `REFERENCE_ONLY_NOT_MANAGED` | Reuse DEV/Stage repositories without adopting them into new state |
| Terraform backend bucket | `NEW_RESOURCE_REQUIRED` | Bootstrap separately before first new-repository apply; exact config requires scaffold review |
| Chann CRM AI Cloud Run services | `NEW_RESOURCE_REQUIRED` | Presentation/Application/Data, DEV first |
| `chann_crm_ai` database | `NEW_RESOURCE_REQUIRED` | Create inside each existing Cloud SQL instance; DEV first |
| Chann CRM AI DB user/config | `NEW_RESOURCE_REQUIRED` | Per environment; no IAM/Secret Manager scope |
| Chann CRM AI GCS application bucket | `NEW_RESOURCE_REQUIRED` | Required for documents/files; naming and lifecycle locked during scaffold review |

Current classifications:

- `ALREADY_MANAGED_BY_STATE`: none proven
- `IMPORT_OR_ADOPT_REQUIRED`: none selected
- `REFERENCE_ONLY_NOT_MANAGED`: all existing persistent infrastructure listed above
- `NEW_RESOURCE_REQUIRED`: Chann CRM AI app-specific resources and a new Terraform backend

This strategy avoids competing Terraform ownership and satisfies the rule not to assume an empty/missing state means infrastructure is absent.

## 5. Architecture and database confirmation

Logical architecture remains:

`Presentation -> Application -> Data -> Database`

Redis, GCS, Zoho Catalyst SmartBrowz, LINE, OpenRouter, payment provider, and scheduled jobs remain supporting services/integrations.

Database strategy:

- new logical DB: `chann_crm_ai`
- new Alembic baseline
- no old Chann1 schema migration
- initialization: migration -> idempotent reference seed -> optional fixture -> runtime readiness -> business acceptance
- DEV first; Production requires explicit approval

## 6. Readiness interpretation

Read-only infrastructure discovery is complete. Existing-resource ownership strategy is now explicit.

Phase 1 application/source review may begin. Infrastructure plan/apply remains blocked until:

1. the greenfield Phase 1 scaffold is inspected;
2. the new backend and app-specific Terraform boundary are defined;
3. a no-change/no-replacement plan for existing persistent resources is verified;
4. any DEV cloud creation is separately reviewed before execution.

Production remains out of scope without explicit approval.

## 7. Next safe step — one set only

Inspect `chann-crm-ai-phase1-scaffold.zip` as the candidate greenfield application source. Validate its 4-tier boundaries, Phase 1 schema/migrations/seeds, tests, selective-deployment tooling, Terraform ownership boundary, runtime configuration placeholders, and absence of copied Chann1 business compatibility.

No further preflight rerun is required unless infrastructure changes or evidence becomes stale.

## 8. Blocking decisions

No immediate product decision is required from the user. The remaining blocker is source evidence: the Phase 1 scaffold must be made available for inspection.

External runtime configuration remains `REQUIRED_NOT_CONFIGURED` as documented. SmartBrowz is a Phase 10 readiness dependency and payment-provider selection is deferred to Phase 17.5; neither blocks Phase 1 source review.
