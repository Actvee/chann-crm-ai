# Chann1 Database / Migration / Cache / Data Safety Standard

## 1. Purpose

This standard defines the rules required to keep application rollback practical without treating production-data rollback as a normal release mechanism.

## 2. Source of truth

PostgreSQL is the authoritative source of business state. Redis/cache is an optimization and must never become the only authoritative copy of business or authorization data.

Only the Data Tier may connect directly to PostgreSQL or Redis at application runtime.

## 3. Schema ownership and versioning

- Database schema is versioned through Alembic migrations.
- Application startup must not silently run migrations.
- Schema changes must be explicit pipeline/release operations.
- Database is versioned by migration/schema state, not as a fake application artifact.
- Every release manifest must include the migration head.

## 4. Migration strategy

Use:

`Expand -> Migrate/Backfill -> Contract`

### Expand

Introduce additive/backward-compatible schema first.

Examples:

- nullable/additive columns;
- new tables;
- new indexes with safe creation strategy;
- dual-read/dual-write support where necessary.

### Migrate/Backfill

Move existing data into the new representation and observe correctness.

### Contract

Remove old structures only after all compatible application versions no longer require them.

## 5. Compatibility window

During a transition, the new database schema should support at least the current and previous compatible Data/Application release.

This is the primary mechanism that permits application rollback without rolling back production data.

A release that cannot preserve the required compatibility window must be explicitly classified as a breaking database release with a separate operational plan.

## 6. Environment initialization order

Mandatory order:

1. migration;
2. idempotent reference seed;
3. environment-specific fixture/demo seed if explicitly required;
4. service deployment/readiness;
5. runtime functional acceptance.

This ordering is a proven Chann1 lesson. A migrated database was structurally valid but Deal creation returned 404 because required `deal_stages` reference data had not been seeded.

## 7. Reference data

Reference data includes values required for the system to function correctly, such as Deal stages and permission definitions.

Rules:

- seed scripts must be idempotent;
- business uniqueness (for example permission `code`) must be honored;
- rerunning a reference seed must not create duplicates or replace unrelated IDs incorrectly;
- reference seed must be part of deployability, not an undocumented operator action.

## 8. Environment/proof fixtures

Environment fixtures (for example an E2E proof user/role) are different from reference data.

Rules:

- run them only in environments that explicitly require them;
- keep them separate from production business data assumptions;
- never let environment fixtures collide with reference-data unique keys;
- use deterministic identifiers only where they improve repeatability;
- do not confuse a proof identity with a real authentication/identity model.

## 9. Authorization data

Authorization context is database-backed and may be cached by Data.

Application selects the required permission and authorized scope. Data enforces the supplied scope against data ownership/team relationships.

Valid scope values are `OWN`, `TEAM`, and `ALL`.

Current functional proof validates permission-driven operation with an E2E role, but the complete Sales/Manager/Admin matrix is not fully proven and must not be documented as complete.

## 10. Cache-aside standard

For every cached object define:

- cache key;
- TTL;
- source of truth;
- cache population rule;
- invalidation rule;
- behavior on cache outage.

Authorization cache failures must fail secure. Cache unavailability may cause a database fallback/degraded mode but may not create broader access.

## 11. Transaction boundaries

Data owns application database transactions. Higher tiers must not coordinate direct SQL transactions.

Business operations should update authoritative state atomically where required. Cache invalidation occurs only after the authoritative database mutation is committed or in an equivalent consistency-safe order.

## 12. Data model safety rules

The baseline database design uses:

- UUID primary keys;
- timezone-aware timestamps;
- optimistic concurrency `version` on mutable entities;
- archive/disable states instead of broad destructive cascade deletion;
- `NUMERIC(18,2)` plus 3-character currency for monetary values;
- foreign-key/check constraints;
- append-oriented audit-event schema.

The presence of `audit_events` in the schema does not by itself prove audit emission. Runtime audit behavior remains NOT_VERIFIED until executable coverage exists.

## 13. Rollback and data safety

Normal application rollback:

- selects a previously accepted immutable Presentation/Application/Data combination;
- routes/promotes those artifacts;
- does not reverse business data;
- relies on database compatibility.

Database reverse migration is not the default rollback plan.

## 14. Backup and restore

A configured backup is not recovery proof.

Recovery is considered PROVEN only when an isolated restore drill succeeds and the restored database/application state is verified.

For this Chann1 project, isolated Cloud SQL restore remains **NOT_PROVEN_DEFERRED**. Do not upgrade that status based on backup configuration or documentation alone.

## 15. Migration/test gates

At minimum require:

- migration syntax/import validation;
- clean upgrade from empty PostgreSQL;
- reference seed execution;
- schema/constraint contract tests;
- Data integration against real PostgreSQL;
- compatibility review for destructive changes;
- runtime acceptance after environment migration.

## 16. Unsafe patterns to reject

Reject changes that:

- require manual production DDL not captured in migration files;
- depend on manual reference-data inserts;
- assume cache contains authoritative state;
- delete old schema before compatible application promotion completes;
- require restoring/reversing production data for routine application rollback;
- suppress failed migration tests to unblock a release;
- mix environment proof fixtures into universal reference data without explicit intent.
