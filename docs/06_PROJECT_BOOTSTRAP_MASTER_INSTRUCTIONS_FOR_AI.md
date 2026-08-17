# Project Bootstrap Master Instructions for AI

## Purpose

Use these instructions when asking an AI coding/architecture agent to start or evolve a production-grade application using the lessons proven by the Chann1 reference project.

These instructions are intentionally technology-opinionated where Chann1 produced useful evidence, but they must be adapted when the target project has different requirements.

---

## Chann CRM AI package-specific override

When this document is used inside the Chann CRM AI package, apply these target-specific constraints before the generic guidance below:

- This is a **greenfield application on pre-existing GCP infrastructure**.
- Reuse existing Cloud SQL, Redis, network/connectors and Artifact Registry where live preflight confirms they are suitable.
- Do not preserve the old Chann1 CRM application, database schema, API contract, seed data or business model.
- The target product's four-tier architecture is locked: `Presentation -> Application -> Data -> Database`. Supporting services/integrations do not create additional business tiers.
- Resolve Terraform state/adoption for existing resources before first apply.
- `CHANN_CRM_AI_MASTER_SPEC.md` is the product source of truth; the 01-06 reference documents are delivery/architecture lessons.

---

## 1. Role and operating principle

Act as the project's architecture, infrastructure, development, testing, release, and operations copilot.

Your goal is not merely to generate code. Your goal is to maintain a coherent, executable delivery system in which architecture, tests, runtime behavior, deployment, rollback, and documentation agree.

Never claim success from design intent alone.

---

## 2. Mandatory environments

Create exactly three logical environments unless the user explicitly approves a different model:

1. DEV
2. Stage/Test
3. Production

Do not create environment-specific source forks. Build immutable artifacts once and promote the same artifact identity across environments.

Production changes require an explicit approval point.

---

## 3. Mandatory four-tier architecture

Use these logical tiers:

1. Presentation Tier
2. Application Tier
3. Data Tier
4. Database Tier

Enforce:

- Presentation -> Application only.
- Application -> Data only.
- Data -> Database/Cache.
- Presentation must never access Data, Database, or Cache directly.
- Application must never access Database or Cache directly.
- Database is the source of truth.
- Cache is an optimization only.

Add executable architecture/boundary tests so these are enforced rather than merely documented.

---

## 4. Independent tier artifacts

Presentation, Application, and Data must be independently buildable, versionable, deployable, and rollbackable.

Database is independently versioned by schema/migration state.

**Do not force every change to rebuild/redeploy every tier.**

Preserve this distinction:

- **deployment scope = smallest changed runtime set**;
- **validation scope = all dependencies that could be affected**.

---

## 5. Selective Deployment + Dependency-aware Testing

This is mandatory.

Detect changed paths and calculate impact before validating or deploying.

Default behavior:

- `presentation/**` -> test/build/deploy Presentation only, plus browser-relevant checks.
- `application/**` -> deploy Application only unless another tier changed; validate Application + Presentation + cross-tier.
- `data/**` -> deploy Data only unless another tier changed; validate Data + Application + Presentation + cross-tier.
- `database/**` -> execute migration gate; validate Database + Data + Application + Presentation + cross-tier.
- contract/shared/integration changes -> expand test scope to every affected tier.

Never equate a broad test scope with a requirement to redeploy unchanged tiers.

Provide one-command developer tools comparable to:

- `dev-validate`;
- `dev-deploy auto`;
- explicit per-tier deploy commands.

---

## 6. Developer Experience requirement

Routine development must not require a person to remember a long list of Docker/cloud commands.

Automate repeated steps in version-controlled scripts/pipelines.

A good developer loop is:

`edit -> one-command validate -> PR -> CI -> merge -> automatic/selective DEV deploy -> runtime smoke`

Keep scripts small enough to diagnose, but consolidate repetitive operator work.

---

## 7. Database standard

Prefer PostgreSQL unless requirements justify another database.

Use versioned schema migrations. Never depend on manual Production schema edits.

Use:

`Expand -> Migrate/Backfill -> Contract`

