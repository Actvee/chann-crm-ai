# Chann1 Reference Architecture & Infrastructure Blueprint

## 1. Purpose

Chann1 is a Python-first reference project intended to prove a production-like application architecture and delivery lifecycle. The architectural goal is not maximum scale; it is a reusable structure that keeps presentation, business policy, data access, and schema evolution independently understandable, testable, deployable, and rollback-aware.

Document baseline: `76a66bc964c9a51a6a2859771540ea05b9fec4ea`.

## 2. Environment model

Chann1 uses exactly three logical environments:

- **DEV** — rapid functional verification and developer feedback.
- **Stage/Test** — production-like promotion gate using immutable artifacts intended for Production.
- **Production** — explicit-approval deployment target.

The environment model follows **build once, promote exact artifact**. Environment-specific source variants are not allowed. Runtime configuration varies by environment, while source and container artifacts remain immutable.

### Runtime proof status

| Environment | Runtime status | Functional status | Security interpretation |
|---|---|---|---|
| DEV | PROVEN | Contact, Deal, Note, Follow-up functional flow proven | Reduced-security developer/demo mode |
| Stage/Test | PROVEN | Functional E2E PASS; readiness PASS | Reference-project Stage proof; test identity used for proof |
| Production | PROVEN_WITH_LIMITATIONS | Readiness and functional E2E PASS | `PRODUCTION_PROOF_REDUCED_SECURITY`, not target secure Production |

## 3. Mandatory four-tier architecture

### 3.1 Presentation Tier

Responsibilities:

- browser/user experience;
- client-side and presentation validation;
- calling only the Application API;
- rendering business data returned by Application.

Current implementation: Next.js App Router, React, TypeScript, containerized standalone build.

Forbidden dependencies:

- Presentation -> Data direct calls;
- Presentation -> PostgreSQL;
- Presentation -> Redis/cache.

### 3.2 Application Tier

Responsibilities:

- external API boundary under `/api/v1`;
- business permission selection;
- authorization scope decision using the authorization context returned by Data;
- request/correlation ID propagation;
- calling Data only through the versioned internal HTTP API.

Current implementation: Python 3.12 + FastAPI + HTTP Data client.

Forbidden dependencies:

- direct SQL/PostgreSQL access;
- direct Redis/cache access;
- schema migration execution during application startup.

### 3.3 Data Tier

Responsibilities:

- sole runtime owner of PostgreSQL and Redis access;
- transaction boundary;
- authorized data-scope enforcement (`OWN`, `TEAM`, `ALL`);
- cache-aside behavior;
- internal API under `/internal/v1`.

Current implementation: Python 3.12, FastAPI, SQLAlchemy, psycopg, Redis client.

Data must never broaden the scope supplied by Application.

### 3.4 Database Tier

Responsibilities:

- PostgreSQL source of truth;
- normalized role/permission and business persistence;
- versioned schema evolution through Alembic;
- referential/check constraints;
- compatibility support for application rollback.

Baseline schema entities include teams, users, roles, permissions, role_permissions, contacts, deal_stages, deals, notes, follow_ups, and audit_events.

## 4. Request and dependency flow

Normal business request path:

`Browser -> Presentation -> Application -> Data -> PostgreSQL`

Redis is a Data Tier optimization/dependency, not an authoritative store.

Authorization path:

`Application -> Data authorization context -> Application permission decision -> Data scoped query`

The dependency direction is intentionally one-way. Higher tiers must not bypass lower-tier policy boundaries.

## 5. Business capability status

The database model represents a broader domain than the currently proven application behavior. Final documentation must distinguish schema presence from executable capability.

### Proven functional baseline

- Contact list/create/update/archive endpoints exist; DEV/Stage/Production proof includes Contact creation.
- Deal list/create/update/archive and stage changes exist; Stage and Production proof include create, WON, and reopen to NEW.
- Notes read/create/update endpoints exist; Stage/Production proof includes create.
- Follow-up read/create/update endpoints exist; Stage/Production proof includes create and complete; UI also exposes cancel.
- Permission/scope enforcement exists at Application and scoped filtering exists in Data.

