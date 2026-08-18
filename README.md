# Chann CRM AI

Multi-tenant SaaS CRM + Field Service platform on LINE.
Greenfield application on existing GCP infrastructure.

**Phase 1 DEV is accepted. Phase 2 — Permission Matrix & license_settings is
implemented in source and remains not Done until source validation, deployment
and DEV runtime business acceptance pass.**
See [Evidence state](#evidence-state) for exactly what is and is not proven.

---

## Architecture

Four logical tiers, locked. Supporting services (Redis, GCS, SmartBrowz, LINE,
OpenRouter, payment, cron) are integrations, **not** a fifth tier.

```
LINE (3 platform-level OAs) ──webhook / LIFF ID Token──┐
                                                        ▼
                        Presentation (Next.js)   presentation/
                                                        │ /api/v1
                                                        ▼
                        Application (FastAPI)    application/
                                                        │ internal HTTP
                                                        ▼
                        Data (FastAPI+SQLAlchemy) data/
                                                        ▼
                        Database (PostgreSQL)     database/
```

The boundary is enforced by `tests/boundary/`, which reads the source rather
than the running app — a runtime test only catches a violation on the code
path that happens to execute.

| Rule | Enforced by |
|---|---|
| Application must not import SQLAlchemy / psycopg / redis | `test_application_does_not_access_persistence_directly` |
| Presentation must not know the Data Tier exists | `test_presentation_only_talks_to_the_application_tier` |
| Data is the only tier owning persistence | `test_data_tier_owns_persistence` |
| PDF vendor SDK stays behind `PdfRenderer` | `test_domain_code_does_not_import_a_pdf_vendor_sdk` |

## Layout

```
├── presentation/     Next.js
├── application/      FastAPI — webhooks, AI orchestration, domain services
├── data/             FastAPI + SQLAlchemy — the only tier touching PG/Redis
├── database/         Alembic migrations + idempotent reference seed
├── tests/            boundary / unit / integration
├── scripts/          preflight, impact detection, validation, deploy, manifest
├── infrastructure/   Terraform (adoption gate — read before applying)
└── docs/             handoff package; CHANN_CRM_AI_MASTER_SPEC.md is the
                      single Product Source of Truth
```

## Quick start

```bash
cp .env.example .env
docker compose up --build -d

export DATABASE_URL=postgresql+psycopg://chann:chann@localhost:5432/chann_crm_ai
make migrate && make seed     # migration BEFORE seed — the order is mandatory
make test
```

Environment initialisation order is not a style preference:

```
migration → idempotent reference seed → environment fixture → runtime → acceptance
```

The reference project lost time to exactly this: schema migration succeeded,
reference data was missing, and record creation failed at runtime.

## Evidence state

Vocabulary from `CLAUDE.md` section 10. **CI green is not Feature Complete.**

| Capability | State |
|---|---|
| 4-tier boundary enforcement | `PROVEN_LOCAL` — boundary suite passes |
| Multi-tenant isolation (unit) | `PROVEN_LOCAL` — scope refuses foreign tenants |
| Cache fail-secure (ADR-006) | `PROVEN` — outage never widens permission |
| LINE signature verification | `PROVEN` — incl. cross-OA replay rejection |
| Platform Admin auth (argon2 + revocable Redis session) | `PROVEN_LOCAL` |
| Phase 1 DEV runtime business acceptance | `PROVEN` — LINE reply + valid LIFF, 3/3 OAs |
| Phase 2 permission matrix / settings / ownership transfer | `SOURCE_IMPLEMENTED_NOT_VERIFIED` |
| Migration from empty database | `NOT_VERIFIED` — needs `TEST_DATABASE_URL`; runs in CI |
| Seed idempotency | `NOT_VERIFIED` — same |
| Cross-tier HTTP smoke | `NOT_VERIFIED` |
| Browser E2E | `NOT_VERIFIED` |
| Phase 2 DEV runtime acceptance | `NOT_VERIFIED` |
| Stage / Production | `REQUIRES_EXPLICIT_APPROVAL` |
| GCP infrastructure inventory | `PROVEN` — read-only discovery 2026-08-17 |
| Terraform state ownership | `PROVEN_WITH_LIMITATIONS` — no backend bucket/state available; existing persistent resources remain reference-only |
| Backup / restore drill | `NOT_PROVEN_DEFERRED` |

## Known limitations — stated plainly

1. **Reduced-security posture, by decision.** Runtime secrets travel as
   environment variables. No Secret Manager, no per-service IAM. Internal
   endpoints use a shared-secret header. This is **not** a secure production
   security model and must never be described as one.
2. **Restore is unproven.** Backups being configured is not recovery being
   proven.
3. **PDF rendering leaves GCP.** From Phase 10, document data is sent to Zoho
   Catalyst SmartBrowz, an external processor (ADR-021, superseding ADR-007).
   Accepted by the project owner on 17 Aug 2026.

Every Release Manifest carries these automatically —
`scripts/release-manifest.py` injects them rather than trusting a human to
remember.

## Current gates

| Item | Blocked on |
|---|---|
| CI push / promotion | GCP auth method undecided (WIF vs SA key vs Cloud Build) |
| Phase 2 prompt-to-permission AI interpretation | Phase 4 OpenRouter adapter; Phase 2 fails closed unless explicit permission keys are supplied |
| Phase 2 closure | Source validation, migration/seed plan, selective DEV deploy and runtime role/settings/transfer acceptance |
| Production anything | Explicit owner approval, per request |

## Deviations from the handoff package

Each was a judgement call; reverse any of them if you disagree.

1. **Tier packages renamed** `app` → `chann_data` / `chann_app`. Two packages
   both named `app` shadow each other on `sys.path`, which breaks cross-tier
   testing outright.
2. **Seed refuses the known bootstrap password outside DEV.** The spec sketches
   `changeme123`; the Platform Admin is the break-glass account with
   cross-tenant reach, so a failed seed beats a publicly known credential.
3. **Preflight rewritten.** The original swallowed every error and always
   exited 0 — a gate that cannot fail. It now separates hard requirements from
   probes and writes JSON evidence.
4. **Terraform uses `data` sources** for existing infrastructure rather than
   importing it, so a mistaken plan cannot destroy Production Cloud SQL.
5. **Release manifests reject tag-pinned artifacts.** "Promote the exact
   Stage-proven artifact" is only real if the pin is immutable.
6. **PDF seam exists in Phase 1** with a null implementation. The engine was
   swapped once already; the next swap should cost one class.
7. **Platform Admin JWTs are revocable.** Login creates an authoritative Redis
   session; cache miss/outage fails closed and requires login again.
8. **LINE webhook sends a real reply.** The response body remains observable
   for acceptance evidence, but the handler also calls the OA-specific LINE
   reply API using the access token for the OA on which the event arrived.

## One-shot source verification

Run from the repository root in Cloud Shell:

```bash
./scripts/phase2-source-verify.sh
```

It runs boundary/unit tests, an empty-PostgreSQL migration + idempotent seed,
tenant-isolation API tests, Next.js typecheck/build, all three Docker builds,
Terraform formatting/validation with the backend disabled, and Release
Manifest generation. It does not call `gcloud`, plan/apply Terraform, deploy,
or mutate any cloud resource. A PASS still leaves runtime business acceptance
`NOT_VERIFIED`.

On success it also creates `chann-crm-ai-phase2-source-verified-*.zip` beside
the repository, including the newly generated npm lockfile and verification
report but excluding local build caches.

The reviewed archive pins Next.js `16.2.11` and React `19.2.8`, including the
matching lockfile. Cloud Shell must still prove `npm ci`, typecheck and build;
the presence of a lockfile is dependency identity, not execution evidence.

## Open decisions

- ADR-010 says anyone may promote to Production with no approval gate;
  `CLAUDE.md` section 10, playbook 02 section 14 and the owner all say the
  opposite. Recommend **ADR-020 superseding ADR-010**.
- Phase 1 acceptance requires a Production deploy, which cannot happen before
  approval. Recommend closing Phase 1 at DEV + Stage and marking Production
  `REQUIRES_EXPLICIT_APPROVAL`.
- Production Artifact Registry may not exist. Recommend promoting Stage images
  directly, since copying across repositories breaks digest immutability.
