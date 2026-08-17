# Chann CRM AI — Phase 1 Implementation Review

Date: 2026-08-17 (Asia/Bangkok)

## Current state

Phase 1 is **IN PROGRESS / NOT FEATURE COMPLETE**.

The verified readiness baseline remains in
`PHASE1_READINESS_VERIFIED_2026-08-17.md`. Existing Cloud SQL, Redis, VPC,
VPC connectors, and DEV/Stage Artifact Registry repositories are
`REFERENCE_ONLY_NOT_MANAGED`. No Terraform state is proven because the
documented backend buckets do not exist. No Cloud Run service, GCS bucket, or
`chann_crm_ai` database exists in the live project.

The embedded documentation was replaced with the exact FINAL v3 source set.
`CHANN_CRM_AI_MASTER_SPEC.md` is the Product Source of Truth and
`SMARTBROWZ_DOCUMENT_ENGINE.md` is present as the final document-engine
decision.

## Source changes completed in this review

- Preserved the four tiers: Presentation -> Application -> Data -> Database.
- Added working Platform Admin login orchestration: Data verifies Argon2,
  Application issues JWT, Redis stores the revocable session, and session
  cache miss/outage fails closed.
- Added Platform Admin HTTP-only cookie login proxy and protected dashboard
  shell.
- Added three runtime-configured LIFF shells and LIFF token proxy/verification.
- Added OA-specific LINE Messaging API reply calls; webhook signature and
  reply access token are selected from the OA on which the event arrived.
- Added Application/Data readiness endpoints without weakening liveness.
- Replaced row-count Chann UID allocation with PostgreSQL sequences to avoid
  concurrent ID collisions.
- Added real tenant-isolation API integration coverage and identity
  create/reuse integration coverage.
- Removed the Presentation Dockerfile's build-error swallowing and switched
  to a standalone Next.js runtime image.
- Prevented environment-specific Application/LIFF values from being baked
  into a build intended for build-once promotion.
- Pinned Next.js 16.2.11, React/React DOM 19.2.8, and the fixed LIFF SDK CDN
  2.29.2. The stale Next.js 15.1.3 lockfile was removed.
- Made impact detection fail closed to `all` when its Git base is unavailable.
- Added an empty GCS backend declaration, verified VPC/connector names, and
  explicit documentation of the remaining Terraform boundary.
- Added `scripts/phase1-source-verify.sh`, which performs local-only source,
  database, container, Terraform, and manifest verification in one run.
- Inspected and adopted the exact verified-source archive from Cloud Shell,
  including the generated npm and Terraform provider lockfiles.
- Added tier `.dockerignore` policies, signal-safe Python container commands,
  and an explicit Phase 1 no-image-optimizer policy.
- Added `scripts/dev-infra-plan.sh` as a DEV-only, no-apply/no-import plan gate.
  It performs fresh read-only preflight and rejects identity/secret resources
  and delete/replace actions.

## Evidence from this environment

- Python boundary/unit/non-DB suite after verified-source hardening:
  **45 passed**.
- PostgreSQL-from-empty tests: **8 skipped here** because Docker/PostgreSQL is
  unavailable in this environment; the one-shot Cloud Shell script makes them
  mandatory using an ephemeral PostgreSQL 16 container.
- Python compile and shell syntax: **PASS**.
- Next.js 15.1.3 typecheck/build was proven before the security pin was
  changed, so it is not evidence for the current source.
- Next.js 16.2.11 install/typecheck/build: **PASS** for the uploaded
  `20260817T042423Z` source; **RERUN REQUIRED** for the subsequent no-image-
  optimizer/config hardening.
- Terraform fmt/init-without-backend/validate: **NOT_VERIFIED** locally because
  Terraform is unavailable; mandatory in the one-shot run.
- Docker image builds: **PASS** for the uploaded `20260817T042423Z` source;
  **RERUN REQUIRED** after the Docker context/start-command hardening because
  Docker is unavailable in this review environment.
- Runtime cross-tier/LINE business acceptance: **NOT_VERIFIED**.

## Cloud Shell verification follow-up

Run `20260817T041832Z` passed Docker access, dependency installation,
boundary/unit tests, PostgreSQL startup, and PostgreSQL readiness. The
database-from-empty suite then reported 7 passed / 1 failed. The failure was
isolated to the test harness reading `License` ORM attributes after a plain
SQLAlchemy `Session` had committed, expired the attributes, and closed. It was
not a migration, query-isolation, or application-runtime failure.