Maintain a compatibility window so a new schema supports at least the current and previous compatible Data/Application release during rollout.

Normal application rollback must not require rolling back Production data.

---

## 8. Environment initialization and seed ordering

Treat environment initialization as part of deployability.

Mandatory order:

1. schema migration;
2. idempotent reference seed;
3. environment-specific fixture/demo seed only if required;
4. runtime deployment/readiness;
5. functional acceptance.

Reference seed must include required functional reference data (for example statuses, stages, permission definitions).

Seed operations must be idempotent by business unique key as well as compatible with synthetic IDs. Do not allow a fixture to collide with a reference row that uses the same business code.

Never leave a successful environment dependent on undocumented manual SQL.

---

## 9. Cache standard

Use cache-aside unless requirements justify another strategy.

For every cached data type define:

- key;
- TTL;
- invalidation;
- authoritative source;
- failure behavior.

Authorization-related cache behavior must fail secure.

Cache outage must not create broader permissions.

---

## 10. Authentication and authorization

Separate authentication from business authorization.

Application is the business-policy boundary.

Data supplies authoritative authorization context and applies data scope; Application selects required permission/scope.

Use explicit scopes such as OWN/TEAM/ALL only if the business model requires them.

Temporary test-identity adapters must be:

- explicitly gated;
- disabled in target secure Production;
- clearly documented as test/proof mechanisms;
- excluded from claims of production-grade authentication.

Do not infer that the existence of role/permission tables proves a complete role-policy implementation. Prove the matrix with executable negative/positive tests.

---

## 11. Testing pyramid and gates

Require appropriate layers:

1. Unit tests
2. Boundary/contract tests
3. Database integration tests
4. Cross-tier HTTP integration/smoke
5. Full browser/user E2E
6. Target-environment runtime acceptance for material business capabilities

Each feature generated by AI must include tests before acceptance.

Do not hide failing tests to make CI green.

If a deliberate contract/UI change makes a test stale, update the test to the new intended behavior while preserving equivalent coverage.

Browser E2E is the authority for browser-visible behavior when raw HTML/framework streaming semantics differ.

---

## 12. Functional Definition of Done

**CI PASS is not Feature Complete. Build PASS is not Feature Complete. Deploy PASS is not Feature Complete.**

A business capability is Done only when:

- source implementation exists across required tiers;
- authorization and data constraints are represented;
- automated tests cover the capability;
- Official CI is PASS;
- migrations/reference data make the environment reproducible;
- the intended business flow is proven in the appropriate runtime environment;
- any release/operational evidence is updated.

Maintain requirement -> implementation -> test -> runtime-evidence traceability for material features.

---

## 13. CI/CD behavior

Official pipeline configuration lives in Git.

The pipeline is a process control, not a developer's memory.

At minimum validate as applicable:

- tier-specific tests;
- architecture boundaries;
- migration safety/clean migration;
- container builds;
- cross-tier integration;
- browser E2E;
- infrastructure syntax/intent;
- release manifest generation.

When CI fails, read the real failed log first. Do not guess repeatedly when diagnostics can identify the actual issue.

---

## 14. Release model

Use build-once/promote-exact-artifact semantics.

Create an immutable Release Manifest containing at minimum:

- platform version;
- Git/source commit;
- Presentation artifact identity;
- Application artifact identity;
- Data artifact identity;
- database migration head;
- compatibility metadata;
- verification/evidence status;
- known limitations/security mode where relevant.

Stage/Test must use the exact artifacts intended for Production.

Production must promote the Stage-proven artifact without rebuild.

---

## 15. Stage/Test promotion gate

Before Production approval require:

- source/release identity confirmed;
- migration PASS;
- reference seed PASS;
- readiness PASS;
- full business acceptance/E2E PASS;
- immutable artifacts recorded;
- rollback rehearsal PASS for material releases;
- limitations/deferred capabilities documented.

Do not permit a design-only or Terraform-only Stage to count as verified Stage.

---

## 16. Rollback standard

Application rollback promotes/routes a previously accepted immutable artifact combination.

Database rollback is not the normal application rollback mechanism.

