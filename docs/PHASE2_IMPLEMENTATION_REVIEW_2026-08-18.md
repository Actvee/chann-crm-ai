# Phase 2 Implementation Review — 2026-08-18

## Source baseline

- Base commit: `a6852811e2c1a536760ef390c08a2d916a1d944c`
- Product source of truth: `CHANN_CRM_AI_MASTER_SPEC.md`, Phase 2 and final permission appendix
- Scope: source implementation and local validation only
- GCP/IAM/Service Account/Secret Manager/Production mutation: none

## Implemented

- Alembic revision `0002_phase2_permissions`
- Tenant-scoped `custom_roles`, `role_permissions`, `license_settings`
- Authoritative `ownership_transfers` state for two-party confirmation
- Complete permission-key catalogue from the final Master Spec appendix
- Default Owner/Admin/Member/CS templates, seeded once per tenant
- Owner immutability and transfer-only owner changes
- Permission context cache-aside with fail-secure database fallback
- Invalidation after role/member/ownership changes
- Application permission-key gates with no role-name authorization branching
- Role, member-role, setting, two-party transfer and Platform Admin break-glass APIs
- LIFF Sales role/settings management page through Presentation -> Application
- Unit, boundary and PostgreSQL integration coverage including duplicate-role race

## Explicit decisions

1. `ownership_transfers` is added although the abbreviated Phase 2 table list
   omits it. A two-party pending transfer cannot safely live only in Redis or
   an untyped setting; PostgreSQL remains authoritative.
2. The CS-vs-Sales acceptance test and cross-cutting CS/Sales separation are
   treated as stricter than the final appendix's broad `member ticket.*`
   shorthand. The default Member template therefore does not receive
   `ticket.assign`; a tenant may grant it explicitly through a custom role.
3. Phase 4 owns the OpenRouter AI adapter. Until then, the Phase 2 policy
   compiler accepts only explicit permission keys and rejects vague prompts.
   It reports `ai_used=false`; no evidence may call AI prompt interpretation
   proven in this phase.
4. Phase 3 owns the authoritative audit log. Ownership operations expose the
   Phase 2 transaction boundary now, but audit emission remains
   `NOT_VERIFIED` until the Phase 3 schema and writer exist.

## Required remaining gates

1. Run `scripts/phase2-source-verify.sh` in Cloud Shell.
2. Review the immutable source verification result and commit/CI evidence.
3. Produce and approve the DEV migration/seed plan for revision 0002.
4. Build immutable Data/Application/Presentation artifacts selectively.
5. Deploy in dependency order: database -> Data -> Application -> Presentation.
6. Run DEV runtime business acceptance for permission positive/negative,
   role isolation, settings, owner transfer and break-glass.
7. After Phase 4 supplies the approved AI adapter, prove prompt interpretation
   and the chat round trip; these are not claimed by the deterministic Phase 2
   compiler alone.

`CI PASS`, `build PASS` or `deploy PASS` alone does not close Phase 2.
