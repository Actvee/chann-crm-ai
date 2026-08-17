# Chann1 Final Reference Project Report

## Executive summary

Chann1 began as a production-like architecture/reference exercise and evolved into a functioning CRM reference application with a demonstrated release lifecycle. The final source baseline for this package is `76a66bc964c9a51a6a2859771540ea05b9fec4ea`.

The project successfully proved:

- a four-tier architecture with executable boundary tests;
- Contact, Deal, Note, and Follow-up runtime functionality;
- automated cross-tier/browser E2E;
- selective tier deployment and dependency-aware validation principles;
- schema migration plus reference/environment seed ordering;
- DEV runtime operation;
- Stage/Test readiness and functional E2E;
- Stage Cloud Run traffic rollback rehearsal;
- build-once/promote-without-rebuild into the Production lifecycle proof;
- Production readiness and functional E2E under `PRODUCTION_PROOF_REDUCED_SECURITY`.

The project did **not** prove a complete secure Production identity/IAM/secret architecture, isolated Cloud SQL restore, or every business capability originally listed in the capability matrix.

## 1. Original project intent

Chann1 was created as a reference project for learning and codifying infrastructure, architecture, development, testing, release, and operations practices that can be reused when starting a larger real Production project.

The architecture centered on:

- DEV, Stage/Test, Production;
- Presentation, Application, Data, Database tiers;
- PostgreSQL source of truth;
- Redis cache-aside;
- versioned migrations;
- immutable artifacts and Release Manifests;
- CI/E2E gates;
- rollback without routine production-data rollback.

## 2. What changed during implementation

The first architecture package reached a technically valid vertical slice, but it did not yet satisfy the intended product usability. The UI could list Contacts, while create/update business flows and Deals/Notes/Follow-ups were incomplete.

A Functional Completion exception was therefore used to implement the missing usable CRM baseline quickly. After the functional baseline was stabilized and merged, the project returned to the normal PR/CI/release process.

This experience produced a key project rule:

> Architecture completion and CI completion are not product completion. A business feature is complete only after the intended business flow is executable in the target environment.

## 3. Final functional implementation

### Contact

Implemented API behavior includes list, create, update, and archive. Runtime proof includes Contact creation in Stage and Production proof.

### Deal

Implemented behavior includes list, create, update, archive, stage transition, closed outcomes, and reopen through the `deal.reopen` permission. Stage/Production proof includes create, WON, and reopen.

### Notes

Read/create/update endpoints exist. Runtime proof includes create.

### Follow-ups

Read/create/update endpoints exist. Database status supports PENDING/COMPLETED/CANCELLED. Runtime proof includes create and complete; UI supports cancel.

### Authorization

Permission-based authorization and OWN/TEAM/ALL data scope architecture are implemented. The proof uses an E2E role; the complete Sales/Manager/Admin policy matrix is not fully proven.

## 4. Business capability gap analysis

Against `docs/business/CAPABILITY_MATRIX.md`:

| Capability | Final status | Notes |
|---|---|---|
| Contact CRUD/archive | PROVEN | API implemented; create proven in Stage/Production; UI operational |
| Ownership and OWN/TEAM/ALL architecture | PARTIALLY_PROVEN | filtering/permission architecture exists; complete role matrix not exhaustively proven |
| Deal create/update/stages/WON/LOST | PROVEN/PARTIAL | create and WON/reopen proven; LOST path implemented but not part of final runtime evidence |
| Deal reopen Manager/Admin + reason + audit | NOT_VERIFIED | `deal.reopen` exists; role restriction/reason/audit policy not fully implemented/proven |
| Notes | PROVEN/PARTIAL | create proven; read/update endpoints exist |
| Follow-ups | PROVEN/PARTIAL | create/complete proven; cancel available in UI/API model; overdue derivation not proven |
| Sales/Manager/Admin authorization matrix | NOT_VERIFIED | proof role is `E2E_SALES` |
| User/role/permission administration | NOT_VERIFIED | schema/permissions exist; management capability not proven |
| Immutable audit trail behavior | NOT_VERIFIED | `audit_events` schema exists; runtime event emission not proven |

## 5. DEV findings and lessons

The DEV implementation exposed several issues that were invisible at the architecture-only level:

- local Python dependencies were not guaranteed in Cloud Shell;
- PostgreSQL integration tests should not be treated as local fast tests when no local PostgreSQL exists;
- Application readiness lost its FastAPI `Request` typing and caused a 422-style query-parameter failure in cross-tier CI;
- API response contracts changed and stale smoke tests expected the old `data.items` shape;
- browser E2E still expected the old `Contacts` heading after the CRM dashboard replaced it;
- Presentation form submission used `event.currentTarget` after an `await`, causing a null reset failure;
- DEV proof role initially had only `contact.read`, so write operations failed authorization;
- Deal creation failed when reference `deal_stages` were absent.

