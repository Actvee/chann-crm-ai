# Chann1 Development / Testing / Release / Operations Playbook

## 1. Purpose

This playbook converts the Chann1 implementation experience into the normal process for future changes. The goal is to preserve strong gates while eliminating unnecessary manual Docker/gcloud work from routine development.

## 2. Normal change lifecycle

Use this default lifecycle:

`Requirement -> Impact analysis -> Feature branch -> Implementation -> Local validation -> PR -> Official CI -> Merge -> Immutable artifact -> DEV -> Stage/Test -> Production approval -> Production -> Verification -> Evidence`

Do not use the Functional Completion fast-track as the normal workflow. It was an explicit exception used to close a large initial implementation gap.

## 3. Source control rules

- Git `main` is the source of truth.
- Use short-lived feature/fix branches.
- Do not treat uncommitted local work as release evidence.
- Every significant change must be represented by a commit and PR.
- Generated files (`*.egg-info`, `.next`, `*.tsbuildinfo`, build-mutated Next config files when unintended) must not contaminate commits.
- If the working tree is unexpectedly dirty, stop and identify generated versus intentional changes before proceeding.

## 4. Impact analysis: selective deployment, dependency-aware testing

### 4.1 Core distinction

**Deployment scope** and **test scope** are different.

Deployment should be selective; validation must expand when a dependency can be affected.

### 4.2 Default impact map

| Changed path | Minimum deployment scope | Validation expansion |
|---|---|---|
| `presentation/**` | Presentation | Presentation tests/build; browser-facing checks |
| `application/**` | Application | Application + Presentation + cross-tier |
| `data/**` | Data | Data + Application + Presentation + cross-tier |
| `database/**` | Database migration | Database + Data + Application + Presentation + cross-tier |
| `integration/**`, contract/shared behavior | Depends on implementation | Cross-tier plus all affected tiers |

The repository provides `scripts/chann1-detect-impact.sh` as the reference detector.

### 4.3 One-command validation

Use:

```bash
scripts/chann1-dev-validate.sh origin/main
```

The validation script must remain a convenience layer over real tier tests; it must not weaken or replace Official CI.

### 4.4 Selective DEV deployment

Use:

```bash
scripts/chann1-dev-deploy.sh auto origin/main
```

or explicitly:

```bash
scripts/chann1-dev-deploy.sh presentation
scripts/chann1-dev-deploy.sh application
scripts/chann1-dev-deploy.sh data
scripts/chann1-dev-deploy.sh database
scripts/chann1-dev-deploy.sh all
```

Do not rebuild unchanged tiers merely for convenience.

## 5. Testing model

Use layered testing. Each layer answers a different question.

### Unit tests

Verify local behavior, permission helpers, transformations, and service logic.

### Boundary/contract tests

Verify architectural dependencies and API/schema contracts. Examples include Presentation not importing lower tiers and Application not accessing PostgreSQL/Redis.

### Database integration

Run clean migrations against real PostgreSQL in CI. Verify schema constraints and reference data.

### Cross-tier HTTP smoke

Verify service-to-service behavior through the real tier boundaries. Keep raw HTTP assertions focused on API/runtime behavior, not browser-rendered equivalence.

### Browser E2E

Use Playwright (or equivalent) as the authority for browser-visible behavior. The Chann1 experience showed that stale UI assertions and raw HTML assumptions can become misleading after UI architecture changes.

### Runtime environment acceptance

Deployment is not completion. After deployment, verify the real business flow in the target environment.

The Chann1 0.2.0 Stage/Production proof used:

`Create Contact -> Create Deal -> Create Note -> Create Follow-up -> Complete Follow-up -> WON Deal -> Reopen Deal`

## 6. Definition of Done

A feature is not Done merely because:

- code compiles;
- Docker builds;
- PR CI is green;
- infrastructure deploy commands succeed;
- health endpoints return 200.

For a business feature, Done requires:

1. implementation across the required tier boundaries;
2. automated tests at appropriate layers;
3. Official CI PASS;
4. runtime deployability including schema/reference data;
5. target-environment business acceptance where applicable;
6. release/evidence updates when behavior affects deployment or operations.

## 7. CI failure handling

When CI fails:

1. read the exact failed step/log;
2. identify whether the issue is environment, dependency, contract, stale test, or implementation;
3. make a targeted fix;
4. preserve valid coverage;
5. rerun the affected gate;
6. do not suppress or delete a failing test solely to obtain green CI.

If an old test represents a superseded intended contract, update the test to the new intended contract and keep equivalent coverage.

## 8. Build and artifact policy

- Build immutable container images from an accepted source commit.
- Store them in Artifact Registry (or equivalent).
- Record exact artifact identity in the Release Manifest.
- Stage/Test must use the artifacts intended for Production.
- Production must promote the Stage-proven artifact; **never rebuild during promotion**.

## 9. Database release sequence

Environment initialization order is mandatory:

1. apply schema migration;
2. seed idempotent reference data;
3. apply environment/demo fixture data only when needed;
4. deploy/verify runtimes;
5. run runtime acceptance.

The Chann1 Stage/Production work exposed why this order matters: Deal creation failed when `deal_stages` reference data was absent even though schema migration had succeeded.

## 10. Seed data rules

- Reference seed must be idempotent.
- Uniqueness must be respected by business key, not only surrogate UUID.
- Environment proof/demo seed must coexist with reference data without unique-key collisions.
- No environment should require undocumented manual SQL to become usable.

## 11. DEV workflow

DEV optimizes for feedback speed:

- selective deploy changed tiers;
- retain dependency-aware validation;
- use test/demo identity only in explicitly reduced-security development mode;
- functional smoke after deploy;
- when manual runtime fixes reveal a reproducibility gap, fix the source/seed/deploy process before promoting.

## 12. Stage/Test gate

Stage is the production-like acceptance environment.

Required evidence before Production:

- exact source/release identity;
- schema migration PASS;
- reference seed PASS;
- service readiness PASS;
- full functional E2E PASS;
- immutable artifact identity recorded;
- rollback rehearsal evidence PASS;
- known limitations recorded.

## 13. Rollback rehearsal

Before Production for material releases:

- identify currently stable revisions/artifacts;
- deploy or route to a candidate/rehearsal revision;
- verify health/business smoke;
- switch traffic back to the captured stable revision;
- verify post-rollback runtime;
- record evidence.

Do not call this proof of historical application/schema compatibility unless a materially different previous release was actually tested.

## 14. Production process

Production always requires explicit approval.

After approval:

1. verify Stage release and rollback evidence;
2. promote exact Stage-proven artifacts without rebuild;
3. execute migration/reference-seed gates;
4. deploy in dependency order where needed;
5. verify readiness;
6. run safe production functional smoke/acceptance appropriate to the system;
7. record Production manifest/evidence;
8. monitor release health.

For a real customer Production system, test/demo identity and reduced-security configuration are forbidden unless explicitly accepted as a temporary security exception by the owning organization.

## 15. Operations and observability

Every runtime service should expose:

- liveness;
- readiness;
- correlation/request IDs;
- structured logs;
- latency/error measurement points.

Release watch should cover Cloud Run revision health, error ratio, latency, database health/capacity, cache availability, migration result, and release-manifest correlation.

## 16. Operational truth rules

- Backup configured != restore proven.
- Terraform valid != infrastructure deployed.
- Infrastructure deployed != application usable.
- Health 200 != business flow complete.
- Stage PASS != Production approval.
- Production deploy PASS != secure Production design.

Use explicit evidence wording instead of optimistic assumptions.
