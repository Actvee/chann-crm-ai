# Package Adjustment Changelog

## Updated for greenfield-on-existing-infrastructure handoff

Changes made to remove ambiguity before handing the project to a new AI:

1. Added `00_START_HERE.md` as the canonical entry point.
2. Added `00_INFRASTRUCTURE_HANDOFF.md` with the known clean infrastructure baseline.
3. Added `ENVIRONMENT_RESOURCE_MAP.yaml` for DEV/Stage/Production mapping.
4. Added `RUNTIME_CONFIG_CONTRACT.md` without real secrets.
5. Added read-only `scripts/infra-preflight.sh`.
6. Rewrote `CLAUDE.md` so it no longer references source files that are absent from this ZIP.
7. Declared `CHANN_CRM_AI_MASTER_SPEC.md` the single Product Source of Truth.
8. Added explicit rule that Phase 16.5/17.5 and Permission Keys corrections are already integrated and must not be merged twice.
9. Added greenfield rule: do not preserve old Chann1 CRM code/API/schema/seed compatibility.
10. Clarified that 4-tier remains the logical architecture while Redis/GCS/Carbone/LINE/OpenRouter/payment are supporting infrastructure/integrations, not new tiers.
11. Added Terraform adoption/state gate for existing GCP resources.
12. Adjusted Phase 1 infrastructure requirement in the Master Spec from creating Cloud SQL/Memorystore/Artifact Registry to reusing/adopting existing resources where live preflight confirms them.
13. Removed Phase 1 Service Account work from the active infrastructure requirement to match the current no-IAM/Service-Account scope.
14. Corrected the embedded Requirements status so it no longer says only Phase 1-10 are ready.


## Final SmartBrowz update — 2026-08-17

1. Replaced Carbone target document renderer with Zoho Catalyst SmartBrowz.
2. Locked DOCX as user authoring input, not runtime rendering format.
3. Added AI-assisted authoring flow with Intermediate Template Model, mapping, preview, approval and immutable versions.
4. Locked runtime PDF generation as deterministic and AI-free.
5. Added generic `document_templates`, `document_template_versions`, and `generated_documents` model in Phase 10.
6. Set v1 renderer adapter baseline to application-managed HTML -> SmartBrowz PDF conversion.
7. Made SmartBrowz predefined-template-ID mode optional until a supported automated management path is verified.
8. Added SmartBrowz external integration readiness/authentication gate for GCP Application.
9. Added `SMARTBROWZ_DOCUMENT_ENGINE.md`.
10. Added `GPT_NEW_CHAT_START_PROMPT.md` for clean continuation in a new chat inside the same ChatGPT Project.

## Phase 1 verified-source hardening — 2026-08-17

1. Imported the npm and Terraform provider lockfiles produced by the successful
   Cloud Shell verification run `20260817T042423Z`.
2. Added tier-local `.dockerignore` files so generated dependency/build trees
   cannot inflate or overwrite deterministic container build contexts.
3. Converted Python container startup to JSON-form commands with an explicit
   `exec` handoff for Cloud Run termination signals.
4. Explicitly disabled Next image optimization for Phase 1, which does not use
   it, so optional Sharp install scripts are not a runtime dependency.
5. Added executable boundary tests for container and infrastructure policy.
6. Added a DEV-only Terraform plan gate that always runs fresh read-only
   preflight and rejects IAM, Service Account, Secret Manager, delete, and
   replace actions. It contains no apply/import path.

## Phase 1 DEV Terraform boundary — 2026-08-17

1. Adopted verified source run `20260817T051609Z` as the new canonical source
   baseline (19/19 checks, 45 boundary/unit tests, 8 database tests).
2. Added the built-in PostgreSQL application user and explicit `ABANDON` plus
   `prevent_destroy` safety for the new database/user.
3. Added digest-only Cloud Run definitions for Presentation, Application, and
   Data, reusing the existing VPC connector and private Cloud SQL/Redis path.
4. Added bounded scaling, build-once release identity, exact runtime config
   mapping, and preconditions for required Phase 1 values.
5. Added an optional private/versioned GCS bucket that remains disabled until
   a file phase needs it and preflight proves the selected name absent.
6. Kept IAM policies, Service Accounts, Secret Manager, `allUsers`, and
   `invoker_iam_disabled` outside the Terraform source. Public invocation
   remains an explicit blocking decision.
7. Strengthened the DEV plan policy with an exact managed-resource allowlist
   and mandatory Cloud Run/database plan-address checks.

## Phase 1 DEV reduced-security invocation approval — 2026-08-17

1. Added an explicit, default-off Terraform input for disabling the Cloud Run
   Invoker IAM check without creating or changing an IAM policy.
2. Enabled the exception only in the DEV example and added a lifecycle
   precondition that blocks it outside DEV.
3. Required all three planned DEV Cloud Run services to show the approved
   invocation setting before the plan gate can pass.
4. Preserved application-layer LINE signature, JWT, and internal shared-secret
   controls and documented that this is not a Production security proof.

## Phase 1 DEV Terraform plan evidence hardening — 2026-08-17

1. Set a private `umask 077` before creating DEV plan artifacts.
2. Delete raw Terraform plan JSON after policy evaluation because it can carry
   sensitive values even when the console rendering redacts them.
3. Emit a value-free policy summary containing only resource addresses, types,
   actions, counts, and the verified DEV invocation-mode addresses.
4. Retain the binary plan only as a local `0600` artifact and mark it explicitly
   as sensitive and prohibited from upload.

## Phase 1 DEV Terraform LIFF evidence hardening — 2026-08-17

1. Marked `liff_ids` as a sensitive Terraform input so human-readable plan
   evidence cannot expose environment identifiers.
2. Extended the boundary contract so future changes cannot silently remove this
   protection.

## Phase 1 LINE Login audience correction — 2026-08-17

1. Separated full LIFF app IDs used by Presentation `liff.init()` from the
   LINE Login Channel ID required as `client_id` by server-side ID-token
   verification.
2. Kept customer, sales, and technician routing as application authorization
   context; an ID token's `aud` proves the LINE Login channel, not a specific
   LIFF app inside that channel.
3. Added a sensitive Terraform input and boundary coverage for the expected
   LINE Login Channel ID.
