# First Task for a New AI Agent

Do not write application code and do not change cloud resources yet.

Your first task is to establish a verified starting baseline for Chann CRM AI.

## Inputs

Read the documentation in the order defined by `00_START_HERE.md` and `CLAUDE.md`.

Treat `CHANN_CRM_AI_MASTER_SPEC.md` as the only Product Source of Truth.

## Step 1 — Read-only infrastructure discovery

Run:

```bash
./scripts/infra-preflight.sh
```

Do not inspect IAM or Secret Manager.

## Step 2 — Produce a Phase 1 Readiness Report

Report:

1. live DEV/Stage/Production infrastructure found;
2. resources that can be reused;
3. any documented resource that is missing;
4. legacy Artifact Registry packages that still exist but are non-authoritative;
5. Terraform state/adoption classification for every persistent resource;
6. new application DB/database-user strategy;
7. required external configuration still `REQUIRED_NOT_CONFIGURED`;
8. decisions still `DECISION_REQUIRED`;
9. Phase 1 tier/file/test plan;
10. SmartBrowz integration readiness (`READY`, `NEEDS_CONFIGURATION`, or `NOT_VERIFIED`), including the selected external-call authentication path;
11. destructive or Production changes that would require approval.

## Step 3 — Architecture confirmation

Confirm that the implementation plan preserves:

`Presentation -> Application -> Data -> Database`

and that Redis/GCS/Zoho Catalyst SmartBrowz/LINE/OpenRouter/payment/cron are supporting services or integrations rather than new logical tiers.

## Step 4 — Stop for plan review

Do not run `terraform apply`, create Production resources, or deploy Production in this first task.

After the readiness report is accepted, begin Phase 1 with DEV first and follow the acceptance/evidence requirements in the Master Spec.