### Not fully proven against the original capability matrix

- `Deal reopen` restricted specifically to Manager/Admin, explicit reopen reason, and audited reopen event: **NOT_VERIFIED**.
- Complete Sales/Manager/Admin behavior matrix across OWN/TEAM/ALL: **NOT_VERIFIED**. The functional proof uses an `E2E_SALES` proof role.
- User/role/permission administration UI/API boundary: **NOT_VERIFIED**.
- Immutable application-level audit event emission for material changes: schema exists, but full runtime behavior is **NOT_VERIFIED**.
- Overdue derivation/presentation for follow-ups: **NOT_VERIFIED**.

## 6. Database and cache architecture

PostgreSQL is always the source of truth. Redis uses cache-aside with explicit TTL/invalidation semantics. Authorization is the initial reference cache use case.

Security-sensitive cache behavior must fail secure. A cache outage may degrade performance, but must not create broader permissions or bypass database authorization state.

## 7. Infrastructure topology

The reference cloud topology demonstrated on GCP uses:

- Cloud Run for Presentation, Application, and Data runtimes;
- Cloud SQL for PostgreSQL;
- Memorystore/Redis for Data Tier caching;
- Artifact Registry for immutable container images;
- VPC connectivity for private-range cache access;
- GitHub Actions as Official CI.

Terraform in the repository represents desired-state infrastructure intent. Infrastructure is considered proven only where runtime deployment evidence exists; Terraform validation alone is not runtime proof.

## 8. Independent deployability

Presentation, Application, and Data are independently versioned container artifacts. Database is independently versioned through migration/schema state.

A change to one tier does **not** require all application tiers to be rebuilt or redeployed. The delivery model therefore separates:

- **deployment scope** — the smallest set of runtime tiers actually changed;
- **validation scope** — the full dependency impact that must be tested.

Example: a Data change may deploy only Data, while validation expands through Application, Presentation, and cross-tier tests.

## 9. Release identity

Every accepted platform release must have an immutable Release Manifest that identifies at minimum:

- platform version;
- Git/source commit;
- Presentation artifact identity;
- Application artifact identity;
- Data artifact identity;
- database migration head;
- environment verification status;
- compatibility/limitations metadata.

Stage proof for version 0.2.0 used source commit `d1bc752123ba885cd5535c531616f11c0bed0586`. Production proof promoted the Stage-proven images without rebuild.

## 10. Rollback architecture

Normal rollback is an application/runtime action, not a production-data rollback action.

- Roll back Presentation/Application/Data by routing/promoting a previously accepted immutable revision/artifact combination.
- Preserve database backward compatibility through Expand -> Migrate/Backfill -> Contract.
- Do not depend on destructive reverse migrations as the primary rollback mechanism.

Stage evidence proves Cloud Run traffic rollback mechanics using rehearsal revisions made from the same immutable images. It does **not** prove compatibility with a materially different historical release; that remains a future compatibility test requirement.

## 11. Operational evidence and remaining gaps

### Proven

- clean schema migration in CI;
- cross-tier HTTP path in CI;
- browser E2E in CI;
- DEV runtime functional usage;
- Stage readiness and functional E2E;
- Stage revision traffic rollback and post-rollback smoke;
- Production lifecycle promotion without rebuild;
- Production readiness and functional E2E under reduced-security proof mode.

### Deferred / not proven

- isolated Cloud SQL restore drill: **NOT_PROVEN_DEFERRED**;
- production-grade identity provider integration: **NOT_VERIFIED**;
- production-grade IAM/service-account/secret handling: intentionally outside this project proof and therefore **NOT_VERIFIED**;
- HA/failover and load/capacity characteristics: **NOT_VERIFIED** unless separate evidence is added.

## 12. Architecture rule for future projects

A reference architecture is complete only when its boundaries are enforced by executable tests and its operational claims are backed by runtime evidence. Design intent, diagrams, Terraform validation, successful image builds, or successful deployment commands alone are not sufficient evidence of business readiness.