Preserve database compatibility windows to permit runtime rollback.

For material releases, rehearse rollback in Stage and record:

- stable revision/artifact;
- candidate revision/artifact;
- pre-change smoke;
- rollback action;
- post-rollback smoke;
- exact limitations of what was proven.

Do not claim historical-release compatibility if the rehearsal only used identical/no-op images.

---

## 17. Production approval and verification

Production is an explicit approval point.

After approval:

1. verify Stage evidence;
2. promote exact Stage artifacts;
3. execute migrations/reference seed;
4. deploy selectively/in dependency-safe order;
5. verify readiness;
6. run safe functional acceptance/smoke;
7. record Production release evidence;
8. monitor release health.

Never silently convert a DEV/Stage security exception into a secure Production claim.

---

## 18. Observability requirement

Every runtime service must provide:

- liveness;
- readiness;
- correlation/request ID;
- structured logs;
- latency and error-rate measurement points.

Operational evidence should correlate runtime revision/artifact with a Release Manifest.

---

## 19. Backup/recovery truth model

Backup configured != recovery proven.

Recovery is PROVEN only after an isolated restore succeeds and restored data/application behavior is verified.

Record RPO/RTO assumptions separately from evidence.

Never mark restore as PASS merely because backups exist or provider documentation says restore is supported.

---

## 20. Security truth model

Distinguish:

- logical security architecture;
- implemented security controls;
- reduced-security development/proof exceptions;
- real secure Production readiness.

Never claim a lifecycle proof is a secure Production proof if authentication, secrets, workload identities, ingress, or least-privilege controls were intentionally skipped.

---

## 21. Documentation evidence states

Use explicit statuses:

- PASS / PROVEN
- PROVEN_WITH_LIMITATIONS
- NOT_VERIFIED
- NOT_VERIFIED_DEFERRED
- NOT_PROVEN_DEFERRED
- BLOCKED
- REQUIRES_EXPLICIT_APPROVAL

Reconcile old status documents against current runtime/release evidence before finalizing a project.

---

## 22. AI change-execution rules

For every requested change, the AI should:

1. inspect source of truth;
2. resolve the affected tier(s);
3. resolve downstream validation impact;
4. make the smallest coherent implementation;
5. update tests and migration/seed behavior when required;
6. run local fast validation;
7. create PR/Official CI evidence;
8. deploy only affected tiers unless dependency changes require more;
9. run runtime acceptance when material;
10. update release/operational documentation.

When a command fails:

- stop at the failure;
- classify the failure;
- preserve already-successful work;
- resume from the smallest safe point;
- do not restart the entire workflow unless necessary.

Prefer idempotent/resumable automation for long environment operations.

---

## 23. Guardrails derived from Chann1

The following are mandatory lessons:

- Architecture tests are valuable but cannot substitute for user-facing functional acceptance.
- Health endpoints can be green while business reference data is missing.
- Environment seed/reference data are deployment dependencies.
- Unique business keys must guide seed idempotency.
- Framework typing/signature changes can break runtime behavior even when static intent looks correct.
- Stale tests must be reconciled after intentional API/UI changes.
- Frontend async event objects must not be assumed stable after `await`; retain needed references before async boundaries.
- Test roles must include all permissions required by the acceptance flow; do not confuse a read-only vertical slice with functional completeness.
- Selective deployment improves speed; dependency-aware testing protects safety.
- Build-once promotion must be verified by artifact identity, not assumed.
- Rollback mechanics and database recovery are separate capabilities and must be proven separately.
- Reduced-security exceptions must be prominently documented and never normalized into target Production design.

---

## 24. Bootstrap deliverables for a new project

At project start, produce and maintain:

- architecture boundary document;
- environment topology;
- repository/tier structure;
- database migration framework;
- cache contract;
- local developer validation/deploy automation;
- Official CI workflows;
- integration/E2E harness;
- Release Manifest schema/generator;
- rollback/recovery runbook;
- observability contract;
- security/environment model;
- evidence/status report.

Do not wait until project closure to create the evidence model; make it part of the delivery system from the beginning.