The test now captures primitive UUID/string identifiers before commit. Hotfix
`chann-crm-ai-phase1-hotfix-v2.sh` applies that exact change idempotently and
resumes the complete verifier.

Rerun `20260817T042423Z` completed successfully:

- boundary/unit: **35 passed**;
- migration + seed + database integration from empty PostgreSQL 16:
  **8 passed**;
- Next.js **16.2.11** dependency install, generated npm lockfile, typecheck,
  and production build: **PASS**;
- Data/Application/Presentation container builds: **PASS**;
- verified Terraform 1.15.8 download/SHA-256, fmt, init with backend disabled,
  and validate using Google provider 6.50.0: **PASS**;
- Release Manifest generation: **PASS**;
- final result: `SOURCE_VALIDATION_PASS_RUNTIME_ACCEPTANCE_NOT_VERIFIED`.

The locally built image manifest-list digests are evidence of the Cloud Shell
build only; they are not Artifact Registry release artifacts and must not be
used as deployment identifiers.

The three non-blocking build warnings observed in that run have now been
resolved in source. A new full one-shot run is required to promote those
changes from locally validated source to verified source.

Rerun `20260817T051609Z` then promoted that hardening to verified source:

- all **19/19** source checks passed;
- boundary/unit: **45 passed**;
- migration + seed + database integration from empty PostgreSQL 16:
  **8 passed**;
- Next.js typecheck/build and all three container builds: **PASS**;
- Docker contexts were reduced to approximately 32–45 KB;
- Terraform fmt/init-without-backend/validate and Release Manifest: **PASS**;
- archive content matched the submitted checkpoint exactly except for the new
  verification result file.

## DEV Terraform boundary after verified run 20260817T051609Z

The verified archive is now the canonical input for the infrastructure-source
closure. The following source-only work was added without calling GCP:

- built-in PostgreSQL app user with password input stored in Terraform state
  under the explicitly accepted reduced-security posture;
- Cloud Run v2 services for Data -> Application -> Presentation deployment
  order, using digest-only images, the existing connector, private Cloud SQL
  and Redis addresses, bounded scaling, and build-once release metadata;
- optional private/versioned GCS bucket, disabled by default in Phase 1;
- exact plan allowlist for only database, database user, Cloud Run, and the
  optional bucket;
- executable checks preserving all existing infrastructure as data sources and,
  at this checkpoint, prohibiting IAM/Service Account/Secret Manager and
  public-invoker configuration. The later approved DEV-only invocation
  exception is documented below.

Post-change local evidence:

- Terraform 1.15.8 fmt/HCL parse: **PASS**;
- provider arguments checked against pinned Google provider 6.50.0 source:
  **PASS**;
- boundary/unit suite: **49 passed**;
- full provider-backed `terraform validate`: **RERUN REQUIRED** in Cloud Shell;
  this review sandbox blocks the provider plugin's local Unix socket;
- Docker/database/runtime acceptance: unchanged from the verified baseline;
  the next one-shot run must reconfirm source and container validation.

## Blocking decisions and missing authority

1. A new app-specific Terraform-state bucket is required. Creating it is a
   cloud mutation and needs approval before execution.
2. The DEV invocation decision is resolved: the approved capability-limited
   mode disables the Cloud Run Invoker IAM check for all three services and
   retains application-layer controls. Terraform blocks that exception outside
   DEV. Stage/Production invocation remains unresolved and no IAM scope was
   added.
3. Production Artifact Registry is absent.
4. LINE channel secrets/access tokens x3 and LIFF IDs x3 remain
   `REQUIRED_NOT_CONFIGURED`.
5. CI-to-GCP authentication/push/promotion remains undecided and may require an
   explicitly approved IAM scope change.
6. SmartBrowz credentials/readiness are Phase 10 gates, not Phase 1 runtime
   dependencies. No runtime LLM/PDF path was added.
7. No Production execution is authorized. Production remains
   `REQUIRES_EXPLICIT_APPROVAL`.

## Next safe step — one set only

Upload the DEV-Terraform checkpoint and its one-shot runner to Cloud Shell
`$HOME`, then run the runner once. This remains local source validation: do not
create the backend bucket, run the DEV plan, apply/deploy, or change IAM,
Service Accounts, or Secret Manager. Return the result text and generated
verified archive for review.