These were converted into process rules instead of being left as one-off manual fixes.

## 6. Developer Experience improvement

The repository added:

- `scripts/chann1-detect-impact.sh`;
- `scripts/chann1-dev-validate.sh`;
- `scripts/chann1-dev-deploy.sh`.

The main principle is **Selective Deployment + Dependency-aware Testing**.

A developer can change only one tier and deploy only that tier, while validation expands to dependent consumers when needed. This reduces routine manual work without weakening architecture gates.

## 7. Stage/Test proof

Stage Release Manifest: `release/manifests/0.2.0-stage-d1bc752123ba.json`.

Source commit: `d1bc752123ba885cd5535c531616f11c0bed0586`.

Stage evidence records PASS for:

- readiness;
- Contact create;
- Deal create;
- Note create;
- Follow-up create/complete;
- Deal WON/reopen;
- full functional E2E.

A Stage deployment issue revealed that schema migration alone did not initialize required Deal stages. The deployment process was corrected to require:

`migration -> reference seed -> environment seed -> runtime acceptance`.

## 8. Stage rollback proof

Evidence: `release/evidence/stage-rollback-20260813073114.json`.

The rehearsal proved:

- pre-rehearsal smoke PASS;
- candidate/no-op rehearsal revisions PASS;
- traffic switched back to captured stable revisions;
- post-rollback runtime smoke PASS.

Limitations:

- no database rollback was performed;
- the rehearsal used identical immutable images to prove traffic-switch mechanics;
- compatibility with a materially different historical application release was not proven.

## 9. Production lifecycle proof

Manifest: `release/manifests/0.2.0-production-proof-d1bc752123ba.json`.

The Production proof promoted the same Stage images without rebuild and records:

- build-once promotion PASS;
- Production readiness PASS;
- Production functional E2E PASS;
- Production change explicitly approved and executed.

Security mode is explicitly `PRODUCTION_PROOF_REDUCED_SECURITY`.

The manifest records these limitations:

- IAM/Secret Manager excluded;
- Cloud Run services public for proof;
- database credentials injected into runtime environment variables;
- test identity header enabled.

Therefore the result proves the **release lifecycle**, not production-grade security.

## 10. Database and seed lesson

The project discovered two separate reproducibility failures:

1. required reference data was not always seeded before runtime acceptance;
2. environment permission seed initially conflicted with reference permission unique codes.

The final process requires idempotent reference seed by business key and environment fixtures that coexist safely with reference data.

This is now considered part of deployability, not optional test setup.

## 11. Release and operations result

### Proven

- tier CI;
- cross-tier HTTP and browser E2E;
- build-once promotion semantics;
- Stage Release Manifest;
- Stage functional proof;
- Stage rollback traffic rehearsal;
- Production lifecycle promotion and functional proof;
- developer selective-deploy automation.

### Not proven/deferred

- isolated Cloud SQL restore drill: **NOT_PROVEN_DEFERRED**;
- production-grade IAM/service-account and secret-management model: **NOT_VERIFIED** by project constraint;
- external IdP production authentication: **NOT_VERIFIED**;
- full Manager/Admin/user-administration/audit capability: **NOT_VERIFIED**;
- HA failover, load, capacity, and formal RPO/RTO: **NOT_VERIFIED** unless separate evidence is added.

## 12. Most important lessons converted into guardrails

1. **Functional requirement traceability must reach an executable acceptance test.**
2. **Deployment success is not application readiness.**
3. **Readiness is not business-function completion.**
4. **Schema migration is not complete environment initialization; reference data is a deployment dependency.**
5. **Seed operations must be idempotent by business uniqueness, not only synthetic IDs.**
6. **Tests must evolve with intentional contract/UI changes, while preserving coverage.**
7. **Selective deployment should reduce work, while dependency-aware testing preserves safety.**
8. **Build once and promote the exact Stage-proven artifact to Production.**
9. **Application rollback should not require normal production-data rollback.**
10. **Runtime evidence must distinguish secure target design from reduced-security proof exceptions.**
11. **Backup existence is not restore proof.**
12. **Documentation status must be reconciled against the latest executable/runtime evidence before project closure.**

## 13. Final conclusion

Chann1 is successful as a reference project because it produced more than a code skeleton. It now contains a functioning CRM baseline, enforced architectural boundaries, executable CI/E2E gates, environment deployment evidence, Stage rollback evidence, build-once Production promotion evidence, and reusable developer/release rules.

Its remaining gaps are deliberately visible rather than hidden. The project should be reused as a set of principles, guardrails, automation patterns, and evidence standards—not copied wholesale as a secure Production implementation.
