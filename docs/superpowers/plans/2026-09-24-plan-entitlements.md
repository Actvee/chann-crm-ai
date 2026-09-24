# Feature control by sales plan (round 21D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A licence has a plan (Starter · Pro · Enterprise · Enterprise Plus); the plan — never instead of the role, always beside it — decides which features, how many AI report credits and how many users a shop has, on every surface (chat, dashboard, external API, the three LINE OAs, background work), and a person who hits a plan limit is told which plan has it.

**Architecture:** The Data tier owns the matrix (`data/chann_data/plans.py`, one literal the owner's HTML table is tested against) and every check that must be inside its own transaction (a seat, a credit, a plan change, a technician invite, a customer link, a multi-step approval). It sends the resolved plan with every membership row, API-key resolution and tenant row, cached under its own licence-level key. The Application subtracts plan-locked keys from the principal's permission set in one place, so the ~170 existing `principal.require(...)` calls and every chat `"x" in set(permission_keys)` check become plan-aware without being edited, and answers with one refusal shape (`403 plan_required`) everywhere; features that share a key with always-on work get a named `require_feature(...)`. The dashboard reads the same payload from `/me/permissions` and draws locked entries disabled-with-reason.

**Tech Stack:** FastAPI (Application + Data), SQLAlchemy 2 + Alembic, Redis (Data-tier cache), httpx `DataClient`, Next.js 16 / React 19 LIFF pages and the `/admin` console, OpenRouter (the intent model), pytest (+ Postgres for `tests/integration`), the repo's `scripts/dev/check-*.py` gate, `scripts/agent-test`.

**Spec:** `docs/superpowers/specs/2026-09-24-plan-entitlements-design.md` (approved 24 ก.ย. 2569 with the owner's five decisions in §13; the owner's table is `docs/superpowers/specs/2026-09-24-sales-plans-source.html`).

## Global Constraints

- **Entitlement sits beside permission, never instead of it.** A role grants only what the plan includes; a plan never grants anything a role does not. Nothing is deleted when a plan goes down; locked data comes back unchanged when it goes up (spec §1, §3.4).
- **The refusal shape, everywhere:** `HTTP 403 {"error": "plan_required", "feature": "<key>", "plan": "<code>", "min_plan": "<code>", "message": "<English sentence>"}`. The external API's `_error_body` turns it into `{"error": {"code": "plan_required", "message": "…"}}` unchanged. Chat uses the §6.2 copy verbatim.
- **The owner's five decisions (spec §13):** backfill → Pro / Enterprise (active API key, multi-step approval, > 15 active members) / Enterprise Plus (> 50); new trials → Pro · **a downgrade is REFUSED while active members exceed the target plan's limit** — 409 `{"error": "plan_member_limit", "members": M, "limit": L, "inactivate": N, "plan": "<code>", "message": "ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น <plan label>"}`, plan unchanged · Enterprise Plus starts at 100 credits/month; the `ai_chart_quota` override is honoured on Pro and up; values already set keep working · Starter sees 3 basic reports (`open_jobs_by_tech`, `satisfaction_avg` hidden) · the upgrade button opens `CHANN_SALES_CONTACT` (`label|url`) and is not drawn when it is unset.
- **Active member** = a distinct `chann_uid` with a `license_members.status = 'active'` row, any channel, owner included; customers never count. A member is inactivated by the existing remove road (`MemberRepository.set_status(..., status="removed")`), which keeps the row.
- **Plan codes and labels, exactly:** `starter`/`Starter`, `pro`/`Pro`, `enterprise`/`Enterprise`, `enterprise_plus`/`Enterprise Plus` (labels are product names, never translated). Server default `pro`.
- **Entitlement keys, exactly ten:** `feature.customer_line_link`, `feature.live_chat`, `feature.service`, `feature.warranty`, `feature.custom_documents`, `feature.custom_roles`, `feature.multi_level_approval`, `feature.external_api`, `quota.ai_reports_per_month`, `limit.members`. `check-perms.py` rejects any other `feature./quota./limit.` key in code.
- **Model-first** (`docs/MODEL_FIRST.md`, CLAUDE.md §-1 rule 6). The plan check runs where the permission check runs — after the model proposed `(action, entity)`. A keyword may *decline* on plan grounds (the typed `สร้างรายงานด้วย AI:` prefix on Starter), never *act*. No new trigger words. The only prompt change is the `plan` entity (Task 9), and it is measured with `scripts/dev/ask-model.py` **per sentence, before and after**, never on totals; the raw JSON goes into the handoff.
- **Tier boundary:** Application code never imports `sqlalchemy`, `psycopg`, `redis`, `alembic` or `chann_data` (`tests/boundary/test_tier_boundaries.py`). Tests may import both tiers. Application authorisation never compares a role string (`role == "admin"` is a boundary-test failure) — standard-vs-custom role checks live in the Data tier.
- Every `routers_phase2.py` route whose path contains `{license_id}` calls `_require_same_tenant(principal, license_id)` and `principal.require(...)`/`require_any(...)` (`test_no_tenant_route_is_missing_its_guards`). Every `DataClient` call maps to a real `/internal/v1/...` route and method (`check-client.py`); every dashboard `/api/phase2/...` call maps to an Application route (`check-routes.py`).
- **Parity** (owner rule 2): chat ↔ dashboard. `ACCEPTED` in `check-parity.py` is keyed `(entity, action)`; `ACTION_PERMISSIONS` (`chat.py:103`) is keyed `(action, entity)`. New this round: a **plan section** in `check-parity.py` and the ten-key family in `check-perms.py` (Task 14).
- A new `ACTION_PERMISSIONS` pair needs, in the same commit: a branch in `_execute_intent`'s dispatch (`measure-capabilities.py` must still print `· 0 with no handler ·`, pinned by `test_round20i_last_gaps.py`), an `ENTITY_DASHBOARD_PAGE` entry whose section is in `DASHBOARD_PATHS` (`test_chat_read_intents.py`), and an `entity="…"` block in `INTENT_SYSTEM_PROMPT` (`test_phase6_chat.py::…test_every_conversational_capability_is_in_the_prompt`).
- `audit_log.action` has a CHECK constraint. Allowed verbs, exactly: `create, update, delete, assign, transfer, cross_tenant_lookup, link_document, upsert, status, remove_product, claim, reject, check_in, check_out, pdpa_erasure, pdpa_export` (`data/chann_data/audit_actions.py`). **This round writes only `update`** (plan change, `entity_type="license"`) — never invent a verb.
- Migration id this round: `0039_license_plan` (17 chars ≤ 32), `down_revision = "0038_deal_closed_at"`; `EXPECTED_MIGRATION_HEAD = "0039_license_plan"` in `data/chann_data/main.py`; the deploy runs the migrate job **before** the data image.
- LINE replies stay within **15 lines** (`reply.text.count("\n") <= 15`); the refusal is ≤ 5 lines. The help menu is **already at its 15-line limit** — this round adds **no** help-menu topic line; the plan read goes into the guide step instead.
- OA theme colours: Sale `#178a50` · Tech `#1f6fd6` · CS `#e8731a` (`presentation/app/globals.css`, `[data-theme]`). The lock styling uses the muted text token already in `globals.css`, with ≥ 4.5:1 contrast.
- **Every dashboard change (buttons, placement, pages, admin console) goes through the `ui-ux-pro-max` skill** — owner's standing rule, 20 ก.ย. 2569. Rule this round leans on: *a disabled control shows its reason as visible text under it, never only in a tooltip.*
- `python3 scripts/dev/render-guides.py` after any `guides.py` change; `python3 scripts/dev/render-guide-images.py` after any scene change, then **LOOK at the PNG**. Never edit a guide PNG by hand.
- Tests need `JWT_SECRET=test-jwt-secret`. Integration tests: `TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test` (container **`pg21c`**). **Never 5435** — that is `pg20w`, which another round's suites use.
- **Never run the full unit suite without these two deselects** (they call the real Zoho endpoint):
  `--deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors"`
- Python for tests: `/tmp/dv/bin/python` (if `/tmp/dv` is gone after a Cloud Shell restart: `python3 -m venv /tmp/dv && /tmp/dv/bin/pip install -q -r data/requirements.txt -r application/requirements.txt -r requirements-test.txt`).
- Work in the worktree **`~/stage-fix/r21d`** (branch `r21d`, based on the 21C tip `404cfe3`, whose tree equals `origin/main` `449819e`). Commit after each task with `git add -A`.
- `~/stage-fix/tools/gate.sh` and `~/stage-fix/tools/converse.py` default to `~/stage-fix/registry` — **point them at `~/stage-fix/r21d`** (a `sed` copy of gate.sh; `CONVERSE_ROOT=~/stage-fix/r21d` for converse.py) or they test the previous round.
- **Line numbers in this plan are as of `404cfe3`.** Earlier tasks move later lines, so every edit also names a `grep -n` anchor; trust the anchor over the number.
- **A test that fails because the fake shop is now Pro is information, not an obstacle** (round 20K): the fix is to give that fake the plan the feature needs (`plan_payload("enterprise")` from `tests/unit/plan_fixtures.py`, Task 5), **never** to weaken a gate. Name every such test in the task report.

---

## File map

| Area | File | Responsibility |
|---|---|---|
| Data | `data/chann_data/plans.py` | **new** — the four plans, `ROW_MAP` (the table's 41 rows), `resolve()`, the three refusals |
| Data | `data/chann_data/repositories/plan_repo.py` | **new** — `PlanRepository`: payload, usage, seat check, feature check, plan change, preview |
| Data | `data/chann_data/models.py` | `License.plan_code` |
| Data | `database/alembic/versions/0039_license_plan.py` | **new** — column + CHECK + backfill + member-limit assertion |
| Data | `data/chann_data/main.py` | `EXPECTED_MIGRATION_HEAD` |
| Data | `data/chann_data/cache.py` | `k_license_plan`, purge pattern |
| Data | `data/chann_data/schemas.py` | `plan` on `MembershipOut`, `ApiKeyResolveOut`, `TenantSummaryOut`; `TenantUpdateIn.plan_code` |
| Data | `data/chann_data/repositories/phase65.py` | seat + service checks in `redeem_invite`; service/custom-role check in `create_invite`; link check in `link_customer` |
| Data | `data/chann_data/repositories/tenant_scope.py` | seat check on reactivation (`MemberRepository.set_status`) |
| Data | `data/chann_data/repositories/phase18.py` | plan column/filter/usage/preview; `plan_code` editable with the downgrade refusal; seat check in `move_member` |
| Data | `data/chann_data/repositories/phase14.py` | `open_steps_for_report(..., max_steps=)` |
| Data | `data/chann_data/repositories/phase15.py` | `sla_overdue` / `time_out` skip licences without live chat |
| Data | `data/chann_data/repositories/phase2.py` | `ai_chart_allowance` from the plan |
| Data | `data/chann_data/routers/internal.py` | plan payload on memberships / api-key resolve / tenants; `GET /licenses/{id}/plan`; `GET /platform/tenants/{id}/plan-preview`; refusal mapping; approvals, sweep, member-role checks |
| App | `application/chann_app/services/entitlements.py` | **new** — `PlanView`, labels, `PERMISSION_FEATURE`, `ALWAYS_ON_PERMISSIONS`, `NAMED_FEATURE_CHECKS`, refusal helpers, `plan_for`, `sales_contact` |
| App | `application/chann_app/services/authorization.py` | `TenantPrincipal.plan`, `plan_locked_keys`, `require_feature`; LIFF principal built from the payload |
| App | `application/chann_app/auth/api_key.py` | ext principal carries the plan; `require_feature("feature.external_api")` |
| App | `application/chann_app/data_client.py` | `license_plan`, `platform_plan_preview`; `platform_tenants(plan=)` |
| App | `application/chann_app/config.py` | `chann_sales_contact` |
| App | `application/chann_app/routers_phase2.py` | `/me/permissions` plan; `GET /licenses/{id}/plan`; named checks; `_propagate` keeps 403 `plan_required` |
| App | `application/chann_app/routers_admin.py` | plan PATCH, preview, list filter, quota clearing, owner notice |
| App | `application/chann_app/services/chart_quota.py` | `DEFAULT_QUOTA` removed; fail-open reads the plan |
| App | `application/chann_app/services/chat.py` | `_PLAN` context, effective keys, refusal copy, central gate, `_no_permission` (115 sites), OA behaviour, `plan` read |
| App | `application/chann_app/services/ai/intent.py` | the `entity="plan"` block (measured) |
| App | `application/chann_app/services/registration.py` | redeem / link refusals in words |
| App | `application/chann_app/services/{notify,approval,job_sla,approval_sla}.py`, `services/documents/selection.py` | background work and customer pushes skip locked shops |
| App | `application/chann_app/services/guides.py` | the AI-reports credit sentence; a plan step |
| Pres | `presentation/app/liff/_nav-model.tsx`, `_nav.tsx`, `_plan.tsx` (**new**) | `feature` on entries, `lockedBy`, `<PlanLocked>`, `planHas` |
| Pres | `presentation/app/liff/sales/{_session.ts,_shell.tsx,_format.ts,_strings.ts}` | plan in the session; locked page in the shell; `plan_required` text |
| Pres | `presentation/app/liff/sales/{company,members,roles,approvals/settings,reports/ai,invoices,quotes/[id]}/*.tsx` | plan card, disabled-with-reason lines, three basic cards on Starter |
| Pres | `presentation/app/admin/**`, `presentation/lib/admin-copy.ts`, `presentation/app/api/admin/tenants/[id]/**` | plan select + preview + refusal, usage, list column/filter |
| Pres | `presentation/lib/i18n/{th,en}.ts` | `dashboard.plan.*` |
| Infra | `infrastructure/terraform/{variables.tf,cloud_run.tf}`, `docs/RUNTIME_CONFIG_CONTRACT.md` | `CHANN_SALES_CONTACT` |
| Tools | `scripts/dev/{check-perms,check-parity,check-routes}.py` | the plan family, the plan parity section, `api_principal` rule |
| Tools | `scripts/agent-test/{scenario.schema.json,agent_test_runner/backends.py,scenarios/round21d-*.yaml}` | `actor.plan`; the Starter and Pro-unchanged scenarios |
| Docs | `docs/API.md`, `docs/SESSION_HANDOFF.md`, `~/CHECKLIST-หลัง-deploy.md` | `plan_required`; handoff; tester section **X** |
| Tests | `tests/unit/test_round21d_{plan_matrix,migration_literals,entitlements,principal,ext_api,chat_refusal,no_bare_refusal,oa_behaviour,credits,plan_read,background,ui,admin,richmenu,checkers}.py`, `tests/unit/plan_fixtures.py` | |
| Tests | `tests/integration/test_round21d_{migration,plan_data,feature_checks}.py` | |

---
### Task 1: The plan matrix is the owner's table

**Files:**
- Create: `data/chann_data/plans.py`
- Test: `tests/unit/test_round21d_plan_matrix.py`

**Interfaces:**
- Consumes: nothing (pure module; the source table is `docs/superpowers/specs/2026-09-24-sales-plans-source.html`).
- Produces (later tasks import these exact names from `chann_data.plans`):
  - `FEATURE_KEYS: tuple[str, ...]` (8), `QUOTA_KEYS` (1), `LIMIT_KEYS` (1), `ENTITLEMENT_KEYS` (10), `AI_REPORTS = "quota.ai_reports_per_month"`, `MEMBERS = "limit.members"`
  - `@dataclass(frozen=True) class Plan(code, label, rank, features, ai_reports_per_month, members, quota_top_up)` with `.has(key) -> bool`
  - `PLANS: dict[str, Plan]`, `PLAN_CODES: tuple[str, ...]`, `DEFAULT_PLAN = "pro"`, `ALWAYS_ON = "always_on"`, `ROW_MAP: tuple[tuple[str, str], ...]`
  - `FEATURE_LABELS_EN: dict[str, str]`, `DOWNGRADE_REFUSED_TH: str`
  - `plan(code: str | None) -> Plan`, `min_plan(key: str) -> str | None`, `smallest_plan_for(people: int, *, at_least: str = "starter") -> str`, `ai_allowance(p: Plan, override) -> int`, `resolve(code: str | None, *, ai_override=None) -> dict`
  - exceptions: `UnknownPlan(ValueError)`, `PlanFeatureLocked(feature, plan_code, **extra)`, `MemberLimitReached(limit, plan_code, **extra)`, `PlanDowngradeRefused(members, limit, plan_code)` — each with `.detail() -> dict`

- [ ] **Step 1: Write the failing test — it parses the owner's HTML**

Create `tests/unit/test_round21d_plan_matrix.py`:

```python
"""Round 21D — the plan matrix IS the owner's table.

The owner uploaded the sales-plan comparison (24 ก.ย. 2569) and asked the
system to enforce it. This test reads that HTML and compares it, cell by
cell, with `chann_data.plans`. If marketing edits the table, this fails
until the code is changed on purpose (spec §11.1).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from chann_data import plans

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/superpowers/specs/2026-09-24-sales-plans-source.html"
COLUMNS = ("starter", "pro", "enterprise", "enterprise_plus")
ROW_RE = re.compile(r'<div class="row[^"]*">.*?</div></div>', re.S)
CELL_RE = re.compile(r'<div class="([^"]*)">(.*?)</div>', re.S)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _table() -> list[tuple[str, tuple[str, str, str, str]]]:
    """(label, four cell texts) per row, in table order. The first cell
    match swallows the category div (its text is the category); the users
    row has an empty description, so its label is the category without
    the emoji."""
    rows = []
    for row in ROW_RE.findall(SOURCE.read_text(encoding="utf-8")):
        cells = CELL_RE.findall(row)
        category, description = _text(cells[0][1]), _text(cells[1][1])
        label = description or category.split(" ", 1)[1]
        rows.append((label, tuple(_text(c[1]) for c in cells[2:6])))
    return rows


TABLE = _table()


def _with(key: str) -> set[str]:
    return {code for code in COLUMNS if key in plans.PLANS[code].features}


class TestEveryRowIsMapped:
    def test_the_table_has_the_41_rows_the_spec_counted(self):
        assert len(TABLE) == 41

    def test_row_map_names_the_same_rows_in_the_same_order(self):
        assert [label for label, _ in TABLE] == [label for label, _ in plans.ROW_MAP]

    def test_every_key_in_the_row_map_is_a_known_key_or_always_on(self):
        for _label, key in plans.ROW_MAP:
            assert key == plans.ALWAYS_ON or key in plans.ENTITLEMENT_KEYS, key

    def test_every_entitlement_key_appears_in_the_table(self):
        mapped = {key for _label, key in plans.ROW_MAP}
        assert set(plans.ENTITLEMENT_KEYS) <= mapped


class TestTheCellsAgree:
    @pytest.mark.parametrize("index", range(41))
    def test_one_row(self, index):
        label, cells = TABLE[index]
        key = plans.ROW_MAP[index][1]
        ticks = {code for code, cell in zip(COLUMNS, cells) if cell == "✓"}
        if key == plans.ALWAYS_ON:
            assert cells == ("✓", "✓", "✓", "✓"), label
        elif key == plans.AI_REPORTS:
            assert cells == ("—", "(30 ครั้ง/เดือน)", "(100 ครั้ง/เดือน)", "(เพิ่มโควต้าได้)")
            assert [plans.PLANS[c].ai_reports_per_month for c in COLUMNS[:3]] == [0, 30, 100]
            plus = plans.PLANS["enterprise_plus"]
            assert plus.ai_reports_per_month == 100 and plus.quota_top_up is True
        elif key == plans.MEMBERS:
            assert cells == ("5 คน", "15 คน", "50 คน", "มากกว่า 50 คน")
            assert [plans.PLANS[c].members for c in COLUMNS] == [5, 15, 50, None]
        elif key == "feature.custom_roles":
            # Row 36, the one mixed row: standard roles are ✓ everywhere,
            # "กำหนดเองได้" is the customising half the key covers.
            assert cells == ("✓", "(กำหนดเองได้)", "(กำหนดเองได้)", "(กำหนดเองได้)")
            assert _with(key) == {"pro", "enterprise", "enterprise_plus"}
        else:
            assert ticks == _with(key), (label, key)


class TestTheOwnersDecisions:
    """Spec §13, 24 ก.ย. 2569 — what the table does not say by itself."""

    def test_the_top_up_is_honoured_on_pro_and_up_and_never_on_starter(self):
        assert [plans.PLANS[c].quota_top_up for c in COLUMNS] == [False, True, True, True]

    def test_an_override_moves_the_allowance_only_where_it_is_honoured(self):
        assert plans.ai_allowance(plans.PLANS["pro"], 45) == 45
        assert plans.ai_allowance(plans.PLANS["pro"], None) == 30
        assert plans.ai_allowance(plans.PLANS["pro"], "") == 30
        assert plans.ai_allowance(plans.PLANS["pro"], 0) == 0      # 0 is a value, not "unset"
        assert plans.ai_allowance(plans.PLANS["starter"], 45) == 0
        assert plans.ai_allowance(plans.PLANS["enterprise_plus"], 400) == 400

    def test_the_default_plan_is_pro(self):
        assert plans.DEFAULT_PLAN == "pro"
        assert plans.plan(None).code == "pro"
        assert plans.plan("nonsense").code == "pro"


class TestResolve:
    def test_min_plan_is_computed_from_rank(self):
        assert plans.min_plan("feature.service") == "pro"
        assert plans.min_plan("feature.external_api") == "enterprise"
        assert plans.min_plan(plans.AI_REPORTS) == "pro"
        assert plans.min_plan(plans.MEMBERS) is None

    def test_starter_payload(self):
        out = plans.resolve("starter")
        assert out["code"] == "starter" and out["label"] == "Starter" and out["rank"] == 0
        assert out["features"] == []
        assert out["locked"][plans.AI_REPORTS] == "pro"
        assert out["locked"]["feature.external_api"] == "enterprise"
        assert out["limits"] == {"members": 5, "ai_reports_per_month": 0}

    def test_pro_payload_is_the_fail_open_shape(self):
        out = plans.resolve("pro")
        assert out["locked"] == {"feature.external_api": "enterprise",
                                 "feature.multi_level_approval": "enterprise"}
        assert out["limits"] == {"members": 15, "ai_reports_per_month": 30}

    def test_an_override_of_zero_on_pro_is_used_up_not_locked(self):
        out = plans.resolve("pro", ai_override=0)
        assert out["limits"]["ai_reports_per_month"] == 0
        assert plans.AI_REPORTS not in out["locked"]

    def test_smallest_plan_for(self):
        assert plans.smallest_plan_for(5) == "starter"
        assert plans.smallest_plan_for(16) == "enterprise"
        assert plans.smallest_plan_for(51) == "enterprise_plus"
        assert plans.smallest_plan_for(3, at_least="pro") == "pro"


class TestTheRefusals:
    def test_plan_required(self):
        body = plans.PlanFeatureLocked("feature.service", "starter").detail()
        assert body == {
            "error": "plan_required", "feature": "feature.service", "plan": "starter",
            "min_plan": "pro",
            "message": "Service jobs and technicians: included from the Pro plan. This shop is on Starter.",
        }

    def test_member_limit(self):
        body = plans.MemberLimitReached(5, "starter", license_id="L1").detail()
        assert body["error"] == "member_limit_reached" and body["limit"] == 5
        assert body["plan"] == "starter" and body["license_id"] == "L1"

    def test_the_downgrade_refusal_says_how_many_to_inactivate(self):
        body = plans.PlanDowngradeRefused(9, 5, "starter").detail()
        assert body == {
            "error": "plan_member_limit", "members": 9, "limit": 5, "inactivate": 4,
            "plan": "starter", "plan_label": "Starter",
            "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter",
        }

    def test_unknown_plan_is_a_value_error(self):
        assert issubclass(plans.UnknownPlan, ValueError)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_plan_matrix.py -q
```
Expected: collection ERROR — `ImportError: cannot import name 'plans' from 'chann_data'`.

- [ ] **Step 3: Write the module**

Create `data/chann_data/plans.py`:

```python
"""The four sales plans (round 21D) — the owner's comparison table as one
literal.

Owner, 24 ก.ย. 2569: "ตัวควบคุมฟีเจอร์ตาม plan". The table is
docs/superpowers/specs/2026-09-24-sales-plans-source.html, and
tests/unit/test_round21d_plan_matrix.py parses it and fails the moment this
file and the table disagree.

A plan is code, not a row: a feature key means nothing until code enforces
it (review C9, 6 Sep 2026 — "catalogue entries nothing enforced"), so a
plan change ships with code and a gate run. Per-licence exceptions (more AI
credits for one shop) are `license_settings` rows, as they always were.

This module holds the table, `resolve()` and the three refusals every tier
speaks. Reading a licence's plan from the database is
`repositories/plan_repo.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

FEATURE_KEYS: tuple[str, ...] = (
    "feature.customer_line_link",
    "feature.live_chat",
    "feature.service",
    "feature.warranty",
    "feature.custom_documents",
    "feature.custom_roles",
    "feature.multi_level_approval",
    "feature.external_api",
)
AI_REPORTS = "quota.ai_reports_per_month"
MEMBERS = "limit.members"
QUOTA_KEYS: tuple[str, ...] = (AI_REPORTS,)
LIMIT_KEYS: tuple[str, ...] = (MEMBERS,)
ENTITLEMENT_KEYS: tuple[str, ...] = FEATURE_KEYS + QUOTA_KEYS + LIMIT_KEYS

#: The §4 English labels — the `message` of a refusal. The Thai labels
#: (chat, dashboard) live in the Application's entitlements.py; a unit test
#: keeps the two English copies equal.
FEATURE_LABELS_EN: dict[str, str] = {
    "feature.customer_line_link": "Customer LINE link",
    "feature.live_chat": "Customer chat",
    "feature.service": "Service jobs and technicians",
    "feature.warranty": "Warranty register",
    "feature.custom_documents": "Custom document templates",
    "feature.custom_roles": "Custom roles",
    "feature.multi_level_approval": "Multi-level approval",
    "feature.external_api": "External API",
    AI_REPORTS: "AI reports",
    MEMBERS: "Users",
}

#: Owner, 24 ก.ย. 2569 (spec §13 Q2): a downgrade is refused, in these words,
#: while the shop has more active members than the target plan allows.
DOWNGRADE_REFUSED_TH = "ต้องปิดใช้งาน (inactivate) สมาชิก {n} คนก่อนลดแพ็กเกจเป็น {plan}"


@dataclass(frozen=True)
class Plan:
    code: str
    label: str
    rank: int
    features: frozenset[str]
    ai_reports_per_month: int
    members: int | None          # None = no cap
    quota_top_up: bool           # the admin's ai_chart_quota override applies (§13 Q3)

    def has(self, key: str) -> bool:
        """The PLAN's answer. On Starter the AI-report road is locked, not
        used up — the person hears the plan, not a counter (spec §4)."""
        if key == AI_REPORTS:
            return self.ai_reports_per_month > 0
        if key in LIMIT_KEYS:
            return True
        return key in self.features


_PRO = frozenset({
    "feature.customer_line_link", "feature.live_chat", "feature.service",
    "feature.warranty", "feature.custom_documents", "feature.custom_roles",
})
_ENTERPRISE = _PRO | frozenset({"feature.multi_level_approval", "feature.external_api"})

PLANS: dict[str, Plan] = {
    "starter": Plan("starter", "Starter", 0, frozenset(), 0, 5, False),
    "pro": Plan("pro", "Pro", 1, _PRO, 30, 15, True),
    "enterprise": Plan("enterprise", "Enterprise", 2, _ENTERPRISE, 100, 50, True),
    "enterprise_plus": Plan("enterprise_plus", "Enterprise Plus", 3, _ENTERPRISE, 100, None, True),
}
PLAN_CODES: tuple[str, ...] = tuple(PLANS)
DEFAULT_PLAN = "pro"

ALWAYS_ON = "always_on"

#: The table's 41 rows in order: (row label as the HTML says it, key).
#: Labels repeat ("Import CSV", "บันทึกเอกสารทุกครั้งที่ออก"), so this is an
#: ordered tuple, not a dict; the test compares the two sequences.
ROW_MAP: tuple[tuple[str, str], ...] = (
    ("เพิ่มลูกค้า (ทีละคน / หลายคน / Import CSV)", ALWAYS_ON),
    ("ตรวจข้อมูลซ้ำจากเบอร์/อีเมล", ALWAYS_ON),
    ("เปลี่ยน Lead เป็นลูกค้ายืนยัน", ALWAYS_ON),
    ("จัดเก็บข้อมูลลูกค้า", ALWAYS_ON),
    ("ประวัติการทำรายการของลูกค้า", ALWAYS_ON),
    ("ผูก LINE ลูกค้ากับร้าน", "feature.customer_line_link"),
    ("สร้างดีล กำหนดมูลค่า วันปิดดีล", ALWAYS_ON),
    ("อัปเดตและติดตามสถานะของดีล", ALWAYS_ON),
    ("จัดการหลายดีลพร้อมกัน", ALWAYS_ON),
    ("สร้างและจัดการรายการสินค้า", ALWAYS_ON),
    ("รหัสสินค้า ราคา และรายละเอียด", ALWAYS_ON),
    ("Import CSV", ALWAYS_ON),
    ("ค้นหาสินค้าผ่าน LINE / Dashboard", ALWAYS_ON),
    ("สร้างใบเสนอราคา", ALWAYS_ON),
    ("อัปเดตและติดตามสถานะใบเสนอราคา", ALWAYS_ON),
    ("บันทึกเอกสารทุกครั้งที่ออก", ALWAYS_ON),
    ("สร้างใบแจ้งหนี้", ALWAYS_ON),
    ("ติดตามสถานะใบแจ้งหนี้และการชำระเงิน", ALWAYS_ON),
    ("บันทึกเอกสารทุกครั้งที่ออก", ALWAYS_ON),
    ("นัดหมายลูกค้า / ติดตามงาน", ALWAYS_ON),
    ("แจ้งเตือนอัตโนมัติ", ALWAYS_ON),
    ("Daily Summary ทุกเช้า 08:00 น.", ALWAYS_ON),
    ("แชทกับลูกค้า (Live Chat + รูป)", "feature.live_chat"),
    ("LINE บริการลูกค้า", "feature.customer_line_link"),
    ("ตั้งค่า SLA การตอบแชท", "feature.live_chat"),
    ("รายงานพื้นฐาน", ALWAYS_ON),
    ("ถามรายงานด้วย AI (ตาราง/กราฟ)", AI_REPORTS),
    ("LINE ช่าง / ทีมช่าง", "feature.service"),
    ("รับงาน เช็คอิน + พิกัด ส่งรูปหน้างาน", "feature.service"),
    ("รายงานการซ่อม PDF + ลายเซ็น", "feature.service"),
    ("แบบประเมินหลังงานเสร็จ", "feature.service"),
    ("บันทึก Serial Number รุ่นที่ซื้อ-ระยะประกัน", "feature.warranty"),
    ("Import CSV", "feature.warranty"),
    ("ตรวจสถานะประกันจากงานซ่อม", "feature.warranty"),
    ("AI ร่างแบบฟอร์ม / อัปโหลด Word Template", "feature.custom_documents"),
    ("บทบาทมาตรฐาน", "feature.custom_roles"),
    ("ขั้นตอนอนุมัติหลายระดับ", "feature.multi_level_approval"),
    ("ประวัติการใช้งาน (Audit Log)", ALWAYS_ON),
    ("โอนความเป็นเจ้าของร้าน", ALWAYS_ON),
    ("เชื่อมต่อ API ภายนอก", "feature.external_api"),
    ("จำนวนผู้ใช้งาน", MEMBERS),
)


def plan(code: str | None) -> Plan:
    """The plan for a code; anything unknown is the default (Pro) — the
    backfill value, so a bad row never locks a paying shop out."""
    return PLANS.get(str(code or ""), PLANS[DEFAULT_PLAN])


def _by_rank() -> list[Plan]:
    return sorted(PLANS.values(), key=lambda p: p.rank)


def min_plan(key: str) -> str | None:
    """The lowest-ranked plan that has the key ("Pro ขึ้นไป"), computed —
    never typed by hand. None for the limit, which every plan has."""
    if key in LIMIT_KEYS:
        return None
    for candidate in _by_rank():
        if candidate.has(key):
            return candidate.code
    return None


def smallest_plan_for(people: int, *, at_least: str = "starter") -> str:
    """The lowest plan at or above `at_least` whose member limit holds
    `people` — what the migration's assertion moves an over-limit shop to."""
    floor = PLANS[at_least].rank
    for candidate in _by_rank():
        if candidate.rank >= floor and (candidate.members is None or people <= candidate.members):
            return candidate.code
    return "enterprise_plus"


def ai_allowance(p: Plan, override) -> int:
    """Plan default, unless the admin set an override and the plan honours
    one (owner, 24 ก.ย. 2569: Pro and up). `0` is a value — the admin
    switching the shop's AI reports off — and not "unset"; "" and None are
    unset."""
    if override is None or override == "" or not p.quota_top_up:
        return p.ai_reports_per_month
    try:
        return max(0, int(str(override).strip()))
    except (TypeError, ValueError):
        return p.ai_reports_per_month


def resolve(code: str | None, *, ai_override=None) -> dict:
    """The payload every principal carries (spec §3.2)."""
    p = plan(code)
    locked = {key: min_plan(key) for key in FEATURE_KEYS if not p.has(key)}
    if not p.has(AI_REPORTS):
        locked[AI_REPORTS] = min_plan(AI_REPORTS)
    return {
        "code": p.code,
        "label": p.label,
        "rank": p.rank,
        "features": sorted(key for key in FEATURE_KEYS if p.has(key)),
        "locked": locked,
        "limits": {"members": p.members, "ai_reports_per_month": ai_allowance(p, ai_override)},
        "quota_top_up": p.quota_top_up,
    }


class UnknownPlan(ValueError):
    """A plan code that is not one of the four."""


class PlanFeatureLocked(Exception):
    """The shop's plan does not have this feature. `extra` rides along in
    the body (company name/phone for the OA sentences)."""

    def __init__(self, feature: str, plan_code: str, **extra):
        super().__init__(f"{feature} is not in the {plan_code} plan")
        self.feature, self.plan_code, self.extra = feature, plan(plan_code).code, extra

    def detail(self) -> dict:
        need = min_plan(self.feature) or DEFAULT_PLAN
        return {
            "error": "plan_required", "feature": self.feature, "plan": self.plan_code,
            "min_plan": need,
            # "<label>: included from …" — the labels are singular and
            # plural nouns alike, so the sentence carries no verb to agree.
            "message": (
                f"{FEATURE_LABELS_EN.get(self.feature, self.feature)}: included from the "
                f"{PLANS[need].label} plan. This shop is on {PLANS[self.plan_code].label}."
            ),
            **self.extra,
        }


class MemberLimitReached(Exception):
    """One more person would take the shop past its plan's user limit."""

    def __init__(self, limit: int, plan_code: str, **extra):
        super().__init__(f"member limit {limit} reached on {plan_code}")
        self.limit, self.plan_code, self.extra = limit, plan(plan_code).code, extra

    def detail(self) -> dict:
        return {
            "error": "member_limit_reached", "limit": self.limit, "plan": self.plan_code,
            "message": f"The shop has all {self.limit} users its {PLANS[self.plan_code].label} plan allows.",
            **self.extra,
        }


class PlanDowngradeRefused(Exception):
    """Owner, 24 ก.ย. 2569: never leave a shop over its limit — inactivate first."""

    def __init__(self, members: int, limit: int, plan_code: str):
        super().__init__(f"{members} active members, {plan_code} allows {limit}")
        self.members, self.limit, self.plan_code = members, limit, plan(plan_code).code

    def detail(self) -> dict:
        label = PLANS[self.plan_code].label
        n = self.members - self.limit
        return {
            "error": "plan_member_limit", "members": self.members, "limit": self.limit,
            "inactivate": n, "plan": self.plan_code, "plan_label": label,
            "message": DOWNGRADE_REFUSED_TH.format(n=n, plan=label),
        }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_plan_matrix.py -q
```
Expected: `57 passed` (41 parametrised rows + 16). If a row fails, **the table is right and the literal is wrong** — fix `PLANS`/`ROW_MAP`, never the test's parser.

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the four sales plans as one literal, tested cell by cell against the owner's table" && git log --oneline -1
```

---

### Task 2: Migration `0039_license_plan` — the column, the backfill, and "no shop is over its limit"

**Files:**
- Create: `database/alembic/versions/0039_license_plan.py`
- Modify: `data/chann_data/models.py:134-196` (`class License` — add `plan_code` after `status`, anchor `grep -n 'status: Mapped\[str\] = mapped_column(String(32), nullable=False, default="trial")' data/chann_data/models.py`)
- Modify: `data/chann_data/main.py:25` (`EXPECTED_MIGRATION_HEAD`)
- Modify: `tests/integration/test_round21c_closed_at.py:50` (it pins `"0038_deal_closed_at"` by value)
- Test: `tests/integration/test_round21d_migration.py`, `tests/unit/test_round21d_migration_literals.py`

**Interfaces:**
- Consumes: `chann_data.plans.PLANS`, `smallest_plan_for` (Task 1) — **only in tests**; the migration freezes its own literals (a migration never imports the Data tier's code; `0017_audit_actions.py` sets the precedent).
- Produces: `licenses.plan_code VARCHAR(24) NOT NULL DEFAULT 'pro'` + `ck_licenses_plan_code`; `License.plan_code: Mapped[str]`; the module-level pure function `plan_moves(rows) -> list[tuple]` in the migration file (tested); `EXPECTED_MIGRATION_HEAD = "0039_license_plan"`.

- [ ] **Step 1: Write the failing unit test for the frozen literals and the assertion**

Create `tests/unit/test_round21d_migration_literals.py`:

```python
"""Round 21D — migration 0039 freezes the plan codes and member limits (it
must not import the Data tier), so a test keeps the copies equal; and the
member-limit assertion is a pure function, tested here without a database."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from chann_data import plans

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "database/alembic/versions/0039_license_plan.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0039", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ids():
    m = _migration()
    assert m.revision == "0039_license_plan" and m.down_revision == "0038_deal_closed_at"


def test_the_frozen_copies_equal_the_matrix():
    m = _migration()
    assert m.PLAN_CODES == plans.PLAN_CODES
    assert m.MEMBER_LIMITS == tuple((code, plans.PLANS[code].members) for code in plans.PLAN_CODES)


def test_a_shop_within_its_limit_is_left_alone():
    assert _migration().plan_moves([("L1", "pro", 15), ("L2", "enterprise", 50)]) == []


def test_a_shop_over_its_limit_moves_to_the_smallest_plan_that_fits():
    moves = _migration().plan_moves([("L1", "pro", 16), ("L2", "pro", 51), ("L3", "starter", 6)])
    assert moves == [("L1", "pro", 16, "enterprise"), ("L2", "pro", 51, "enterprise_plus"),
                     ("L3", "starter", 6, "pro")]
    for _id, code, people, target in moves:
        assert target == plans.smallest_plan_for(people, at_least=code)


def test_enterprise_plus_is_never_over():
    assert _migration().plan_moves([("L1", "enterprise_plus", 5000)]) == []
```

- [ ] **Step 2: Write the failing integration test**

Create `tests/integration/test_round21d_migration.py`:

```python
"""Migration 0039_license_plan on a real database.

Its own module so it owns the schema: it steps back to 0038, writes rows
the old way, and upgrades (the 0026 pattern). Spec §11.3 / owner decision
Q1 (24 ก.ย. 2569): a plain shop → pro; an unrevoked API key, a multi-step
approval chain or > 15 active members → enterprise; > 50 → enterprise_plus.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _alembic(target: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    verb = "upgrade" if target == "head" else "downgrade"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", verb, target],
        cwd=str(ROOT / "database"), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic {verb} {target} failed:\n{result.stdout}\n{result.stderr}"
    return result


def _licence(conn, text, tag: str, name: str):
    lic = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO licenses (id, license_code, company_name, status, auto_accept_new_customers, created_at, updated_at) "
        "VALUES (:id, :code, :name, 'active', false, now(), now())"
    ), {"id": lic, "code": f"P{tag}{name}"[:32], "name": f"Plan {name} {tag}"})
    return lic


def _people(conn, text, lic, tag: str, n: int, *, status: str = "active", prefix: str = "a"):
    for i in range(n):
        uid = f"CHN-9{prefix}{tag}{i:03d}"
        conn.execute(text(
            "INSERT INTO chann_identities (chann_uid, line_user_id, primary_role, created_at, updated_at) "
            "VALUES (:uid, :line, 'sales', now(), now()) ON CONFLICT DO NOTHING"
        ), {"uid": uid, "line": f"line-{uid}"})
        conn.execute(text(
            "INSERT INTO license_members (id, license_id, chann_uid, role, channel, status, joined_at, created_at, updated_at) "
            "VALUES (:id, :lic, :uid, 'member', 'sales', :status, now(), now(), now())"
        ), {"id": uuid.uuid4(), "lic": lic, "uid": uid, "status": status})


def test_backfill_up_and_the_column_comes_off_again(migrated_db):
    from sqlalchemy import inspect, text

    _alembic("0038_deal_closed_at")
    tag = uuid.uuid4().hex[:5]
    with migrated_db.begin() as conn:
        plain = _licence(conn, text, tag, "plain")
        _people(conn, text, plain, tag, 3, prefix="p")
        keyed = _licence(conn, text, tag, "key")
        conn.execute(text(
            "INSERT INTO api_keys (id, license_id, name, key_prefix, key_hash, created_at, updated_at) "
            "VALUES (:id, :lic, 'ERP', 'chann_live_ab', :hash, now(), now())"
        ), {"id": uuid.uuid4(), "lic": keyed, "hash": uuid.uuid4().hex + uuid.uuid4().hex[:32]})
        revoked = _licence(conn, text, tag, "revoked")
        conn.execute(text(
            "INSERT INTO api_keys (id, license_id, name, key_prefix, key_hash, revoked_at, created_at, updated_at) "
            "VALUES (:id, :lic, 'old', 'chann_live_cd', :hash, now(), now(), now())"
        ), {"id": uuid.uuid4(), "lic": revoked, "hash": uuid.uuid4().hex + uuid.uuid4().hex[:32]})
        chain = _licence(conn, text, tag, "chain")
        conn.execute(text(
            "INSERT INTO approval_workflows (id, license_id, entity_type, rules_json, is_active, created_at, updated_at) "
            "VALUES (:id, :lic, 'service_report', CAST(:rules AS jsonb), true, now(), now())"
        ), {"id": uuid.uuid4(), "lic": chain,
            "rules": '{"steps": [{"order": 1, "approver_type": "role", "approver_ref": "cs"},'
                     ' {"order": 2, "approver_type": "role", "approver_ref": "admin"}]}'})
        one_step = _licence(conn, text, tag, "onestep")
        conn.execute(text(
            "INSERT INTO approval_workflows (id, license_id, entity_type, rules_json, is_active, created_at, updated_at) "
            "VALUES (:id, :lic, 'service_report', CAST(:rules AS jsonb), true, now(), now())"
        ), {"id": uuid.uuid4(), "lic": one_step,
            "rules": '{"steps": [{"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"}]}'})
        sixteen = _licence(conn, text, tag, "sixteen")
        _people(conn, text, sixteen, tag, 16, prefix="s")
        fifteen_plus_removed = _licence(conn, text, tag, "fifteen")
        _people(conn, text, fifteen_plus_removed, tag, 15, prefix="f")
        _people(conn, text, fifteen_plus_removed, tag, 4, status="removed", prefix="r")
        fifty_one = _licence(conn, text, tag, "big")
        _people(conn, text, fifty_one, tag, 51, prefix="b")

    result = _alembic("head")
    assert "0039_license_plan" in (result.stdout + result.stderr)   # the per-plan counts are printed

    with migrated_db.connect() as conn:
        codes = dict(conn.execute(text(
            "SELECT id, plan_code FROM licenses WHERE id = ANY(:ids)"
        ), {"ids": [plain, keyed, revoked, chain, one_step, sixteen, fifteen_plus_removed, fifty_one]}).all())
        assert codes == {
            plain: "pro", keyed: "enterprise", revoked: "pro", chain: "enterprise",
            one_step: "pro", sixteen: "enterprise",
            # Removed rows do not count: 15 active is still Pro.
            fifteen_plus_removed: "pro", fifty_one: "enterprise_plus",
        }
        constraints = {r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'licenses'::regclass"
        )).all()}
        assert "ck_licenses_plan_code" in constraints
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    from chann_data.main import EXPECTED_MIGRATION_HEAD
    assert head == EXPECTED_MIGRATION_HEAD == "0039_license_plan"

    # The CHECK refuses a fifth plan.
    with pytest.raises(Exception, match="ck_licenses_plan_code"):
        with migrated_db.begin() as conn:
            conn.execute(text("UPDATE licenses SET plan_code = 'gold' WHERE id = :id"), {"id": plain})

    # A new licence gets the server default.
    with migrated_db.begin() as conn:
        fresh = _licence(conn, text, tag, "fresh")
    with migrated_db.connect() as conn:
        assert conn.execute(text("SELECT plan_code FROM licenses WHERE id = :id"), {"id": fresh}).scalar_one() == "pro"

    # Downgrade drops the column and its constraint; upgrade restores head
    # for the rest of the module.
    _alembic("0038_deal_closed_at")
    assert "plan_code" not in {c["name"] for c in inspect(migrated_db).get_columns("licenses")}
    _alembic("head")
```

- [ ] **Step 3: Run both to verify they fail**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_migration_literals.py -q
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_migration.py -q
```
Expected: the unit file errors with `FileNotFoundError` on `0039_license_plan.py`; the integration test fails on `KeyError`/`UndefinedColumn plan_code` (no such column yet). If the integration run says `skipped`, `TEST_DATABASE_URL` is not set — **a skip is not a pass**; start `pg21c` (`docker start pg21c`) and rerun.

- [ ] **Step 4: Write the migration**

Create `database/alembic/versions/0039_license_plan.py`:

```python
"""A licence has a plan (round 21D).

Owner, 24 ก.ย. 2569: "ตัวควบคุมฟีเจอร์ตาม plan" — four plans, Starter · Pro ·
Enterprise · Enterprise Plus, from the sales table in
docs/superpowers/specs/2026-09-24-sales-plans-source.html.

Every existing shop becomes Pro in one statement (the server default), then
the backfill moves UP so nothing a shop uses today disappears (owner
decision Q1): an unrevoked API key, an active approval chain of more than
one step, or more than 15 active people → Enterprise; more than 50 →
Enterprise Plus. "Active people" is distinct chann_uid with an active
license_members row, any channel — a removed row does not count.

Owner decision Q2 (same day): no shop may be over its plan's user limit.
The rules above are built so that never happens here; `plan_moves` checks
it anyway and moves any shop that would be over to the smallest plan that
fits, printing the licence. The deploy log is the record of the counts.

The plan codes and limits are frozen here, not imported: a migration runs
the code of its own day (0017 set the precedent). A unit test keeps them
equal to chann_data.plans.

Revision ID: 0039_license_plan
Revises: 0038_deal_closed_at
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_license_plan"
down_revision = "0038_deal_closed_at"
branch_labels = None
depends_on = None

PLAN_CODES = ("starter", "pro", "enterprise", "enterprise_plus")
MEMBER_LIMITS = (("starter", 5), ("pro", 15), ("enterprise", 50), ("enterprise_plus", None))

_ACTIVE_PEOPLE = (
    "SELECT license_id, count(DISTINCT chann_uid) AS people FROM license_members "
    "WHERE status = 'active' GROUP BY license_id"
)
# Guarded with CASE, not AND: SQL does not promise to short-circuit, and
# jsonb_array_length raises on anything that is not an array.
_STEPS = (
    "CASE WHEN jsonb_typeof(rules_json->'steps') = 'array' "
    "THEN jsonb_array_length(rules_json->'steps') ELSE 0 END"
)


def plan_moves(rows) -> list[tuple]:
    """(license_id, plan_code, people) rows → (license_id, plan_code, people,
    target) for every licence over its plan's limit. Pure, so it is tested
    without a database."""
    limits = dict(MEMBER_LIMITS)
    order = [code for code, _ in MEMBER_LIMITS]
    moves = []
    for license_id, code, people in rows:
        limit = limits.get(code)
        if limit is None or people <= limit:
            continue
        target = next(c for c in order[order.index(code):] if limits[c] is None or people <= limits[c])
        moves.append((license_id, code, people, target))
    return moves


def upgrade() -> None:
    op.add_column(
        "licenses",
        sa.Column("plan_code", sa.String(24), nullable=False, server_default="pro"),
    )
    op.create_check_constraint(
        "ck_licenses_plan_code", "licenses",
        "plan_code IN ('starter', 'pro', 'enterprise', 'enterprise_plus')",
    )
    op.execute(
        "UPDATE licenses SET plan_code = 'enterprise' WHERE "
        "id IN (SELECT license_id FROM api_keys WHERE revoked_at IS NULL) "
        f"OR id IN (SELECT license_id FROM approval_workflows WHERE is_active AND {_STEPS} > 1) "
        f"OR id IN (SELECT license_id FROM ({_ACTIVE_PEOPLE}) AS c WHERE c.people > 15)"
    )
    op.execute(
        "UPDATE licenses SET plan_code = 'enterprise_plus' WHERE "
        f"id IN (SELECT license_id FROM ({_ACTIVE_PEOPLE}) AS c WHERE c.people > 50)"
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        f"SELECT l.id, l.plan_code, c.people FROM licenses l JOIN ({_ACTIVE_PEOPLE}) AS c ON c.license_id = l.id"
    )).all()
    for license_id, code, people, target in plan_moves(rows):
        print(f"0039_license_plan: licence {license_id} has {people} active people, over {code}'s "
              f"limit — moved to {target}", flush=True)
        conn.execute(sa.text("UPDATE licenses SET plan_code = :p WHERE id = :id"), {"p": target, "id": license_id})
    counts = conn.execute(sa.text(
        "SELECT plan_code, count(*) FROM licenses GROUP BY plan_code ORDER BY plan_code"
    )).all()
    print("0039_license_plan: licences per plan " + ", ".join(f"{c}={n}" for c, n in counts), flush=True)


def downgrade() -> None:
    op.drop_constraint("ck_licenses_plan_code", "licenses", type_="check")
    op.drop_column("licenses", "plan_code")
```

- [ ] **Step 5: The model column and the head**

In `data/chann_data/models.py`, inside `class License`, directly under the `status:` line:

```python
    # Round 21D — the sales plan (chann_data/plans.py). A constrained string,
    # not a FK: the four plans are code, and a `subscription_plans` billing
    # table (Master Spec 17.5, not built) would join on this same value.
    plan_code: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pro", server_default="pro",
    )
```

In `data/chann_data/main.py:25`: `EXPECTED_MIGRATION_HEAD = "0039_license_plan"`.

In `tests/integration/test_round21c_closed_at.py:50`, stop pinning the tip by value (the 0026 test explains why — every later migration broke it):

```python
        assert head == EXPECTED_MIGRATION_HEAD
```

- [ ] **Step 6: Run the three to verify they pass**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_migration_literals.py tests/unit/test_phase65_registration.py -q
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_migration.py tests/integration/test_round21c_closed_at.py tests/integration/test_member_channel_migration.py tests/integration/test_database_from_empty.py -q
```
Expected: all pass (`5 passed` for the literals file; the integration files `… passed`, none skipped).

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): migration 0039 gives every licence a plan, backfilled up so nothing in use disappears and no shop is over its limit" && git log --oneline -1
```

---

### Task 3: Data — the plan travels with every principal, one seat check, and a downgrade that refuses

**Files:**
- Create: `data/chann_data/repositories/plan_repo.py`
- Modify: `data/chann_data/cache.py:142-162` (add `k_license_plan` after `k_license_setting`; add its pattern to `k_license_patterns`)
- Modify: `data/chann_data/schemas.py` — `MembershipOut` (`:28`), `ApiKeyResolveOut` (`:1602`), `TenantSummaryOut` (`:1485`), `TenantUpdateIn` (`:503`)
- Modify: `data/chann_data/routers/internal.py` — imports (`:19-45`); `list_memberships` (`:595-653`); `set_member_status` (`:733-806`); `put_license_setting` (`:1154`) and `delete_license_setting` (anchor `grep -n '^def delete_license_setting' data/chann_data/routers/internal.py`); `_phase65_http_error` (`:1808`); `platform_tenants` / `platform_tenant` / `platform_tenant_update` / `_platform_error` (`:6309-6391`); `resolve_api_key` (`:6759-6777`)
- Modify: `data/chann_data/repositories/phase65.py:303-360` (`redeem_invite` — seat check)
- Modify: `data/chann_data/repositories/tenant_scope.py:145-170` (`MemberRepository.set_status` — seat check on reactivation)
- Modify: `data/chann_data/repositories/phase18.py` — `_summary` (`:87-97`), `tenants` (`:99-115`), `tenant` (`:117-151`), `EDITABLE`/`update` (`:155-179`), `move_member` (`:309`)
- Test: `tests/integration/test_round21d_plan_data.py`

**Interfaces:**
- Consumes (Task 1): `chann_data.plans` — `PLANS`, `FEATURE_KEYS`, `plan`, `resolve`, `UnknownPlan`, `PlanFeatureLocked`, `MemberLimitReached`, `PlanDowngradeRefused`. (Task 2): `License.plan_code`.
- Produces:
  - `chann_data.repositories.plan_repo.PlanRepository(session)` with `plan_code(license_id) -> str`, `ai_override(license_id)`, `payload(license_id) -> dict`, `active_people(license_id) -> int`, `is_active_person(license_id, chann_uid) -> bool`, `owner_uid(license_id) -> str | None`, `ai_used(license_id, *, month) -> int`, `usage(license_id, *, month) -> dict`, `require_feature(license_id, feature, **extra) -> None`, `require_seat(license_id, chann_uid) -> None`, `check_change(license_id, to_code) -> None`, `preview(license_id) -> dict`
  - `chann_data.cache.k_license_plan(license_id: str) -> str` = `"license_plan:{license_id}"`
  - in `routers/internal.py`: `_plan_payload(session, license_id) -> dict` (cached) and `_plan_refusal(exc) -> HTTPException | None` (403 `plan_required` · 409 `member_limit_reached` / `plan_member_limit` · 422 `unknown_plan`)
  - HTTP: `GET /internal/v1/licenses/{license_id}/plan` → `{"plan": <payload>, "usage": {"members", "members_limit", "ai_reports_used", "ai_reports_allowance", "ai_reports_month"}}`; `GET /internal/v1/platform/tenants/{license_id}/plan-preview` → `{"current": code, "previews": {code: {"plan","label","locks":[{"feature","counts"}],"members","limit","refused","inactivate"}}}`; `GET /internal/v1/platform/tenants?plan=` filter; `PATCH /internal/v1/platform/tenants/{id}` accepts `plan_code`
  - payload fields: `MembershipOut.plan: dict | None`, `ApiKeyResolveOut.plan: dict | None`, `TenantSummaryOut.plan_code: str`, `TenantSummaryOut.plan: dict | None`; `platform_tenant` adds `usage`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21d_plan_data.py`:

```python
"""Round 21D on Postgres — the plan payload, the seat check and the plan
change (spec §3, §5.7, §11.2–11.3; owner decision Q2, 24 ก.ย. 2569)."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chann_data.models import AuditLog, ChannIdentity, License, LicenseMember
from chann_data.plans import MemberLimitReached, PlanDowngradeRefused, UnknownPlan
from chann_data.repositories.phase18 import PlatformRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.plan_repo import PlanRepository
from chann_data.repositories.tenant_scope import MemberRepository, TenantScope

SECRET = {"X-Internal-Secret": "test-internal-secret"}


def _identity(s, uid: str) -> None:
    s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}", primary_role="sales"))


@pytest.fixture
def make_shop(migrated_db):
    """make_shop(plan_code, people) → (license_id, [chann_uid, ...]) with the
    owner first. Everyone joins through a real invite, so the seat check is
    on the road the fixture itself walks."""
    def make(plan_code: str, people: int):
        tag = uuid.uuid4().hex[:6]
        uids = [f"CHN-21D{tag}{i:03d}" for i in range(people)]
        with Session(migrated_db) as s:
            for uid in uids:
                _identity(s, uid)
            s.commit()
        with Session(migrated_db) as s:
            row = RegistrationRepository(s).create_license(
                company_name=f"Plan shop {tag}", created_by_chann_uid=uids[0])
            row.plan_code = plan_code
            license_id = row.id
            s.commit()
        for uid in uids[1:]:
            with Session(migrated_db) as s:
                reg = RegistrationRepository(s)
                invite = reg.create_invite(license_id, role="member")
                reg.redeem_invite(invite_code=invite.invite_code, chann_uid=uid)
                s.commit()
        return license_id, uids
    return make


def _join(engine, license_id, uid: str) -> None:
    with Session(engine) as s:
        _identity(s, uid)
        s.commit()
    with Session(engine) as s:
        reg = RegistrationRepository(s)
        invite = reg.create_invite(license_id, role="member")
        reg.redeem_invite(invite_code=invite.invite_code, chann_uid=uid)
        s.commit()


class TestTheSeat:
    def test_starter_takes_its_fifth_person_and_refuses_the_sixth(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 4)
        _join(migrated_db, license_id, f"CHN-21DX{uuid.uuid4().hex[:6]}")      # the 5th: limit reached, not passed
        with pytest.raises(MemberLimitReached) as caught:
            _join(migrated_db, license_id, f"CHN-21DY{uuid.uuid4().hex[:6]}")
        body = caught.value.detail()
        assert body["error"] == "member_limit_reached" and body["limit"] == 5 and body["plan"] == "starter"
        assert body["license_id"] == str(license_id) and body["owner_chann_uid"]
        with Session(migrated_db) as s:
            assert PlanRepository(s).active_people(license_id) == 5

    def test_a_removed_row_frees_its_seat_and_reactivating_takes_one(self, migrated_db, make_shop):
        license_id, uids = make_shop("starter", 5)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            MemberRepository(s).set_status(scope, uids[4], channel="sales", status="removed")
            s.commit()
        _join(migrated_db, license_id, f"CHN-21DZ{uuid.uuid4().hex[:6]}")      # back to 5
        with Session(migrated_db) as s:
            with pytest.raises(MemberLimitReached):
                MemberRepository(s).set_status(scope, uids[4], channel="sales", status="active")
            s.rollback()

    def test_one_person_on_two_channels_is_one_seat(self, migrated_db, make_shop):
        license_id, uids = make_shop("pro", 15)
        with Session(migrated_db) as s:
            # The owner redeeming a technician invite adds a ROW, not a person.
            PlanRepository(s).require_seat(license_id, uids[0])
            assert PlanRepository(s).active_people(license_id) == 15
            s.rollback()

    def test_enterprise_plus_has_no_cap(self, migrated_db, make_shop):
        license_id, _ = make_shop("enterprise_plus", 1)
        tag = uuid.uuid4().hex[:5]
        uids = [f"CHN-21DP{tag}{i:03d}" for i in range(500)]
        with Session(migrated_db) as s:
            for uid in uids:
                _identity(s, uid)
            s.commit()                     # identities first: license_members has an FK to them
        with Session(migrated_db) as s:
            s.add_all(LicenseMember(license_id=license_id, chann_uid=uid, role="member", status="active")
                      for uid in uids)
            s.commit()
        with Session(migrated_db) as s:
            PlanRepository(s).require_seat(license_id, f"CHN-21DQ{tag}")
            assert PlanRepository(s).active_people(license_id) == 501
            s.rollback()

    def test_two_people_racing_for_the_last_seat_one_wins(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 4)
        racers = [f"CHN-21DR{uuid.uuid4().hex[:6]}" for _ in range(2)]
        codes = []
        with Session(migrated_db) as s:
            for uid in racers:
                _identity(s, uid)
                codes.append(RegistrationRepository(s).create_invite(license_id, role="member").invite_code)
            s.commit()
        barrier, outcomes = threading.Barrier(2), []

        def race(uid, code):
            with sessionmaker(bind=migrated_db)() as s:
                barrier.wait()
                try:
                    RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=uid)
                    s.commit()
                    outcomes.append("joined")
                except MemberLimitReached:
                    s.rollback()
                    outcomes.append("refused")

        threads = [threading.Thread(target=race, args=pair) for pair in zip(racers, codes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(outcomes) == ["joined", "refused"]

    def test_moving_someone_into_a_full_shop_is_refused(self, migrated_db, make_shop):
        full, _ = make_shop("starter", 5)
        source, uids = make_shop("pro", 2)
        with Session(migrated_db) as s:
            with pytest.raises(MemberLimitReached):
                PlatformRepository(s).move_member(source, uids[1], target_license_id=full, role_name="member")
            s.rollback()


class TestThePlanChange:
    def test_a_downgrade_over_the_limit_is_refused_with_how_many_to_inactivate(self, migrated_db, make_shop):
        license_id, uids = make_shop("pro", 9)
        with Session(migrated_db) as s:
            with pytest.raises(PlanDowngradeRefused) as caught:
                PlatformRepository(s).update(license_id, {"plan_code": "starter"})
            s.rollback()
        assert caught.value.detail()["message"] == "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter"
        with Session(migrated_db) as s:
            assert s.get(License, license_id).plan_code == "pro"          # unchanged
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            for uid in uids[5:]:                                          # inactivate four
                MemberRepository(s).set_status(scope, uid, channel="sales", status="removed")
            s.commit()
        with Session(migrated_db) as s:
            before, row = PlatformRepository(s).update(license_id, {"plan_code": "starter"})
            assert before == {"plan_code": "pro"} and row.plan_code == "starter"
            s.commit()

    def test_an_unknown_plan_is_refused(self, migrated_db, make_shop):
        license_id, _ = make_shop("pro", 1)
        with Session(migrated_db) as s:
            with pytest.raises(UnknownPlan):
                PlatformRepository(s).update(license_id, {"plan_code": "gold"})
            s.rollback()

    def test_the_payload_and_usage(self, migrated_db, make_shop):
        license_id, _ = make_shop("starter", 3)
        with Session(migrated_db) as s:
            repo = PlanRepository(s)
            assert repo.payload(license_id)["code"] == "starter"
            assert repo.usage(license_id, month="2026-09") == {
                "members": 3, "members_limit": 5, "ai_reports_used": 0,
                "ai_reports_allowance": 0, "ai_reports_month": "2026-09",
            }

    def test_the_preview_counts_what_would_lock(self, migrated_db, make_shop):
        license_id, _ = make_shop("pro", 9)
        with Session(migrated_db) as s:
            out = PlanRepository(s).preview(license_id)
        assert out["current"] == "pro" and set(out["previews"]) == {"starter", "enterprise", "enterprise_plus"}
        starter = out["previews"]["starter"]
        assert {lock["feature"] for lock in starter["locks"]} == {
            "feature.customer_line_link", "feature.live_chat", "feature.service",
            "feature.warranty", "feature.custom_documents", "feature.custom_roles"}
        assert starter["refused"] is True and starter["inactivate"] == 4
        assert out["previews"]["enterprise"]["locks"] == [] and out["previews"]["enterprise"]["refused"] is False


class TestTheInternalRoutes:
    def _http(self, migrated_db, monkeypatch):
        from fastapi.testclient import TestClient

        from chann_data.config import settings
        from chann_data.db import get_session
        from chann_data.main import app

        monkeypatch.setattr(settings, "admin_secret", "test-internal-secret")
        TestSession = sessionmaker(bind=migrated_db, future=True)

        def override_session():
            session = TestSession()
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
        return TestClient(app)

    def test_the_membership_carries_the_plan_and_a_change_is_seen_at_once(
        self, migrated_db, monkeypatch, make_shop, memory_cache,
    ):
        http = self._http(migrated_db, monkeypatch)
        license_id, uids = make_shop("pro", 2)
        rows = http.get(f"/internal/v1/identities/{uids[1]}/memberships?oa=sales", headers=SECRET).json()
        assert rows[0]["plan"]["code"] == "pro"                    # now cached under license_plan:<id>
        out = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "enterprise"},
                         headers={**SECRET, "X-Actor-Id": "00000000-0000-0000-0000-000000000001"})
        assert out.status_code == 200, out.text
        assert out.json()["plan_code"] == "enterprise" and out.json()["plan"]["code"] == "enterprise"
        rows = http.get(f"/internal/v1/identities/{uids[1]}/memberships?oa=sales", headers=SECRET).json()
        assert rows[0]["plan"]["code"] == "enterprise"             # k_license_plan was invalidated
        with Session(migrated_db) as s:
            audit = s.execute(select(AuditLog).where(
                AuditLog.entity_id == license_id, AuditLog.action == "update",
            ).order_by(AuditLog.created_at.desc())).scalars().first()
            assert audit.field_changes["plan_code"] == {"old": "pro", "new": "enterprise"}

    def test_the_route_refuses_a_downgrade_with_409_and_the_sentence(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("pro", 7)
        out = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "starter"}, headers=SECRET)
        assert out.status_code == 409
        assert out.json()["detail"] == {
            "error": "plan_member_limit", "members": 7, "limit": 5, "inactivate": 2,
            "plan": "starter", "plan_label": "Starter",
            "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 2 คนก่อนลดแพ็กเกจเป็น Starter",
        }
        bad = http.patch(f"/internal/v1/platform/tenants/{license_id}", json={"plan_code": "gold"}, headers=SECRET)
        assert bad.status_code == 422 and bad.json()["detail"]["error"] == "unknown_plan"

    def test_a_redeem_past_the_limit_is_409_with_the_limit(self, migrated_db, monkeypatch, make_shop):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("starter", 5)
        uid = f"CHN-21DH{uuid.uuid4().hex[:6]}"
        with Session(migrated_db) as s:
            _identity(s, uid)
            code = RegistrationRepository(s).create_invite(license_id, role="member").invite_code
            s.commit()
        out = http.post("/internal/v1/invites/redeem", json={"invite_code": code, "chann_uid": uid}, headers=SECRET)
        assert out.status_code == 409 and out.json()["detail"]["error"] == "member_limit_reached"

    def test_the_plan_route_and_the_list_filter(self, migrated_db, monkeypatch, make_shop, memory_cache):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("starter", 2)
        out = http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()
        assert out["plan"]["code"] == "starter" and out["usage"]["members"] == 2
        listed = http.get("/internal/v1/platform/tenants?plan=starter", headers=SECRET).json()
        assert str(license_id) in {row["id"] for row in listed}
        assert {row["plan_code"] for row in listed} == {"starter"}

    def test_an_override_reaches_the_payload_at_once(self, migrated_db, monkeypatch, make_shop, memory_cache):
        http = self._http(migrated_db, monkeypatch)
        license_id, _ = make_shop("pro", 1)
        assert http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()["plan"]["limits"]["ai_reports_per_month"] == 30
        put = http.put(f"/internal/v1/licenses/{license_id}/settings/ai_chart_quota",
                       json={"setting_value": 45}, headers=SECRET)
        assert put.status_code == 200, put.text
        assert http.get(f"/internal/v1/licenses/{license_id}/plan", headers=SECRET).json()["plan"]["limits"]["ai_reports_per_month"] == 45

    def test_the_api_key_resolution_carries_the_plan(self, migrated_db, monkeypatch, make_shop):
        from chann_data.repositories.api_keys import ApiKeyRepository, hash_key
        from chann_data.routers import internal

        http = self._http(migrated_db, monkeypatch)
        monkeypatch.setattr(internal, "rate_window_remaining", lambda *a, **k: 599)
        license_id, uids = make_shop("enterprise", 1)
        with Session(migrated_db) as s:
            _row, raw = ApiKeyRepository(s).create(TenantScope(license_id=license_id), name="ERP", created_by_chann_uid=uids[0])
            s.commit()
        out = http.post("/internal/v1/api-keys/resolve", json={"key_hash": hash_key(raw)}, headers=SECRET).json()
        assert out["plan"]["code"] == "enterprise"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_plan_data.py -q
```
Expected: collection ERROR `ModuleNotFoundError: No module named 'chann_data.repositories.plan_repo'`.

- [ ] **Step 3: The repository**

Create `data/chann_data/repositories/plan_repo.py`:

```python
"""Round 21D — a licence's plan, read from the database.

The matrix is chann_data/plans.py. This reads which plan a licence is on,
the admin's AI-credit override and how many people hold a seat, and makes
the checks that must share a transaction with the change they guard: a
seat (a join, a reactivation, a move), a plan change (owner, 24 ก.ย. 2569:
a downgrade is refused while the shop is over the target's limit) and a
feature the Data tier itself acts on.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ApiKey, ApprovalWorkflow, ChatSession, CustomerLicenseLink, CustomRole, DocumentTemplate,
    License, LicenseMember, LicenseSetting, ServiceTicket, Warranty,
)
from ..permissions import DEFAULT_ROLE_TEMPLATES
from ..plans import (
    FEATURE_KEYS, PLANS, MemberLimitReached, PlanDowngradeRefused, PlanFeatureLocked, UnknownPlan,
    plan, resolve,
)

#: The same two settings rows LicenseSettingRepository has always used.
QUOTA_KEY = "ai_chart_quota"
USAGE_KEY = "ai_chart_usage"
OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")


class PlanRepository:
    def __init__(self, session: Session):
        self._s = session

    # ------------------------------------------------------------ reads

    def plan_code(self, license_id) -> str:
        code = self._s.execute(
            select(License.plan_code).where(License.id == license_id)
        ).scalar_one_or_none()
        return plan(code).code

    def ai_override(self, license_id):
        return self._s.execute(
            select(LicenseSetting.setting_value).where(
                LicenseSetting.license_id == license_id, LicenseSetting.setting_key == QUOTA_KEY,
            )
        ).scalar_one_or_none()

    def payload(self, license_id) -> dict:
        return resolve(self.plan_code(license_id), ai_override=self.ai_override(license_id))

    def active_people(self, license_id) -> int:
        """Distinct PEOPLE with an active row on any channel — a person who
        is both sales and technician is one user; customers have no row."""
        return int(self._s.execute(
            select(func.count(func.distinct(LicenseMember.chann_uid))).where(
                LicenseMember.license_id == license_id, LicenseMember.status == "active",
            )
        ).scalar_one() or 0)

    def is_active_person(self, license_id, chann_uid: str) -> bool:
        return self._s.execute(
            select(LicenseMember.id).where(
                LicenseMember.license_id == license_id,
                LicenseMember.chann_uid == chann_uid,
                LicenseMember.status == "active",
            ).limit(1)
        ).first() is not None

    def owner_uid(self, license_id) -> str | None:
        return self._s.execute(
            select(LicenseMember.chann_uid)
            .join(CustomRole, (CustomRole.license_id == LicenseMember.license_id)
                  & (CustomRole.role_name == LicenseMember.role))
            .where(LicenseMember.license_id == license_id, LicenseMember.status == "active",
                   CustomRole.is_owner.is_(True))
            .order_by(LicenseMember.created_at).limit(1)
        ).scalar_one_or_none()

    def ai_used(self, license_id, *, month: str) -> int:
        value = self._s.execute(
            select(LicenseSetting.setting_value).where(
                LicenseSetting.license_id == license_id, LicenseSetting.setting_key == USAGE_KEY,
            )
        ).scalar_one_or_none()
        if not isinstance(value, dict) or str(value.get("month") or "") != month:
            return 0
        try:
            return int(value.get("used") or 0)
        except (TypeError, ValueError):
            return 0

    def usage(self, license_id, *, month: str) -> dict:
        limits = self.payload(license_id)["limits"]
        return {
            "members": self.active_people(license_id),
            "members_limit": limits["members"],
            "ai_reports_used": self.ai_used(license_id, month=month),
            "ai_reports_allowance": limits["ai_reports_per_month"],
            "ai_reports_month": month,
        }

    # ------------------------------------------------------------ checks

    def require_feature(self, license_id, feature: str, **extra) -> None:
        code = self.plan_code(license_id)
        if not PLANS[code].has(feature):
            raise PlanFeatureLocked(feature, code, **extra)

    def require_seat(self, license_id, chann_uid: str) -> None:
        """One more PERSON would join. Locks the licence row first, so two
        redemptions in the same second queue here and the second one counts
        the first (spec §5.7). A person who already holds an active row on
        any channel takes no new seat."""
        row = self._s.execute(
            select(License).where(License.id == license_id).with_for_update()
        ).scalar_one_or_none()
        if row is None or self.is_active_person(license_id, chann_uid):
            return
        current = plan(row.plan_code)
        if current.members is None:
            return
        if self.active_people(license_id) >= current.members:
            raise MemberLimitReached(
                current.members, current.code, license_id=str(license_id),
                company_name=row.company_name, owner_chann_uid=self.owner_uid(license_id),
            )

    def check_change(self, license_id, to_code: str) -> None:
        """Owner, 24 ก.ย. 2569: refuse a downgrade while active members
        exceed the target's limit — nobody is left over a limit."""
        if to_code not in PLANS:
            raise UnknownPlan(f"unknown plan '{to_code}'")
        self._s.execute(select(License.id).where(License.id == license_id).with_for_update())
        limit = PLANS[to_code].members
        people = self.active_people(license_id)
        if limit is not None and people > limit:
            raise PlanDowngradeRefused(people, limit, to_code)

    # ------------------------------------------------------------ the admin's preview

    def preview(self, license_id) -> dict:
        current = PLANS[self.plan_code(license_id)]
        people = self.active_people(license_id)
        counts = self._feature_counts(license_id)
        previews = {}
        for code, target in PLANS.items():
            if code == current.code:
                continue
            refused = target.members is not None and people > target.members
            previews[code] = {
                "plan": code, "label": target.label,
                "locks": [{"feature": key, "counts": counts[key]}
                          for key in FEATURE_KEYS if current.has(key) and not target.has(key)],
                "members": people, "limit": target.members,
                "refused": refused, "inactivate": (people - target.members) if refused else 0,
            }
        return {"current": current.code, "previews": previews}

    def _count(self, model, *where) -> int:
        return int(self._s.execute(select(func.count()).select_from(model).where(*where)).scalar_one() or 0)

    def _feature_counts(self, license_id) -> dict[str, dict[str, int]]:
        custom_roles = [
            name for name in self._s.execute(
                select(CustomRole.role_name).where(CustomRole.license_id == license_id)
            ).scalars()
            if name not in DEFAULT_ROLE_TEMPLATES
        ]
        workflow = self._s.execute(
            select(ApprovalWorkflow.rules_json).where(
                ApprovalWorkflow.license_id == license_id, ApprovalWorkflow.is_active.is_(True),
            )
        ).scalars().first() or {}
        technicians = int(self._s.execute(
            select(func.count(func.distinct(LicenseMember.chann_uid))).where(
                LicenseMember.license_id == license_id, LicenseMember.status == "active",
                LicenseMember.channel == "technician",
            )
        ).scalar_one() or 0)
        return {
            "feature.service": {
                "open_tickets": self._count(ServiceTicket, ServiceTicket.license_id == license_id,
                                            ServiceTicket.status.in_(OPEN_TICKET_STATUSES)),
                "technicians": technicians,
            },
            "feature.warranty": {"warranties": self._count(Warranty, Warranty.license_id == license_id)},
            "feature.live_chat": {"open_chats": self._count(
                ChatSession, ChatSession.license_id == license_id, ChatSession.status == "open")},
            "feature.customer_line_link": {"linked_customers": self._count(
                CustomerLicenseLink, CustomerLicenseLink.license_id == license_id)},
            "feature.custom_documents": {"templates": self._count(
                DocumentTemplate, DocumentTemplate.license_id == license_id, DocumentTemplate.is_active.is_(True))},
            "feature.custom_roles": {"custom_roles": len(custom_roles)},
            "feature.multi_level_approval": {"steps": len(list((workflow or {}).get("steps") or []))},
            "feature.external_api": {"live_keys": self._count(
                ApiKey, ApiKey.license_id == license_id, ApiKey.revoked_at.is_(None))},
        }
```

- [ ] **Step 4: The cache key**

In `data/chann_data/cache.py`, after `k_license_setting`:

```python
def k_license_plan(license_id: str) -> str:
    """Round 21D — the resolved plan of one licence. Its own key, never
    folded into k_permissions (which is per person): a plan change must reach
    every member at once by invalidating ONE key (spec §3.2)."""
    return f"license_plan:{license_id}"
```

and add `f"license_plan:{license_id}",` to the tuple `k_license_patterns` returns.

- [ ] **Step 5: The schemas**

In `data/chann_data/schemas.py`:
- `MembershipOut` (after `member_id`): `plan: dict | None = None  # round 21D — chann_data.plans.resolve()`
- `ApiKeyResolveOut` (after `remaining`): `plan: dict | None = None`
- `TenantSummaryOut` (after `last_activity_at`): `plan_code: str = "pro"` and `plan: dict | None = None`
- `TenantUpdateIn` (after `admin_notes`): `plan_code: str | None = None  # validated by PlanRepository.check_change → 422 unknown_plan`

- [ ] **Step 6: The seat check on every road a person joins by**

`data/chann_data/repositories/phase65.py`, `redeem_invite` — add the import at the top of the function beside `channel_for_role`, and call the check **before** both the reactivation and the new row:

```python
        from ..permissions import channel_for_role
        from .plan_repo import PlanRepository
```
```python
        if existing is not None:
            if existing.status != "active":
                # Reactivating is a join (round 21D): it takes a seat.
                PlanRepository(self._s).require_seat(invite.license_id, chann_uid)
                existing.status = "active"
                existing.role = invite.role
                invite.used_count += 1
                self._s.flush()
            return existing

        if invite.used_count >= invite.max_uses:
            raise RegistrationConflict("invite code has no uses left")

        PlanRepository(self._s).require_seat(invite.license_id, chann_uid)
        member = LicenseMember(
```

`data/chann_data/repositories/tenant_scope.py`, `MemberRepository.set_status` — after the owner check, before `unassigned`:

```python
        if status == "active" and member.status != "active":
            # Round 21D: bringing someone back takes a seat like a join does.
            from .plan_repo import PlanRepository

            PlanRepository(self._s).require_seat(scope.license_id, chann_uid)
```

`data/chann_data/repositories/phase18.py`, `move_member` — directly after the `existing = ...` lookup (anchor `grep -n 'existing = self._s.execute(' data/chann_data/repositories/phase18.py`) and before any row is written:

```python
        # Round 21D: arriving at the target takes a seat there.
        from .plan_repo import PlanRepository

        PlanRepository(self._s).require_seat(target_license_id, chann_uid)
```

- [ ] **Step 7: The plan change, the list and the tenant page in `PlatformRepository`**

In `data/chann_data/repositories/phase18.py`:

```python
    def _summary(self, row: License) -> dict:
        from .plan_repo import PlanRepository

        owner_uid, owner_name = self._owner(row.id)
        return {
            "id": row.id, "license_code": row.license_code, "company_name": row.company_name,
            "company_code": row.company_code, "status": row.status,
            "expires_at": row.expires_at, "deleted_at": row.deleted_at, "created_at": row.created_at,
            "owner_chann_uid": owner_uid, "owner_name": owner_name,
            # Round 21D — the plan, resolved (with the shop's AI override).
            "plan_code": row.plan_code, "plan": PlanRepository(self._s).payload(row.id),
            **self._counts(row.id),
        }
```

`tenants(self, *, q=None, status=None, plan=None, limit=200)` — after the status filter:

```python
        if plan:
            query = query.where(License.plan_code == plan)
```

`tenant()` — add to the returned dict (the Bangkok month, the same clock `consume_ai_chart`'s callers use):

```python
            "usage": PlanRepository(self._s).usage(
                license_id, month=bangkok_today().strftime("%Y-%m")),
```
with `from .localtime import bangkok_today` and `from .plan_repo import PlanRepository` imported at the top of `tenant()`.

`EDITABLE` gains `"plan_code",` (comment: `# Round 21D: the sales plan — only this admin route writes it.`), and in `update()`, directly after the `unknown = …` check:

```python
        if "plan_code" in changes:
            # Round 21D (owner, 24 ก.ย. 2569): unknown code → UnknownPlan
            # (422); a downgrade over the target's user limit →
            # PlanDowngradeRefused (409). The licence row is locked first.
            from .plan_repo import PlanRepository

            PlanRepository(self._s).check_change(license_id, str(changes["plan_code"] or ""))
```

- [ ] **Step 8: The routes**

In `data/chann_data/routers/internal.py`:

Imports — add `k_license_plan` to the `from ..cache import (...)` list, and:

```python
from ..plans import MemberLimitReached, PlanDowngradeRefused, PlanFeatureLocked, UnknownPlan
from ..repositories.plan_repo import PlanRepository
```

Helpers, placed directly above `def list_memberships` (anchor `grep -n '^def list_memberships' data/chann_data/routers/internal.py`):

```python
def _plan_payload(session: Session, license_id) -> dict:
    """Round 21D — a licence's resolved plan, cached under its own
    licence-level key (never inside k_permissions, spec §3.2). Invalidated
    by the admin's plan PATCH and by the ai_chart_quota setting."""
    value = cache.get_or_load(
        k_license_plan(str(license_id)),
        settings.cache_ttl_license_setting_s,
        lambda: PlanRepository(session).payload(license_id),
        CacheFailureMode.FALLBACK_DB,
    )
    return value if isinstance(value, dict) else PlanRepository(session).payload(license_id)


def _plan_refusal(exc: Exception) -> HTTPException | None:
    """The three plan refusals in the shapes every tier reads (spec §5.2)."""
    if isinstance(exc, PlanFeatureLocked):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail())
    if isinstance(exc, (MemberLimitReached, PlanDowngradeRefused)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail())
    if isinstance(exc, UnknownPlan):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"error": "unknown_plan", "message": str(exc)})
    return None
```

`list_memberships` — both constructors gain the plan: in the customer branch `plan=_plan_payload(session, shop.id),`; in the members branch `plan=_plan_payload(session, m.license_id),`.

`set_member_status` (tenant route) — add before `except MemberNotFound`:

```python
    except MemberLimitReached as exc:
        session.rollback()
        raise _plan_refusal(exc)
```

`put_license_setting` and `delete_license_setting` — directly after each `session.commit()`:

```python
        if setting_key == "ai_chart_quota":
            # Round 21D: the override is part of the resolved plan.
            cache.invalidate(k_license_plan(str(license_id)))
```

`_phase65_http_error` — first lines of the function:

```python
    refusal = _plan_refusal(exc)
    if refusal is not None:
        return refusal
```

`_platform_error` — the same three lines first.

New routes, placed directly after `platform_tenant` (anchor `grep -n '^def platform_tenant(' data/chann_data/routers/internal.py`) and directly after `get_authorization_context` respectively:

```python
@router.get("/platform/tenants/{license_id}/plan-preview")
def platform_plan_preview(license_id: uuid.UUID, session: Session = Depends(get_session)):
    """Round 21D — for the admin's confirm dialog: what each other plan
    would lock (with counts) and whether its user limit refuses the change."""
    if session.get(License, license_id) is None:
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})
    return PlanRepository(session).preview(license_id)
```

```python
@router.get("/licenses/{license_id}/plan")
def license_plan(license_id: uuid.UUID, session: Session = Depends(get_session)):
    """Round 21D — the shop's plan and how much of it is in use. Usage is
    counted fresh (it moves with every join); the plan comes from cache."""
    from ..repositories.localtime import bangkok_today

    if session.get(License, license_id) is None:
        raise HTTPException(status_code=404, detail="license not found")
    month = bangkok_today().strftime("%Y-%m")
    return {"plan": _plan_payload(session, license_id),
            "usage": PlanRepository(session).usage(license_id, month=month)}
```

`platform_tenants` — add the `plan: str | None = None` query parameter and pass `plan=plan`.

`platform_tenant` — add `"plan_code"` and `"plan"` travel through `TenantSummaryOut` automatically; add `"usage": data.get("usage"),` to the `payload.update({...})` dict.

`platform_tenant_update` — replace the `try/except` around `PlatformRepository(session).update(...)` with:

```python
    try:
        before, row = PlatformRepository(session).update(license_id, changes)
    except PlatformNotFound:
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})
    except (UnknownPlan, PlanDowngradeRefused) as exc:
        session.rollback()
        raise _plan_refusal(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_tenant_update", "message": str(exc)})
```

(the `UnknownPlan` branch **must** come before `ValueError` — it is a subclass) and after `session.commit()`:

```python
    if "plan_code" in changes:
        cache.invalidate(k_license_plan(str(license_id)))
```

`resolve_api_key` — `plan=_plan_payload(session, row.license_id),` in the `ApiKeyResolveOut(...)`.

- [ ] **Step 9: Run the test to verify it passes, then everything these files touch**

```bash
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_plan_data.py -q
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_platform_admin_data.py tests/integration/test_tenant_edit.py tests/integration/test_member_channel.py tests/integration/test_round21b_api_keys.py tests/integration/test_shop_code_links_data.py -q
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_phase65_registration.py tests/boundary -q
```
Expected: all pass. `test_platform_admin_data.py` builds `TenantSummaryOut` from `_summary`, so the two new fields must be present there — if it fails on a missing key, the summary is wrong, not the test.

- [ ] **Step 10: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the Data tier sends the plan with every principal, counts seats in the join's own transaction, and refuses a downgrade that would leave a shop over its limit" && git log --oneline -1
```

---

### Task 4: Data — the feature checks the Data tier owns

The checks that act inside a Data-tier transaction or on a road no principal walks: a technician invite (minted and redeemed), a customer linking by company code, assigning a custom role, saving and opening a multi-step approval chain, and the platform chat sweep. Each refuses with `PlanFeatureLocked` → **403 `plan_required`** through `_plan_refusal` (Task 3).

**Files:**
- Modify: `data/chann_data/repositories/plan_repo.py` (add `licences_without`)
- Modify: `data/chann_data/repositories/phase65.py` — `create_invite` (`:202`), `redeem_invite` (`:303`), `link_customer` (anchor `grep -n 'def link_customer' data/chann_data/repositories/phase65.py`)
- Modify: `data/chann_data/repositories/phase14.py:138-185` (`open_steps_for_report`)
- Modify: `data/chann_data/repositories/phase15.py:292-363` (`sla_overdue`, `time_out`)
- Modify: `data/chann_data/routers/internal.py` — `_phase2_http_error` (`:955`), `set_member_role` (`:1114`), `_approval_error` (`:5108`), `replace_approval_workflow` (`:5170`), `open_approval_steps` (`:5195`), `sweep_chat_sessions` (`:6147`)
- Test: `tests/integration/test_round21d_feature_checks.py`

**Interfaces:**
- Consumes (Task 3): `PlanRepository.require_feature(license_id, feature, **extra)`, `PlanRepository.plan_code`, `_plan_refusal`. (Task 1): `PLANS`, `PlanFeatureLocked`.
- Produces:
  - `PlanRepository.licences_without(feature: str) -> list[uuid.UUID]`
  - `ApprovalRepository.open_steps_for_report(scope, report, *, max_steps: int | None = None) -> list[ApprovalStep]`
  - `ChatSessionRepository.sla_overdue(*, now=None, skip_licenses=())` and `.time_out(*, now=None, skip_licenses=())`
  - 403 bodies that carry `company_name` (technician redeem) and `company_name` + `company_phone` (customer link) for the OA sentences of spec §7 (Task 8 reads them)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21d_feature_checks.py`:

```python
"""Round 21D — the plan checks the Data tier makes itself (spec §4.1, §5.7,
§7.2, §7.3, §5.9). Each refusal is a PlanFeatureLocked, which every route
turns into 403 plan_required; nothing is written when it refuses."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chann_data.models import (
    ChannIdentity, CustomerLicenseLink, License, LicenseInvite, LicenseMember, ServiceReport,
)
from chann_data.plans import PlanFeatureLocked
from chann_data.repositories.phase14 import ApprovalRepository
from chann_data.repositories.phase2 import RoleRepository
from chann_data.repositories.phase65 import RegistrationRepository

from .test_phase14_approvals import _make_tenant, _submitted_report

SECRET = {"X-Internal-Secret": "test-internal-secret"}


def _set_plan(engine, license_id, code: str) -> None:
    with Session(engine) as s:
        s.get(License, license_id).plan_code = code
        s.commit()


@pytest.fixture
def shop(migrated_db):
    tag = uuid.uuid4().hex[:6]
    owner = f"CHN-21DF{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=owner, line_user_id=f"line-{owner}", primary_role="sales"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(company_name=f"ร้านแอร์ {tag}", created_by_chann_uid=owner)
        row.company_phone = "021234567"
        license_id, company_code = row.id, row.company_code
        s.commit()
    return migrated_db, license_id, company_code, tag


def _http(migrated_db, monkeypatch):
    from fastapi.testclient import TestClient

    from chann_data.config import settings
    from chann_data.db import get_session
    from chann_data.main import app

    monkeypatch.setattr(settings, "admin_secret", "test-internal-secret")
    TestSession = sessionmaker(bind=migrated_db, future=True)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setitem(app.dependency_overrides, get_session, override_session)
    return TestClient(app)


class TestTechnicianInvites:
    def test_starter_cannot_mint_one(self, shop):
        engine, license_id, _code, _tag = shop
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).create_invite(license_id, role="technician")
            s.rollback()
        assert caught.value.detail()["feature"] == "feature.service"
        with Session(engine) as s:
            RegistrationRepository(s).create_invite(license_id, role="member")   # a sales invite is fine
            s.rollback()

    def test_a_code_made_before_a_downgrade_is_refused_and_not_consumed(self, shop):
        engine, license_id, _code, tag = shop
        tech = f"CHN-21DT{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=tech, line_user_id=f"line-{tech}", primary_role="technician"))
            code = RegistrationRepository(s).create_invite(license_id, role="technician").invite_code
            s.commit()
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=tech, oa="technician")
            s.rollback()
        assert caught.value.detail()["company_name"].startswith("ร้านแอร์")
        with Session(engine) as s:
            invite = s.execute(select(LicenseInvite).where(LicenseInvite.invite_code == code)).scalar_one()
            assert invite.used_count == 0
            assert s.execute(select(LicenseMember).where(LicenseMember.chann_uid == tech)).first() is None
        _set_plan(engine, license_id, "pro")                  # works again after the upgrade
        with Session(engine) as s:
            member = RegistrationRepository(s).redeem_invite(invite_code=code, chann_uid=tech, oa="technician")
            assert member.channel == "technician"
            s.commit()


class TestCustomerLink:
    def test_starter_refuses_the_link_and_writes_nothing(self, shop):
        engine, license_id, company_code, tag = shop
        customer = f"CHN-21DC{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=customer, line_user_id=f"line-{customer}", primary_role="customer"))
            s.commit()
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            with pytest.raises(PlanFeatureLocked) as caught:
                RegistrationRepository(s).link_customer(chann_uid=customer, company_code=company_code)
            s.rollback()
        body = caught.value.detail()
        assert body["feature"] == "feature.customer_line_link" and body["company_phone"] == "021234567"
        with Session(engine) as s:
            assert s.execute(select(CustomerLicenseLink).where(
                CustomerLicenseLink.chann_uid == customer)).first() is None


class TestCustomRoles:
    def test_assigning_a_custom_role_on_starter_is_403_and_a_standard_one_works(self, shop, monkeypatch):
        from chann_data.repositories.tenant_scope import TenantScope

        engine, license_id, _code, tag = shop
        http = _http(engine, monkeypatch)
        person = f"CHN-21DR{tag}"
        with Session(engine) as s:
            s.add(ChannIdentity(chann_uid=person, line_user_id=f"line-{person}", primary_role="sales"))
            s.flush()
            RoleRepository(s).create(TenantScope(license_id=license_id), "ช่างอาวุโส", {"customer.read"})
            s.add(LicenseMember(license_id=license_id, chann_uid=person, role="member", status="active"))
            s.commit()
        _set_plan(engine, license_id, "starter")
        out = http.patch(f"/internal/v1/licenses/{license_id}/members/{person}/role",
                         json={"role_name": "ช่างอาวุโส"}, headers=SECRET)
        assert out.status_code == 403 and out.json()["detail"]["error"] == "plan_required"
        assert out.json()["detail"]["feature"] == "feature.custom_roles"
        ok = http.patch(f"/internal/v1/licenses/{license_id}/members/{person}/role",
                        json={"role_name": "admin"}, headers=SECRET)
        assert ok.status_code == 200, ok.text


class TestMultiLevelApproval:
    TWO_STEPS = {"steps": [
        {"order": 1, "approver_type": "user", "approver_ref": "ticket_owner"},
        {"order": 2, "approver_type": "role", "approver_ref": "admin"},
    ]}

    def test_saving_two_steps_needs_enterprise(self, migrated_db, monkeypatch):
        tenant = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        license_id = tenant["scope"].license_id
        http = _http(migrated_db, monkeypatch)
        out = http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                       json={"rules_json": self.TWO_STEPS}, headers=SECRET)
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "feature.multi_level_approval"
        _set_plan(migrated_db, license_id, "enterprise")
        out = http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                       json={"rules_json": self.TWO_STEPS}, headers=SECRET)
        assert out.status_code == 200, out.text

    def test_on_pro_a_saved_chain_opens_step_one_only_and_a_report_mid_flow_keeps_its_steps(
        self, migrated_db, monkeypatch,
    ):
        tenant = _make_tenant(migrated_db, uuid.uuid4().hex[:6])
        license_id = tenant["scope"].license_id
        http = _http(migrated_db, monkeypatch)
        _set_plan(migrated_db, license_id, "enterprise")
        assert http.put(f"/internal/v1/licenses/{license_id}/approval-workflows/service_report",
                        json={"rules_json": self.TWO_STEPS}, headers=SECRET).status_code == 200
        _, before = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        opened = http.post(f"/internal/v1/licenses/{license_id}/service-reports/{before}/approval-steps",
                           headers=SECRET).json()
        assert len(opened) == 2
        _set_plan(migrated_db, license_id, "pro")
        _, after = _submitted_report(tenant, owner_member_id=tenant["members"]["cs"])
        opened = http.post(f"/internal/v1/licenses/{license_id}/service-reports/{after}/approval-steps",
                           headers=SECRET).json()
        assert [s["step_order"] for s in opened] == [1]
        with tenant["session"]() as s:
            assert len(ApprovalRepository(s).steps_for_entity(tenant["scope"], "service_report", before)) == 2


class TestTheRoundTrip:
    """Spec §11.3: Pro → Starter → Pro leaves every row byte-identical —
    a plan locks, it never deletes (spec §1)."""

    TABLES = ("service_tickets", "custom_roles", "role_permissions", "license_members", "approval_workflows",
              "document_templates", "chat_sessions", "warranties", "customer_license_links", "api_keys")

    def test_nothing_changes_on_the_way_down_and_back(self, shop):
        from sqlalchemy import text

        from chann_data.repositories.phase12 import ServiceTicketRepository
        from chann_data.repositories.phase18 import PlatformRepository
        from chann_data.repositories.tenant_scope import TenantScope

        engine, license_id, _code, _tag = shop
        scope = TenantScope(license_id=license_id)
        with Session(engine) as s:
            ServiceTicketRepository(s).create(scope, issue_description="แอร์ไม่เย็น")
            RoleRepository(s).create(scope, "ผู้ช่วยขาย", {"customer.read"})
            s.commit()

        def snapshot():
            with engine.connect() as conn:
                return {t: sorted(map(str, conn.execute(
                    text(f"SELECT * FROM {t} WHERE license_id = :id"), {"id": license_id}).all()))
                    for t in self.TABLES}

        before = snapshot()
        for code in ("starter", "pro"):
            with Session(engine) as s:
                PlatformRepository(s).update(license_id, {"plan_code": code})
                s.commit()
        assert snapshot() == before


class TestTheChatSweep:
    def test_a_starter_shop_is_skipped(self, shop):
        from chann_data.repositories.phase15 import ChatSessionRepository
        from chann_data.repositories.plan_repo import PlanRepository

        engine, license_id, _code, _tag = shop
        _set_plan(engine, license_id, "starter")
        with Session(engine) as s:
            skip = PlanRepository(s).licences_without("feature.live_chat")
            assert license_id in skip
            assert all(row.license_id != license_id
                       for row in ChatSessionRepository(s).sla_overdue(skip_licenses=skip))
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_feature_checks.py -q
```
Expected: FAIL — `DID NOT RAISE PlanFeatureLocked` (technician invite, link), 200 instead of 403 (custom role, two steps), `len(opened) == 2` on Pro, `AttributeError: licences_without`. Before writing code, confirm `steps_for_entity`'s real signature with `grep -n 'def steps_for_entity' -A3 data/chann_data/repositories/phase14.py` and match the call in the test to it.

- [ ] **Step 3: `licences_without`**

Append to `PlanRepository` in `data/chann_data/repositories/plan_repo.py`:

```python
    def licences_without(self, feature: str) -> list:
        """Every licence whose plan lacks `feature` — for the platform's own
        sweeps, which have no principal (spec §5.9)."""
        codes = [code for code, p in PLANS.items() if not p.has(feature)]
        if not codes:
            return []
        return list(self._s.execute(select(License.id).where(License.plan_code.in_(codes))).scalars())
```

- [ ] **Step 4: Invites and the customer link (`phase65.py`)**

`create_invite` — first lines of the body, before `if max_uses < 1` (so no self-healed role row is written for a refused invite):

```python
        # Round 21D: a technician is feature.service; a role the shop made
        # itself is feature.custom_roles (standard roles stay on every plan).
        from ..permissions import DEFAULT_ROLE_TEMPLATES, channel_for_role
        from .plan_repo import PlanRepository

        plan_checks = PlanRepository(self._s)
        if channel_for_role(role) == "technician":
            plan_checks.require_feature(license_id, "feature.service")
        if role not in DEFAULT_ROLE_TEMPLATES:
            plan_checks.require_feature(license_id, "feature.custom_roles")
```

(the function's later `from ..permissions import DEFAULT_ROLE_TEMPLATES` stays; the duplicate import is harmless — or delete the later one, same name.)

`redeem_invite` — directly after the `if oa is not None and oa != channel:` refusal, before the `existing = …` lookup:

```python
        if channel == "technician":
            # Spec §7.2: a code made before a downgrade is refused, not
            # consumed — it works again after an upgrade until it expires.
            shop = self._s.get(License, invite.license_id)
            PlanRepository(self._s).require_feature(
                invite.license_id, "feature.service",
                company_name=shop.company_name if shop is not None else "",
            )
```

`link_customer` — directly after the `INACTIVE_STATUSES` refusal, before the `existing = …` lookup:

```python
        # Spec §7.3: the Customer OA is feature.customer_line_link. No link
        # row is written; the refusal carries what the customer is told.
        from .plan_repo import PlanRepository

        PlanRepository(self._s).require_feature(
            license_row.id, "feature.customer_line_link",
            company_name=license_row.company_name, company_phone=license_row.company_phone or "",
        )
```

- [ ] **Step 5: Custom-role assignment and the error mappers (`internal.py`)**

`_phase2_http_error` and `_approval_error` — first lines of each:

```python
    refusal = _plan_refusal(exc)
    if refusal is not None:
        return refusal
```

`set_member_role` — first line inside `try:`:

```python
        if payload.role_name not in DEFAULT_ROLE_TEMPLATES:
            # Spec §4.1: members KEEP a custom role after a downgrade;
            # assigning one needs feature.custom_roles.
            PlanRepository(session).require_feature(license_id, "feature.custom_roles")
```

with `DEFAULT_ROLE_TEMPLATES` added to the existing `from ..permissions import ...` line (anchor `grep -n '^from ..permissions import' data/chann_data/routers/internal.py`).

- [ ] **Step 6: Approvals (`phase14.py` + `internal.py`)**

`open_steps_for_report` gains `*, max_steps: int | None = None` and slices the sorted specs:

```python
        workflow = self.active_workflow(scope, "service_report")
        specs = sorted(workflow.rules_json.get("steps") or [], key=lambda s: s["order"])
        if max_steps is not None:
            # Round 21D: on a plan without multi-level approval the saved
            # chain opens its first step only (spec §3.4). Reports already
            # mid-flow keep the steps they were opened with.
            specs = specs[:max_steps]
        created: list[ApprovalStep] = []
        for spec in specs:
```

(replacing the existing `for spec in sorted(...)` line; the loop body is unchanged).

`replace_approval_workflow` — first lines inside `try:`:

```python
        steps = (payload.get("rules_json") or {}).get("steps") or []
        if len(steps) > 1:
            PlanRepository(session).require_feature(license_id, "feature.multi_level_approval")
```

`open_approval_steps` — replace the `open_steps_for_report(scope, report)` call with:

```python
        from ..plans import PLANS

        chain = PLANS[PlanRepository(session).plan_code(license_id)].has("feature.multi_level_approval")
        steps = ApprovalRepository(session).open_steps_for_report(
            scope, report, max_steps=None if chain else 1,
        )
```

- [ ] **Step 7: The chat sweep (`phase15.py` + `internal.py`)**

`sla_overdue(self, *, now=None, skip_licenses=())` and `time_out(self, *, now=None, skip_licenses=())` — each adds to its `select(...).where(...)`:

```python
        query = select(ChatSession).where(...)          # the existing conditions, unchanged
        if skip_licenses:
            # Round 21D: a shop without feature.live_chat is left alone by
            # the platform's clock — nothing escalated, nothing closed.
            query = query.where(ChatSession.license_id.notin_(list(skip_licenses)))
```

(restructure each into `query = …` then `if …` then execute, keeping `order_by` / `with_for_update(skip_locked=True)` on the final statement exactly as they are.)

`sweep_chat_sessions`:

```python
        skip = PlanRepository(session).licences_without("feature.live_chat")
        overdue = repo.sla_overdue(skip_licenses=skip)
        timed_out = repo.time_out(skip_licenses=skip)
```

- [ ] **Step 8: Run the test to verify it passes, then the neighbours**

```bash
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_feature_checks.py tests/integration/test_phase14_approvals.py tests/integration/test_live_chat_data.py tests/integration/test_shop_code_links_data.py tests/integration/test_member_channel.py tests/integration/test_last_approval_finishes_data.py -q
```
Expected: all pass. Every shop those older tests build is `pro` by default — Pro has service, customer link, live chat and custom roles, so they are unaffected; a test that saves a **two-step** chain now needs `enterprise` (give it that plan with `_set_plan`, and say so in its docstring).

- [ ] **Step 9: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the Data tier refuses on plan what it acts on itself — technician invites, customer links, custom roles, approval chains, and the chat sweep" && git log --oneline -1
```

---

### Task 5: Application — the principal carries the plan, `require_feature` beside `require`, one refusal shape

**Files:**
- Create: `application/chann_app/services/entitlements.py`
- Create: `tests/unit/plan_fixtures.py`
- Modify: `application/chann_app/services/authorization.py` (whole `TenantPrincipal` and the three returns of `resolve_tenant_principal`)
- Modify: `application/chann_app/config.py` (add `chann_sales_contact` beside `catalyst_zaid`, `:79`)
- Modify: `docs/RUNTIME_CONFIG_CONTRACT.md` (Application table — `tests/unit/test_config_contract.py` fails the moment `config.py` reads a name the contract does not list), `infrastructure/terraform/variables.tf` (after `openrouter_model_reasoning`), `infrastructure/terraform/cloud_run.tf:22-60` (`application_runtime_env`), `infrastructure/terraform/envs/dev/terraform.tfvars.example`
- Modify: `application/chann_app/data_client.py` — add `license_plan`, `platform_plan_preview` after `platform_tenant` (`:934`); `platform_tenants` (`:927`) gains `plan`
- Modify: `application/chann_app/routers_phase2.py` — `_propagate` (`:170`), `my_permissions` (`:1413`), a new `GET /licenses/{license_id}/plan` beside it; named checks in `compile_role_policy` (`:181`), `create_role` (`:220`), `update_role` (`:235`), the five technician-team routes (`:3574-3730`), `survey_summary` (`:2103`), `send_invoice_to_customer` (`:3008`), `send_quote_to_customer` (`:3038`), `upload_document_template` (`:4187`), `publish_document_template` (`:4412`), `set_document_template_active` (`:4552`), `create_api_key` (`:5585`)
- Test: `tests/unit/test_round21d_entitlements.py`, `tests/unit/test_round21d_principal.py`

**Interfaces:**
- Consumes (Task 3): the Data routes `GET /internal/v1/licenses/{id}/plan`, `GET /internal/v1/platform/tenants/{id}/plan-preview`, `?plan=` on the tenant list; `plan` on every membership row.
- Produces (`chann_app.services.entitlements`):
  - constants `AI_REPORTS`, `MEMBERS`, `PLAN_ORDER`, `PLAN_LABELS`, `FEATURE_LABELS` (`{key: {"th","en"}}`), `UNKNOWN_PLAN`, `PERMISSION_FEATURE`, `ALWAYS_ON_PERMISSIONS`, `NAMED_FEATURE_CHECKS` (keyed `(action, entity)` like `ACTION_PERMISSIONS`), `SERVICE_BASIC_REPORTS = ("open_jobs_by_tech", "satisfaction_avg")`
  - `@dataclass(frozen=True) class PlanView(code, label, features, locked, members_limit, ai_allowance, known=True)` with `from_payload(payload, *, known=True)`, `unknown()`, `has(key)`, `min_plan(key) -> str`, `min_label(key) -> str`, `as_payload() -> dict`
  - `feature_of(permission_key) -> str | None`, `feature_for_intent(action, entity, permission_key) -> str | None`, `feature_label(feature, language="th") -> str`, `effective_keys(role_keys, plan, *, whole_road=None) -> tuple[frozenset, frozenset]`, `entitled(payload, key) -> bool`, `refusal_detail(feature, plan) -> dict`, `plan_required(feature, plan) -> HTTPException`, `is_plan_refusal(exc) -> bool`, `async plan_for(client, license_id) -> tuple[PlanView, dict]`, `sales_contact() -> dict | None`
- Produces (`authorization.TenantPrincipal`): fields `plan: PlanView = PlanView.unknown()`, `plan_locked_keys: frozenset[str] = frozenset()`, `lock_feature: str | None = None`; methods `require`, `require_any` (plan-aware), `require_feature(feature)`; `build_principal(**) -> TenantPrincipal`
- Produces (`DataClient`): `async license_plan(license_id) -> dict`, `async platform_plan_preview(license_id) -> dict | None`, `platform_tenants(*, q=None, status=None, plan=None)`
- Produces (HTTP): `GET /api/v1/licenses/{license_id}/me/permissions` adds `plan`, `plan_locked_keys`, `sales_contact`; `GET /api/v1/licenses/{license_id}/plan` → `{"plan": <PlanView payload>, "usage": {...}}` (needs `setting.manage` or `member.manage`)
- Produces (tests): `tests/unit/plan_fixtures.py` → `plan_payload(code, *, ai_override=None) -> dict`, `plan_view(code) -> PlanView`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/plan_fixtures.py`:

```python
"""Round 21D — plan payloads for fakes, straight from the Data tier's own
resolve(), so a fake is never more generous than the real payload (the
17 ก.ย. 2569 lesson: a fake with extra keys hides dead code)."""
from __future__ import annotations

from chann_app.services.entitlements import PlanView
from chann_data.plans import resolve


def plan_payload(code: str, *, ai_override=None) -> dict:
    return resolve(code, ai_override=ai_override)


def plan_view(code: str) -> PlanView:
    return PlanView.from_payload(plan_payload(code))
```

Create `tests/unit/test_round21d_entitlements.py`:

```python
"""Round 21D — the Application's copies agree with the Data tier's matrix,
and every permission key is classified (spec §4.1, §10)."""
from __future__ import annotations

import pytest

from chann_app.services import entitlements as E
from chann_data import plans
from chann_data.permissions import PERMISSION_KEYS
from plan_fixtures import plan_payload, plan_view


class TestTheCopiesAgree:
    def test_labels_cover_exactly_the_ten_keys(self):
        assert set(E.FEATURE_LABELS) == set(plans.ENTITLEMENT_KEYS)

    def test_the_english_labels_are_the_data_tiers(self):
        assert {k: v["en"] for k, v in E.FEATURE_LABELS.items()} == plans.FEATURE_LABELS_EN

    def test_plan_labels_and_order(self):
        assert E.PLAN_ORDER == plans.PLAN_CODES
        assert E.PLAN_LABELS == {code: plans.PLANS[code].label for code in plans.PLAN_CODES}

    def test_the_fail_open_plan_is_pro_exactly(self):
        assert E.UNKNOWN_PLAN == plans.resolve("pro")


class TestEveryPermissionKeyIsClassified:
    def test_families_and_always_on_partition_the_catalogue(self):
        gated = {k for k in PERMISSION_KEYS if E.feature_of(k) is not None}
        assert gated.isdisjoint(E.ALWAYS_ON_PERMISSIONS)
        assert gated | E.ALWAYS_ON_PERMISSIONS == set(PERMISSION_KEYS)

    def test_every_gated_family_names_a_real_feature(self):
        assert set(E.PERMISSION_FEATURE.values()) <= set(plans.FEATURE_KEYS)
        assert set(E.NAMED_FEATURE_CHECKS.values()) <= set(plans.FEATURE_KEYS)

    @pytest.mark.parametrize("key,feature", [
        ("ticket.read", "feature.service"), ("service_report.create", "feature.service"),
        ("approval.approve", "feature.service"), ("warranty.create", "feature.warranty"),
        ("chat_session.reply", "feature.live_chat"), ("customer.read", None), ("setting.manage", None),
    ])
    def test_feature_of(self, key, feature):
        assert E.feature_of(key) == feature

    def test_named_checks_win_over_the_key(self):
        assert E.feature_for_intent("create", "api_key", "setting.manage") == "feature.external_api"
        assert E.feature_for_intent("read", "api_key", "setting.manage") is None
        assert E.feature_for_intent("send", "invoice", "invoice.update") == "feature.customer_line_link"
        assert E.feature_for_intent("read", "ticket", "ticket.read") == "feature.service"


class TestPlanView:
    def test_no_payload_behaves_as_pro_and_says_it_is_a_guess(self):
        view = E.PlanView.from_payload(None)
        assert view.code == "pro" and view.known is False
        assert view.has("feature.service") and not view.has("feature.external_api")

    def test_starter(self):
        view = plan_view("starter")
        assert not view.has("feature.service") and not view.has(E.AI_REPORTS)
        assert view.has(E.MEMBERS)
        assert view.min_plan("feature.external_api") == "enterprise" and view.min_label("feature.service") == "Pro"

    def test_an_override_of_zero_is_not_a_plan_lock(self):
        assert E.PlanView.from_payload(plan_payload("pro", ai_override=0)).has(E.AI_REPORTS)

    def test_effective_keys_subtract_what_the_plan_locks(self):
        keys, locked = E.effective_keys({"ticket.read", "customer.read", "warranty.read"}, plan_view("starter"))
        assert keys == {"customer.read"} and locked == {"ticket.read", "warranty.read"}

    def test_a_whole_road_locks_every_key(self):
        keys, locked = E.effective_keys({"ticket.read", "invoice.read"}, plan_view("starter"),
                                        whole_road="feature.customer_line_link")
        assert keys == frozenset() and locked == {"ticket.read", "invoice.read"}

    def test_the_refusal_shape(self):
        exc = E.plan_required("feature.service", plan_view("starter"))
        assert exc.status_code == 403
        assert exc.detail == {
            "error": "plan_required", "feature": "feature.service", "plan": "starter", "min_plan": "pro",
            "message": "Service jobs and technicians: included from the Pro plan. This shop is on Starter.",
        }
        # The Data tier says the same sentence for the same refusal.
        assert exc.detail == plans.PlanFeatureLocked("feature.service", "starter").detail()


class TestSalesContact:
    def test_unset_is_none(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "")
        assert E.sales_contact() is None

    def test_label_and_url(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "LINE @channcrm|https://line.me/R/ti/p/@channcrm")
        assert E.sales_contact() == {"label": "LINE @channcrm", "url": "https://line.me/R/ti/p/@channcrm"}

    def test_a_url_that_is_not_https_is_dropped_but_the_label_stays(self, monkeypatch):
        monkeypatch.setattr(E.settings, "chann_sales_contact", "โทร 02-123-4567|javascript:alert(1)")
        assert E.sales_contact() == {"label": "โทร 02-123-4567", "url": ""}
```

Create `tests/unit/test_round21d_principal.py`:

```python
"""Round 21D — the principal: locked keys subtracted in one place, `require`
says WHY (plan, not permission), `require_feature` for the named checks,
and the dashboard routes that share a key with always-on work."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from chann_app import routers_phase2
from chann_app.data_client import DataTierError
from chann_app.services import authorization
from chann_app.services.authorization import TenantPrincipal
from plan_fixtures import plan_payload, plan_view
from test_phase6_chat import LICENSE_ID, FakeDataClient


def _principal(code: str, keys: set[str], *, is_owner=False) -> TenantPrincipal:
    return authorization.build_principal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="admin", is_owner=is_owner,
        role_keys=keys, audience="sales", license_status="active", plan_payload=plan_payload(code),
    )


class TestRequire:
    def test_a_locked_key_is_refused_for_the_plan_not_the_permission(self):
        principal = _principal("starter", {"ticket.read", "customer.read"})
        assert "ticket.read" not in principal.permission_keys
        with pytest.raises(HTTPException) as caught:
            principal.require("ticket.read")
        assert caught.value.status_code == 403 and caught.value.detail["error"] == "plan_required"
        assert caught.value.detail["feature"] == "feature.service"
        principal.require("customer.read")

    def test_a_key_the_role_never_had_is_still_a_permission_refusal(self):
        principal = _principal("pro", {"customer.read"})
        with pytest.raises(HTTPException) as caught:
            principal.require("deal.read")
        assert caught.value.detail == "permission required: deal.read"

    def test_require_any_names_the_plan_when_the_only_holder_is_locked(self):
        principal = _principal("starter", {"ticket.update"})
        with pytest.raises(HTTPException) as caught:
            principal.require_any("ticket.assign", "ticket.update")
        assert caught.value.detail["error"] == "plan_required"

    def test_require_feature(self):
        _principal("enterprise", set()).require_feature("feature.external_api")
        with pytest.raises(HTTPException) as caught:
            _principal("pro", {"setting.manage"}).require_feature("feature.external_api")
        assert caught.value.detail["min_plan"] == "enterprise"

    def test_a_principal_built_without_a_plan_is_pro(self):
        principal = TenantPrincipal(license_id="L", chann_uid="U", role="r", is_owner=False,
                                    permission_keys=frozenset({"ticket.read"}))
        assert principal.plan.code == "pro" and principal.plan.known is False
        principal.require("ticket.read")


class _Identity(FakeDataClient):
    plan = None

    async def resolve_identity(self, line_user_id, primary_role, display_name=None):
        return {"chann_uid": "CHN-S-000001", "primary_role": primary_role}

    async def memberships_of(self, chann_uid, oa=None):
        return [{"license_id": LICENSE_ID, "license_code": "TESTCO", "company_name": "บริษัททดสอบ",
                 "license_status": "active", "plan": self.plan}]


@pytest.fixture
def verified(monkeypatch):
    async def fake_verify(token, audience):
        return {"sub": "U1", "name": "x"}

    monkeypatch.setattr(authorization, "verify_id_token", fake_verify)


class TestTheLiffPrincipal:
    async def test_staff_on_starter(self, verified):
        client = _Identity(permission_keys=["ticket.read", "customer.read"])
        client.plan = plan_payload("starter")
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="GET")
        assert principal.plan.code == "starter"
        assert principal.permission_keys == {"customer.read"} and principal.plan_locked_keys == {"ticket.read"}

    async def test_a_customer_of_a_starter_shop_can_do_nothing_there_and_is_told_why(self, verified):
        client = _Identity(permission_keys=[])
        client.plan = plan_payload("starter")
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="customer", x_license_id="", method="GET")
        assert principal.permission_keys == frozenset()
        with pytest.raises(HTTPException) as caught:
            principal.require("invoice.read")
        assert caught.value.detail["feature"] == "feature.customer_line_link"

    async def test_an_old_data_tier_with_no_plan_is_pro(self, verified):
        client = _Identity(permission_keys=["ticket.read"])
        principal = await authorization.resolve_tenant_principal(
            client, x_liff_id_token="t", x_liff_audience="sales", x_license_id="", method="GET")
        assert principal.plan.code == "pro" and "ticket.read" in principal.permission_keys


class _Routes(FakeDataClient):
    async def license_plan(self, license_id):
        return {"plan": plan_payload("starter"), "usage": {"members": 3, "members_limit": 5}}

    async def create_api_key(self, license_id, payload, actor_id=None):
        return {"id": "k1", "name": payload.get("name"), "key": "chann_live_x"}

    async def create_role(self, license_id, payload, actor_id=None):
        return {"role_name": payload["role_name"]}


def _app(principal: TenantPrincipal, client=None) -> TestClient:
    client = client or _Routes()

    async def override_client():
        yield client

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTheDashboardRoutes:
    def test_me_carries_the_plan_and_the_contact_for_whoever_can_act_on_it(self, monkeypatch):
        monkeypatch.setattr(routers_phase2.entitlements.settings, "chann_sales_contact", "LINE @chann|https://line.me/x")
        owner = _app(_principal("starter", {"setting.manage"}, is_owner=True)).get(
            f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert owner["plan"]["code"] == "starter" and owner["sales_contact"]["url"] == "https://line.me/x"
        clerk = _app(_principal("starter", {"ticket.read", "customer.read"})).get(
            f"/api/v1/licenses/{LICENSE_ID}/me/permissions").json()
        assert clerk["sales_contact"] is None and clerk["plan_locked_keys"] == ["ticket.read"]

    def test_the_plan_route(self):
        out = _app(_principal("starter", {"setting.manage"})).get(f"/api/v1/licenses/{LICENSE_ID}/plan")
        assert out.status_code == 200 and out.json()["usage"]["members"] == 3
        denied = _app(_principal("starter", {"customer.read"})).get(f"/api/v1/licenses/{LICENSE_ID}/plan")
        assert denied.status_code == 403

    @pytest.mark.parametrize("method,path,body,feature", [
        ("post", "/roles", {"role_name": "ช่างอาวุโส", "permission_keys": ["customer.read"]}, "feature.custom_roles"),
        ("post", "/api-keys", {"name": "ERP"}, "feature.external_api"),
        ("get", "/technician-teams", None, "feature.service"),
        ("get", "/surveys/summary", None, "feature.service"),
    ])
    def test_a_named_check_refuses_on_plan(self, method, path, body, feature):
        keys = {"role.manage", "setting.manage", "team.manage", "view_reports", "ticket.read"}
        call = getattr(_app(_principal("starter", keys, is_owner=True)), method)
        out = call(f"/api/v1/licenses/{LICENSE_ID}{path}", **({"json": body} if body else {}))
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "plan_required" and out.json()["detail"]["feature"] == feature

    def test_enterprise_makes_a_key(self):
        out = _app(_principal("enterprise", {"setting.manage"}, is_owner=True)).post(
            f"/api/v1/licenses/{LICENSE_ID}/api-keys", json={"name": "ERP"})
        assert out.status_code in (200, 201), out.text

    def test_a_data_tier_plan_refusal_keeps_its_403(self):
        exc = DataTierError(403, "x", {"error": "plan_required", "feature": "feature.service"})
        assert routers_phase2._propagate(exc).status_code == 403
        assert routers_phase2._propagate(DataTierError(403, "cross tenant")).status_code == 502
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_entitlements.py tests/unit/test_round21d_principal.py -q
```
Expected: collection ERROR `ModuleNotFoundError: No module named 'chann_app.services.entitlements'`.

- [ ] **Step 3: The entitlements module**

Create `application/chann_app/services/entitlements.py`:

```python
"""What the shop's plan lets it do (round 21D) — the Application's half.

The Data tier owns the matrix (chann_data/plans.py) and sends the resolved
plan with every membership row, API-key resolution and tenant row; this
module READS that payload and never decides a plan. Entitlement sits beside
permission, never instead of it (spec §1): a role grants only what the plan
includes, and a plan grants nothing a role does not.

The copies here (labels, the Pro fallback) exist because the Application
cannot import chann_data (tier boundary); tests/unit/test_round21d_
entitlements.py keeps them equal to the Data tier's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status

from ..config import settings

log = logging.getLogger(__name__)

AI_REPORTS = "quota.ai_reports_per_month"
MEMBERS = "limit.members"
PLAN_ORDER = ("starter", "pro", "enterprise", "enterprise_plus")
PLAN_LABELS = {"starter": "Starter", "pro": "Pro", "enterprise": "Enterprise", "enterprise_plus": "Enterprise Plus"}

#: Spec §4 — the refusal and admin wording, both languages.
FEATURE_LABELS: dict[str, dict[str, str]] = {
    "feature.customer_line_link": {"th": "ผูก LINE ลูกค้ากับร้าน", "en": "Customer LINE link"},
    "feature.live_chat": {"th": "แชทกับลูกค้า", "en": "Customer chat"},
    "feature.service": {"th": "งานบริการ / งานซ่อม และทีมช่าง", "en": "Service jobs and technicians"},
    "feature.warranty": {"th": "ทะเบียนเครื่อง / ประกัน", "en": "Warranty register"},
    "feature.custom_documents": {"th": "แบบฟอร์มเอกสารของร้านเอง", "en": "Custom document templates"},
    "feature.custom_roles": {"th": "สร้างและแก้บทบาทเอง", "en": "Custom roles"},
    "feature.multi_level_approval": {"th": "ขั้นตอนอนุมัติหลายระดับ", "en": "Multi-level approval"},
    "feature.external_api": {"th": "เชื่อมต่อ API ภายนอก", "en": "External API"},
    AI_REPORTS: {"th": "ถามรายงานด้วย AI", "en": "AI reports"},
    MEMBERS: {"th": "จำนวนผู้ใช้งาน", "en": "Users"},
}

#: The payload of Pro — what a principal with no plan behaves as (spec §5.1).
UNKNOWN_PLAN: dict = {
    "code": "pro", "label": "Pro", "rank": 1,
    "features": ["feature.custom_documents", "feature.custom_roles", "feature.customer_line_link",
                 "feature.live_chat", "feature.service", "feature.warranty"],
    "locked": {"feature.multi_level_approval": "enterprise", "feature.external_api": "enterprise"},
    "limits": {"members": 15, "ai_reports_per_month": 30},
    "quota_top_up": True,
}

#: A permission key in one of these families is usable only when the plan
#: has the feature (spec §4.1).
PERMISSION_FEATURE: dict[str, str] = {
    "ticket.": "feature.service",
    "service_report.": "feature.service",
    "approval.": "feature.service",      # approvals are of service reports only
    "warranty.": "feature.warranty",
    "chat_session.": "feature.live_chat",
}
#: Every other permission key, named — so a NEW key has to be classified,
#: not forgotten (check-perms.py and the unit test both enforce it).
ALWAYS_ON_PERMISSIONS: frozenset[str] = frozenset({
    "customer.read", "customer.create", "customer.update", "customer.archive",
    "deal.read", "deal.create", "deal.update", "deal.archive", "deal.reopen",
    "note.read", "note.create", "note.update",
    "followup.read", "followup.create", "followup.update",
    "product.read", "product.manage", "team.manage",
    "quote.read", "quote.create", "quote.update",
    "invoice.read", "invoice.create", "invoice.update", "invoice.void",
    "reassign_records", "view_reports", "role.manage", "member.manage", "setting.manage",
    "audit_log.view", "platform.admin.access", "platform.admin.break_glass",
    "pdpa.request.view", "pdpa.request.process",
})
#: Features that share a key with always-on work, checked by NAME at the
#: route / handler (spec §4.1). Keyed (action, entity) exactly like
#: chat.ACTION_PERMISSIONS, which check-parity.py reads beside it.
#: Reading and revoking API keys stay open on every plan (a downgraded
#: owner must be able to see and revoke what an outside system holds);
#: only MAKING one is the feature — the same rule on both surfaces.
NAMED_FEATURE_CHECKS: dict[tuple[str, str], str] = {
    ("create", "api_key"): "feature.external_api",
    ("create", "role"): "feature.custom_roles",
    ("update", "role"): "feature.custom_roles",
    ("read", "team"): "feature.service",
    ("create", "team"): "feature.service",
    ("update", "team"): "feature.service",
    ("delete", "team"): "feature.service",
    ("archive", "team"): "feature.service",
    ("read", "survey"): "feature.service",
    ("send", "invoice"): "feature.customer_line_link",
    ("send", "quote"): "feature.customer_line_link",
}
#: Owner decision Q4: on a plan without service jobs the basic-report card
#: shows three reports, not five.
SERVICE_BASIC_REPORTS = ("open_jobs_by_tech", "satisfaction_avg")


def feature_of(permission_key: str | None) -> str | None:
    key = str(permission_key or "")
    for prefix, feature in PERMISSION_FEATURE.items():
        if key.startswith(prefix):
            return feature
    return None


def feature_for_intent(action: str | None, entity: str | None, permission_key: str | None) -> str | None:
    return NAMED_FEATURE_CHECKS.get((str(action or ""), str(entity or ""))) or feature_of(permission_key)


def feature_label(feature: str, language: str = "th") -> str:
    entry = FEATURE_LABELS.get(feature) or {}
    return entry.get("en" if language == "en" else "th") or feature


@dataclass(frozen=True)
class PlanView:
    code: str
    label: str
    features: frozenset[str]
    locked: tuple[tuple[str, str], ...]     # (key, min plan code); a tuple so the view stays hashable
    members_limit: int | None
    ai_allowance: int
    known: bool = True

    @classmethod
    def from_payload(cls, payload, *, known: bool = True) -> "PlanView":
        if not isinstance(payload, dict) or payload.get("code") not in PLAN_LABELS:
            return cls.unknown()
        limits = payload.get("limits") or {}
        return cls(
            code=str(payload["code"]),
            label=str(payload.get("label") or PLAN_LABELS[payload["code"]]),
            features=frozenset(str(f) for f in payload.get("features") or ()),
            locked=tuple(sorted((str(k), str(v)) for k, v in (payload.get("locked") or {}).items())),
            members_limit=limits.get("members"),
            ai_allowance=int(limits.get("ai_reports_per_month") or 0),
            known=known,
        )

    @classmethod
    def unknown(cls) -> "PlanView":
        """No plan from the Data tier (an older image mid-deploy, a read
        that failed): behave as Pro, the backfill value, so a transient
        failure never locks a paying shop out — the quota already fails open
        for the same reason (spec §5.1)."""
        return cls.from_payload(UNKNOWN_PLAN, known=False)

    def has(self, key: str) -> bool:
        if key == MEMBERS:
            return True
        if key == AI_REPORTS:
            return key not in dict(self.locked)
        return key in self.features

    def min_plan(self, key: str) -> str:
        return dict(self.locked).get(key) or "pro"

    def min_label(self, key: str) -> str:
        return PLAN_LABELS.get(self.min_plan(key), "Pro")

    def as_payload(self) -> dict:
        return {
            "code": self.code, "label": self.label, "features": sorted(self.features),
            "locked": dict(self.locked),
            "limits": {"members": self.members_limit, "ai_reports_per_month": self.ai_allowance},
            "known": self.known,
        }


def effective_keys(role_keys, plan: PlanView, *, whole_road: str | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """(keys the person may use, keys their role holds that the plan locks).
    Subtracting here is what makes every existing `principal.require(...)`
    and every chat `"x" in set(permission_keys)` plan-aware without editing
    them (spec §5.1). `whole_road`: a feature without which NONE of the keys
    mean anything (a customer of a shop without the Customer LINE link)."""
    keys = frozenset(role_keys or ())
    if whole_road is not None and not plan.has(whole_road):
        return frozenset(), keys
    locked = frozenset(k for k in keys if (feature := feature_of(k)) is not None and not plan.has(feature))
    return keys - locked, locked


def entitled(payload, key: str) -> bool:
    """For code with no principal (sweeps): the plan payload a tenant row carries."""
    return PlanView.from_payload(payload).has(key)


def refusal_detail(feature: str, plan: PlanView) -> dict:
    need = plan.min_plan(feature)
    return {
        "error": "plan_required", "feature": feature, "plan": plan.code, "min_plan": need,
        "message": (f"{feature_label(feature, 'en')}: included from the {PLAN_LABELS.get(need, 'Pro')} "
                    f"plan. This shop is on {plan.label}."),
    }


def plan_required(feature: str, plan: PlanView) -> HTTPException:
    """The standard refusal (spec §5.2) — 403, `error: plan_required`."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal_detail(feature, plan))


PLAN_REFUSAL_CODES = ("plan_required", "member_limit_reached", "plan_member_limit")


def is_plan_refusal(exc) -> bool:
    body = getattr(exc, "structured", None) or getattr(exc, "detail", None)
    return isinstance(body, dict) and body.get("error") in PLAN_REFUSAL_CODES


async def plan_for(client, license_id: str) -> tuple[PlanView, dict]:
    """(plan, usage) for code that has a licence but no principal. Fails
    open as Pro, like the principal does."""
    try:
        out = await client.license_plan(str(license_id)) or {}
    except Exception:  # noqa: BLE001 — a read that failed must not lock a shop out
        log.exception("could not read the plan of %s; acting as Pro", license_id)
        return PlanView.unknown(), {}
    return PlanView.from_payload(out.get("plan")), dict(out.get("usage") or {})


def sales_contact() -> dict | None:
    """Owner decision Q5: `CHANN_SALES_CONTACT` = "label|url". None when
    unset (the upgrade button is then not drawn). A URL that is not https
    is dropped — it would be put behind a button — and the label kept."""
    raw = str(settings.chann_sales_contact or "").strip()
    if not raw:
        return None
    label, _sep, url = raw.partition("|")
    label, url = label.strip(), url.strip()
    if not url.startswith("https://"):
        url = ""
    if not label and not url:
        return None
    return {"label": label or url, "url": url}
```

In `application/chann_app/config.py`, after `catalyst_zaid: str = ""`:

```python
    # Round 21D (owner decision Q5): the Chann team's contact behind every
    # "ติดต่อเพื่ออัปเกรด" — "label|https-url", e.g. "LINE @channcrm|https://line.me/R/ti/p/@channcrm".
    # Not a secret. Empty = the button is not drawn.
    chann_sales_contact: str = ""
```

The configuration travels the usual road — contract, Terraform variable, Cloud Run env — so the owner can set it in `terraform.tfvars` (owner decision Q5):

`docs/RUNTIME_CONFIG_CONTRACT.md`, a row in the **Application** table after `OPENROUTER_MODEL_REASONING`:

```markdown
| `CHANN_SALES_CONTACT` | OPTIONAL (default empty) | round 21D — the Chann team's contact behind every "ติดต่อเพื่ออัปเกรด": `label\|https-url` (e.g. `LINE @channcrm\|https://line.me/R/ti/p/@channcrm`); empty = the button is not drawn and the text says to contact the team that looks after the shop. Not a secret. |
```

`infrastructure/terraform/variables.tf`, after `variable "openrouter_model_reasoning"`:

```hcl
variable "chann_sales_contact" {
  type        = string
  description = "Round 21D: 'label|https-url' of the Chann sales contact behind the upgrade button. Not a secret; empty hides the button."
  default     = ""
}
```

`infrastructure/terraform/cloud_run.tf`, in `application_runtime_env` after `OPENROUTER_MODEL_REASONING`: `CHANN_SALES_CONTACT = var.chann_sales_contact`.

`infrastructure/terraform/envs/dev/terraform.tfvars.example`: `chann_sales_contact = ""  # "LINE @channcrm|https://line.me/R/ti/p/@channcrm" — the owner supplies it`.

- [ ] **Step 4: The principal**

In `application/chann_app/services/authorization.py`: change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`, add `from . import entitlements`, and replace `class TenantPrincipal` with:

```python
@dataclass(frozen=True)
class TenantPrincipal:
    license_id: str
    chann_uid: str
    role: str
    is_owner: bool
    #: Role grants MINUS what the plan locks (round 21D) — the effective set.
    permission_keys: frozenset[str]
    audience: str = "sales"
    license_status: str = "active"
    #: Round 21D: the shop's plan; a principal built without one is Pro.
    plan: entitlements.PlanView = field(default_factory=entitlements.PlanView.unknown)
    #: Held by the role, locked by the plan — what lets require() say WHY.
    plan_locked_keys: frozenset[str] = frozenset()
    #: Set when ONE feature locks every key (a customer of a shop without
    #: the Customer LINE link); otherwise each key names its own family.
    lock_feature: str | None = None

    @property
    def is_customer(self) -> bool:
        return self.audience == "customer"

    @property
    def is_suspended(self) -> bool:
        return self.license_status in READ_ONLY_STATUSES

    def _plan_refusal(self, key: str) -> HTTPException:
        return entitlements.plan_required(
            self.lock_feature or entitlements.feature_of(key) or "feature.service", self.plan,
        )

    def require_any(self, *permission_keys: str) -> None:
        if any(key in self.permission_keys for key in permission_keys):
            return
        locked = [key for key in permission_keys if key in self.plan_locked_keys]
        if locked:
            raise self._plan_refusal(locked[0])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"permission required: {' or '.join(permission_keys)}",
        )

    def require(self, permission_key: str) -> None:
        # Plan before permission (spec §5.3): "your shop's plan doesn't have
        # it" is the truer answer, and a role grant is irrelevant until the
        # plan has it.
        if permission_key in self.plan_locked_keys:
            raise self._plan_refusal(permission_key)
        if permission_key not in self.permission_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission required: {permission_key}",
            )

    def require_feature(self, feature: str) -> None:
        """The named checks of spec §4.1 — a feature that shares its
        permission key with always-on work."""
        if not self.plan.has(feature):
            raise entitlements.plan_required(feature, self.plan)


def build_principal(
    *, license_id: str, chann_uid: str, role: str, is_owner: bool, role_keys, audience: str,
    license_status: str, plan_payload, whole_road: str | None = None,
) -> TenantPrincipal:
    """The ONE place a principal gets its plan (spec §5.1): LIFF and the
    external API both come through here."""
    plan = entitlements.PlanView.from_payload(plan_payload)
    keys, locked = entitlements.effective_keys(role_keys, plan, whole_road=whole_road)
    return TenantPrincipal(
        license_id=license_id, chann_uid=chann_uid, role=role, is_owner=is_owner,
        permission_keys=keys, audience=audience, license_status=license_status,
        plan=plan, plan_locked_keys=locked,
        lock_feature=whole_road if (whole_road and not plan.has(whole_road)) else None,
    )
```

In `resolve_tenant_principal`, the two returns that have a selected shop become `build_principal(...)` calls (the no-shop customer return stays a plain `TenantPrincipal` — there is no shop and no plan):

```python
    if x_liff_audience == "customer":
        return build_principal(
            license_id=str(selected["license_id"]), chann_uid=identity["chann_uid"],
            role="customer", is_owner=False, role_keys=CUSTOMER_PERMISSION_KEYS,
            audience="customer", license_status=license_status, plan_payload=selected.get("plan"),
            # Spec §7.3: the customer app IS the Customer LINE link.
            whole_road="feature.customer_line_link",
        )
```
```python
    return build_principal(
        license_id=str(selected["license_id"]), chann_uid=identity["chann_uid"],
        role=context["role"], is_owner=bool(context["is_owner"]),
        role_keys=context["permission_keys"], audience=x_liff_audience,
        license_status=license_status, plan_payload=selected.get("plan"),
    )
```

The plan comes from the **membership row**, not from `authorization_context`: the membership is read on every request anyway and is not cached per person, while `authorization_context` is cached per `(license, person, channel)` — folding the plan in there is exactly the seam spec §3.2 warns about. (Ruling recorded in the self-review.)

- [ ] **Step 5: DataClient**

In `application/chann_app/data_client.py`, `platform_tenants` becomes:

```python
    async def platform_tenants(self, *, q: str | None = None, status: str | None = None,
                               plan: str | None = None) -> list[dict]:
        params = {k: v for k, v in (("q", q), ("status", status), ("plan", plan)) if v}
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/tenants", params=params or None, headers=self._headers,
        )
        return self._unwrap(resp)
```

and after `platform_tenant`:

```python
    async def license_plan(self, license_id: str) -> dict:
        """Round 21D — {"plan": <resolved>, "usage": {...}} for one shop."""
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/plan", headers=self._headers,
        )
        return self._unwrap(resp)

    async def platform_plan_preview(self, license_id: str) -> dict | None:
        """Round 21D — what each other plan would lock, for the admin."""
        resp = await self._client.get(
            f"{self._base}/internal/v1/platform/tenants/{license_id}/plan-preview", headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        return self._unwrap(resp)
```

- [ ] **Step 6: The dashboard routes (`routers_phase2.py`)**

Import: `from .services import entitlements` beside the other `from .services import …` lines.

`_propagate` — keep 403 for a plan refusal only (every other 403 from the Data tier stays 502, as today):

```python
def _propagate(exc: DataTierError) -> HTTPException:
    allowed = {400, 404, 409, 422, 503}
    code = exc.status_code if exc.status_code in allowed else 502
    if exc.status_code == 403 and entitlements.is_plan_refusal(exc):
        # Round 21D: the Data tier's plan refusal is the caller's answer.
        code = 403
    return HTTPException(status_code=code, detail=exc.structured or exc.detail)
```

`my_permissions` returns, additionally:

```python
        # Round 21D — the plan, and the upgrade contact for whoever can act
        # on it (the owner, or a holder of setting.manage).
        "plan": principal.plan.as_payload(),
        "plan_locked_keys": sorted(principal.plan_locked_keys),
        "sales_contact": entitlements.sales_contact()
        if (principal.is_owner or "setting.manage" in principal.permission_keys) else None,
```

New route, directly after `my_permissions`:

```python
@router.get("/licenses/{license_id}/plan")
async def license_plan(
    license_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Round 21D — the plan card (company page) and the members page's
    "ผู้ใช้ n/limit": the plan and how much of it is used. Chat's twin is
    ("read", "plan")."""
    _require_same_tenant(principal, license_id)
    principal.require_any("setting.manage", "member.manage")
    try:
        out = await client.license_plan(license_id)
    except DataTierError as exc:
        raise _propagate(exc)
    return {"plan": entitlements.PlanView.from_payload(out.get("plan")).as_payload(),
            "usage": out.get("usage") or {}}
```

Named checks — one line each, directly **after** the route's existing `principal.require(...)` line:
- `compile_role_policy`, `create_role`, `update_role`: `principal.require_feature("feature.custom_roles")` (deleting a role stays open: it is cleanup, and members keep the role they hold — spec §3.4)
- the five technician-team routes (`list_technician_teams`, the POST, the DELETE, the members GET/POST/DELETE — `grep -n '"/licenses/{license_id}/technician-teams' application/chann_app/routers_phase2.py` lists them): `principal.require_feature("feature.service")`
- `survey_summary`: `principal.require_feature("feature.service")`
- `send_invoice_to_customer`, `send_quote_to_customer`: `principal.require_feature("feature.customer_line_link")`
- `upload_document_template`, `publish_document_template`, `set_document_template_active`: `principal.require_feature("feature.custom_documents")` (listing, previewing and archiving stay open — the page is read-only on a plan without the feature, spec §3.4)
- `create_api_key`: `principal.require_feature("feature.external_api")` (listing and revoking stay open — ruling in the self-review)

- [ ] **Step 7: Run the new tests, then the suite, and fix every Pro-default fake honestly**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_entitlements.py tests/unit/test_round21d_principal.py tests/unit/test_config_contract.py -q
cd ~/stage-fix/r21d/infrastructure/terraform && terraform fmt -check -diff variables.tf cloud_run.tf || terraform fmt variables.tf cloud_run.tf
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit tests/boundary -q -p no:cacheprovider \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors" | tail -5
```
Expected: the two new files pass. In the full run, the only failures allowed are fakes that build a principal with **no plan** (= Pro) and then exercise an Enterprise feature — `tests/unit/test_round21b_api_keys.py` (creating a key). Fix each by giving that fake `plan_payload("enterprise")` through `build_principal(...)` or the membership row, with a one-line comment `# round 21D: making a key is Enterprise`. **Any other failure is a regression — read it.** Record the list of files touched this way in the task report.

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the principal carries the plan, subtracts what it locks in one place, and every route answers 403 plan_required the same way" && git log --oneline -1
```

---

### Task 6: External API — every request on a plan without it is 403 `plan_required`

**Files:**
- Modify: `application/chann_app/auth/api_key.py:33-85` (`api_principal`)
- Modify: `tests/unit/test_round21b_ext_api.py` — the `RESOLVED` fixture (`:84`) gains `"plan": plan_payload("enterprise")`
- Modify: `scripts/dev/check-routes.py` (one new rule, appended)
- Modify: `docs/API.md` (error table, `:55-68`)
- Test: `tests/unit/test_round21d_ext_api.py`

**Interfaces:**
- Consumes (Task 3): `plan` on the `/internal/v1/api-keys/resolve` answer. (Task 5): `authorization.build_principal`, `TenantPrincipal.require_feature`.
- Produces: `api_principal` refuses with `{"error": {"code": "plan_required", "message": "External API: included from the Enterprise plan. This shop is on Pro."}}` (403) on **every** ext route including `/me`; 401/429 still come first.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21d_ext_api.py`:

```python
"""Round 21D — the external API is an Enterprise feature (spec §5.5). The
key checks stay first: a bad key is 401 whatever the plan, so an attacker
learns nothing about the shop's plan."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from chann_app import routers_ext
from chann_app.routers_admin import get_data_client
from plan_fixtures import plan_payload
from test_phase6_chat import LICENSE_ID, FakeDataClient
from test_round21b_ext_api import RAW, RESOLVED, _ResolvingFake


@pytest.fixture(autouse=True)
def _clean():
    yield
    routers_ext.ext_app.dependency_overrides.clear()


def _ext_with(plan_code: str | None):
    resolved = copy.deepcopy(RESOLVED)
    if plan_code is None:
        resolved.pop("plan", None)
    else:
        resolved["plan"] = plan_payload(plan_code)
    fake = _ResolvingFake(resolved=resolved, role="sales", permission_keys=["customer.read"])

    async def override():
        yield fake

    routers_ext.ext_app.dependency_overrides[get_data_client] = override
    return TestClient(routers_ext.ext_app)


@pytest.mark.parametrize("plan_code", ["starter", "pro"])
def test_below_enterprise_every_route_is_403_plan_required(plan_code):
    http = _ext_with(plan_code)
    for path in ("/me", "/customers?limit=1"):
        out = http.get(path, headers={"Authorization": f"Bearer {RAW}"})
        assert out.status_code == 403, (path, out.text)
        assert out.json()["error"]["code"] == "plan_required"
        assert "Enterprise" in out.json()["error"]["message"]


@pytest.mark.parametrize("plan_code", ["enterprise", "enterprise_plus"])
def test_enterprise_and_up_pass(plan_code):
    out = _ext_with(plan_code).get("/me", headers={"Authorization": f"Bearer {RAW}"})
    assert out.status_code == 200, out.text


def test_an_older_data_tier_with_no_plan_is_pro_and_refused():
    out = _ext_with(None).get("/me", headers={"Authorization": f"Bearer {RAW}"})
    assert out.status_code == 403


def test_a_bad_key_is_401_before_any_plan_is_read():
    http = _ext_with("starter")
    assert http.get("/me").status_code == 401
    assert http.get("/me", headers={"Authorization": "Bearer chann_live_short"}).status_code == 401
```

Check the ext app's mount path first — `grep -n 'ext_app.include_router\|prefix=' application/chann_app/routers_ext.py` — and use the paths the existing `test_round21b_ext_api.py` requests use (`/me`, `/customers`); if they are prefixed there, prefix them here the same way.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_ext_api.py -q
```
Expected: the `starter`/`pro` and no-plan cases FAIL with `assert 200 == 403`.

- [ ] **Step 3: The principal**

In `application/chann_app/auth/api_key.py`, import `from ..services.authorization import build_principal, refuse_if_suspended` (replacing the `TenantPrincipal` import if nothing else uses it) and replace the final `return TenantPrincipal(...)` with:

```python
    principal = build_principal(
        license_id=str(key["license_id"]), chann_uid=f"api:{key['id']}", role="api", is_owner=False,
        role_keys=found.get("permission_keys") or (), audience="api",
        license_status=license_status, plan_payload=found.get("plan"),
    )
    # Round 21D (spec §5.5): the external API is an Enterprise feature.
    # AFTER the key checks — a bad key is 401 whatever the plan.
    principal.require_feature("feature.external_api")
    return principal
```

Keep the `-> TenantPrincipal` return annotation (import `TenantPrincipal` too if it was the only use).

- [ ] **Step 4: The older 21B fixture carries a plan now**

In `tests/unit/test_round21b_ext_api.py`, add `from plan_fixtures import plan_payload  # noqa: E402` beside the other imports and add to `RESOLVED`:

```python
    # Round 21D: the resolver's answer carries the shop's plan; a key only
    # works on Enterprise and up (spec §5.5).
    "plan": plan_payload("enterprise"),
```

- [ ] **Step 5: `check-routes.py` pins it**

Append to `scripts/dev/check-routes.py`:

```python
# Round 21D (spec §10): every ext route is behind api_principal, and
# api_principal itself must refuse on plan — one line that a refactor could
# silently drop and no single route test would notice.
api_key_src = Path("application/chann_app/auth/api_key.py").read_text()
if 'require_feature("feature.external_api")' not in api_key_src:
    print('\nauth/api_key.py: api_principal no longer calls require_feature("feature.external_api")')
    raise SystemExit(1)
print("api_principal refuses on plan")
```

- [ ] **Step 6: The documentation**

In `docs/API.md`, add a row to the error table directly under the `403 | forbidden` row:

```markdown
| 403 | `plan_required` | แพ็กเกจของร้านไม่มี API ภายนอก (มีตั้งแต่ Enterprise ขึ้นไป) — ทุก route รวม `/me` · key ยังอยู่ ใช้ได้อีกเมื่อร้านอัปเกรด |
```

- [ ] **Step 7: Run to verify, then the 21B suites and the boundary test**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_ext_api.py tests/unit/test_round21b_ext_api.py tests/unit/test_round21b_api_keys.py tests/boundary/test_tier_boundaries.py -q
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
```
Expected: all pass; `check-routes.py` ends `api_principal refuses on plan`.

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the external API answers 403 plan_required below Enterprise, after the key checks" && git log --oneline -1
```

---

### Task 7: Chat — the refusal, the central gate, and every bare "no permission" made plan-aware

**Files:**
- Modify: `application/chann_app/services/chat.py`
  - imports (`:15-50`): `from . import entitlements`
  - `_ROAD` context (`:13485`): add `_PLAN` beside it; `_plan_has`, `_plan_refusal`, `_plan_reply_from`, `_no_permission` defined right after `_note_road` (anchor `grep -n '^def _note_road' application/chann_app/services/chat.py`)
  - copy: `PLAN_REQUIRED*`, `PLAN_CONTACT_BUTTON` directly after `SUGGEST_NO_PERMISSION_NAMED` (`:20308`)
  - `handle_chat_message` (`:26471`): set/reset `_PLAN`
  - `_route_chat_message` staff branch (`:26754-26760`, anchor `permission_keys = list(context.get("permission_keys") or [])`): effective keys + `_PLAN.set(...)`; customer branch (`:26702`): `_PLAN.set(...)`
  - `_execute_intent` gate (`:30174-30206`, anchor `needed = required_permission(req_action, req_entity)`)
  - `suggest_what_you_can_do` (`:21310`)
  - the 115 bare `ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language)` sites (by script)
- Modify: `tests/unit/test_phase6_chat.py` — `FakeDataClient.license_plan` (next to `authorization_context`, `:162`)
- Modify: `scripts/agent-test/agent_test_runner/scenario.py:52` (`ACTOR_KEYS`), `scripts/agent-test/scenario.schema.json` (`$defs.actor`), `scripts/agent-test/run.py:183-192`, `scripts/agent-test/agent_test_runner/backends.py` (`FakeBackend.send`, `DbBackend.send`), `scripts/agent-test/scenarios/api-keys.yaml` (`actor.plan: enterprise`)
- Test: `tests/unit/test_round21d_chat_refusal.py`, `tests/unit/test_round21d_no_bare_refusal.py`

**Interfaces:**
- Consumes (Task 5): `entitlements.PlanView`, `effective_keys`, `feature_for_intent`, `feature_label`, `sales_contact`, `is_plan_refusal`, `PLAN_LABELS`; (Task 3) `plan` on every membership row.
- Produces (in `chat.py`, used by Tasks 8–10):
  - `_PLAN: contextvars.ContextVar[tuple[PlanView, bool] | None]` — (plan, can_upgrade) for the message being handled
  - `_plan_has(key: str) -> bool` (no context → Pro)
  - `_plan_refusal(feature: str, language: str) -> ChatReply` — the §6.2 text; owner tail + contact button for whoever can upgrade, member tail otherwise; the AI hint for `quota.ai_reports_per_month`
  - `_plan_reply_from(body: dict, language: str) -> ChatReply` — a Data-tier refusal body in words
  - `_no_permission(language: str, *keys: str, url: tuple[str, str] | None = None) -> ChatReply`
  - `FakeDataClient.license_plan(license_id)` → `{"plan": self._plan (default None = Pro), "usage": {"members": self._members_count (default 1), ...}}`
  - agent-test: `actor.plan` (`starter|pro|enterprise|enterprise_plus`) — fake backend only

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_round21d_no_bare_refusal.py`:

```python
"""Round 21D — no handler answers "you lack permission" without asking the
plan first (spec §5.3). Without this a Starter owner typing a service
trigger is told to ask the owner — themselves — for a permission."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = (ROOT / "application/chann_app/services/chat.py").read_text(encoding="utf-8")


def test_no_bare_no_permission_reply_is_left():
    assert "ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD" not in CHAT


def test_the_lead_is_used_only_by_its_definition_and_no_permission():
    uses = [m.start() for m in re.finditer(r"\bSUGGEST_NO_PERMISSION_LEAD\b", CHAT)]
    start = CHAT.index("def _no_permission(")
    end = start + 1 + re.search(r"\n\S", CHAT[start + 1:]).start()   # the first unindented line after it
    inside = CHAT[start:end].count("SUGGEST_NO_PERMISSION_LEAD")
    assert inside == 1
    assert len(uses) == 2          # the definition + the one inside _no_permission
```

Create `tests/unit/test_round21d_chat_refusal.py`:

```python
"""Round 21D — chat on a plan without the feature (spec §5.3, §6.2).

The plan check runs where the permission check runs: after the model read
the sentence. Order: unknown feature → wrong OA → PLAN → permission."""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services import entitlements
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio

STARTER_REFUSAL = (
    "🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter\n"
    "ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ"
)
OPEN_A_JOB = {"action": "create", "entity": "ticket",
              "fields": {"target_name": "สมชาย", "issue_description": "แอร์ไม่เย็น"}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setattr(settings, "chann_sales_contact", "LINE @channcrm|https://line.me/R/ti/p/@channcrm")


async def _say(client, message, ai, *, plan="starter", oa="sales", role="owner"):
    ctx = _ctx(primary_role=role, oa=oa)
    if plan is not None:
        ctx.memberships[0]["plan"] = plan_payload(plan)
    return await handle_chat_message(
        client, message=message, ctx=ctx,
        ai_client=httpx.AsyncClient(transport=_ai(json.dumps(ai))),
    )


def _owner(keys=None) -> FakeDataClient:
    client = FakeDataClient(permission_keys=keys or ["ticket.create", "ticket.read", "setting.manage",
                                                      "customer.read", "deal.read"], role="owner")
    client._is_owner = True
    return client


class TestTheRefusalItself:
    async def test_the_owner_hears_the_plan_and_the_contact_in_three_lines(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB)
        assert reply.text == STARTER_REFUSAL + "\nอยากเปิดใช้: ติดต่อทีม Chann CRM AI LINE @channcrm"
        assert reply.quick_reply_url == ("ติดต่อทีม Chann", "https://line.me/R/ti/p/@channcrm")

    async def test_a_salesperson_hears_to_tell_the_owner(self):
        client = FakeDataClient(permission_keys=["ticket.create", "customer.read"], role="sales")
        reply = await _say(client, "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, role="sales")
        assert reply.text == STARTER_REFUSAL + "\nถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย"

    async def test_plan_comes_before_permission(self):
        # This salesperson's role does not hold ticket.create either — the
        # plan is still the truer answer (spec §5.3).
        client = FakeDataClient(permission_keys=["customer.read"], role="sales")
        reply = await _say(client, "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, role="sales")
        assert reply.text.startswith("🔒 «งานบริการ")

    async def test_without_a_contact_the_owner_is_told_who_to_ask(self, monkeypatch):
        monkeypatch.setattr(settings, "chann_sales_contact", "")
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB)
        assert reply.text.endswith("อยากเปิดใช้: ติดต่อทีม Chann CRM AI ที่ดูแลร้านของคุณ")
        assert reply.quick_reply_url is None          # no contact → no button (owner decision Q5)

    async def test_five_lines_at_most_even_with_the_ai_hint(self):
        reply = C._plan_refusal("quota.ai_reports_per_month", "th")
        assert reply.text.count("\n") <= 4

    async def test_english(self):
        client = _owner()
        ctx = _ctx(primary_role="owner", oa="sales")
        ctx.memberships[0]["plan"] = plan_payload("starter")
        reply = await handle_chat_message(client, message="open a repair job for Somchai", ctx=ctx, language="en",
                                          ai_client=httpx.AsyncClient(transport=_ai(json.dumps(OPEN_A_JOB))))
        assert reply.text.startswith("🔒 «Service jobs and technicians» is included from the Pro plan — this shop is on Starter.")


class TestOnPro:
    async def test_the_same_sentence_reaches_the_handler(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, plan="pro")
        assert "🔒" not in reply.text

    async def test_no_plan_in_the_membership_is_pro(self):
        reply = await _say(_owner(), "เปิดงานซ่อมให้คุณสมชาย", OPEN_A_JOB, plan=None)
        assert "🔒" not in reply.text

    async def test_making_an_api_key_names_enterprise(self):
        reply = await _say(_owner(["setting.manage"]), "สร้าง API key",
                           {"action": "create", "entity": "api_key", "fields": {}, "missing": []}, plan="pro")
        assert reply.text.startswith("🔒 «เชื่อมต่อ API ภายนอก» อยู่ในแพ็กเกจ Enterprise ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Pro")

    async def test_listing_api_keys_stays_open(self):
        reply = await _say(_owner(["setting.manage"]), "รายการ API key",
                           {"action": "read", "entity": "api_key", "fields": {}, "missing": []}, plan="pro")
        assert "🔒" not in reply.text


class TestTheHelpers:
    def test_no_permission_names_the_plan_for_a_locked_key(self):
        token = C._PLAN.set((entitlements.PlanView.from_payload(plan_payload("starter")), False))
        try:
            assert C._no_permission("th", "ticket.update").text.startswith("🔒 «งานบริการ")
            assert C._no_permission("th", "setting.manage").text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
            assert C._no_permission("th").text == C._t(C.SUGGEST_NO_PERMISSION_LEAD, "th")
        finally:
            C._PLAN.reset(token)

    def test_outside_a_message_the_plan_is_pro(self):
        assert C._plan_has("feature.service") and not C._plan_has("feature.external_api")

    def test_suggest_names_the_plan(self):
        token = C._PLAN.set((entitlements.PlanView.from_payload(plan_payload("starter")), False))
        try:
            text = C.suggest_what_you_can_do(["customer.read"], [], "th",
                                             requested_action="read", requested_entity="warranty")
            assert text.startswith("🔒 «ทะเบียนเครื่อง / ประกัน»")
        finally:
            C._PLAN.reset(token)

    def test_a_data_tier_refusal_body_in_words(self):
        reply = C._plan_reply_from({"error": "member_limit_reached", "limit": 5, "plan": "starter"}, "th")
        assert reply.text == ("ร้านมีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว — "
                              "เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_chat_refusal.py tests/unit/test_round21d_no_bare_refusal.py -q
```
Expected: FAIL — `AttributeError: module 'chann_app.services.chat' has no attribute '_PLAN'`, and the bare-refusal test finds 115.

- [ ] **Step 3: The copy (spec §6.2, verbatim)**

In `chat.py`, directly after `SUGGEST_NO_PERMISSION_NAMED`:

```python
# Round 21D — the plan is the reason (spec §6.2). Five lines at most.
PLAN_REQUIRED = {
    "th": "🔒 «{feature}» อยู่ในแพ็กเกจ {min_plan} ขึ้นไป — ร้านนี้ใช้แพ็กเกจ {plan}\n"
          "ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ",
    "en": "🔒 «{feature}» is included from the {min_plan} plan — this shop is on {plan}.\n"
          "Nothing the shop already has has been deleted.",
}
PLAN_REQUIRED_OWNER_TAIL = {       # the owner, or anyone holding setting.manage
    "th": "อยากเปิดใช้: ติดต่อทีม Chann CRM AI {contact}",
    "en": "To upgrade, contact the Chann CRM AI team {contact}",
}
#: CHANN_SALES_CONTACT unset (owner decision Q5): no button, and these words.
PLAN_REQUIRED_OWNER_TAIL_NO_CONTACT = {
    "th": "อยากเปิดใช้: ติดต่อทีม Chann CRM AI ที่ดูแลร้านของคุณ",
    "en": "To upgrade, contact the Chann CRM AI team that looks after your shop.",
}
PLAN_REQUIRED_MEMBER_TAIL = {
    "th": "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย",
    "en": "If you need it, let the shop owner know.",
}
AI_REPORTS_LOCKED_HINT = {        # appended on Starter's AI-report refusal
    "th": "รายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) ยังถามได้ฟรีเสมอ",
    "en": "The basic reports (pipeline value · won this month · outstanding invoices) are always free.",
}
PLAN_CONTACT_BUTTON = {"th": "ติดต่อทีม Chann", "en": "Contact Chann"}
```

Also add to `application/chann_app/services/entitlements.py` (the Registration service and chat both read it; chat imports registration, so the shared copy lives here):

```python
MEMBER_LIMIT_REACHED = {       # to the person minting an invite (spec §6.2)
    "th": "ร้านมีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว — เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม",
    "en": "The shop has all {limit} users its {plan} plan allows — remove someone who no longer uses it, or upgrade to invite more.",
}
```

- [ ] **Step 4: The context and the helpers**

Directly after `_REPLY_TO = …` (`:13488`):

```python
#: Round 21D — (the shop's plan, may this person upgrade it) for the message
#: being handled. Set by _route_chat_message once the membership and the
#: role are known; read by every refusal, so a handler that says "no" does
#: not have to be handed the plan. None outside a message → Pro.
_PLAN: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("chat_plan", default=None)
```

Directly after `def _note_road` ends:

```python
def _plan_now() -> tuple:
    held = _PLAN.get()
    return held if held else (entitlements.PlanView.unknown(), False)


def _plan_has(key: str) -> bool:
    return _plan_now()[0].has(key)


def _plan_refusal(feature: str, language: str) -> ChatReply:
    """Spec §6.2: which plan has it, which plan the shop is on, that
    nothing was deleted — then who can act on it. Decline-only: nothing
    here acts on a word."""
    plan, can_upgrade = _plan_now()
    lines = [_t(PLAN_REQUIRED, language).format(
        feature=entitlements.feature_label(feature, language),
        min_plan=plan.min_label(feature), plan=plan.label,
    )]
    button = None
    if can_upgrade:
        contact = entitlements.sales_contact()
        if contact and contact.get("label"):
            lines.append(_t(PLAN_REQUIRED_OWNER_TAIL, language).format(contact=contact["label"]))
        else:
            lines.append(_t(PLAN_REQUIRED_OWNER_TAIL_NO_CONTACT, language))
        if contact and contact.get("url"):
            button = (_t(PLAN_CONTACT_BUTTON, language), contact["url"])
    else:
        lines.append(_t(PLAN_REQUIRED_MEMBER_TAIL, language))
    if feature == entitlements.AI_REPORTS:
        lines.append(_t(AI_REPORTS_LOCKED_HINT, language))
    return ChatReply(text="\n".join(lines), quick_reply_url=button)


def _plan_reply_from(body: dict, language: str) -> ChatReply:
    """A Data-tier plan refusal (403 plan_required / 409 member_limit_reached)
    in the same words chat uses for its own."""
    if (body or {}).get("error") == "member_limit_reached":
        return ChatReply(text=_t(entitlements.MEMBER_LIMIT_REACHED, language).format(
            limit=body.get("limit"), plan=entitlements.PLAN_LABELS.get(str(body.get("plan")), str(body.get("plan"))),
        ))
    return _plan_refusal(str((body or {}).get("feature") or "feature.service"), language)


def _no_permission(language: str, *keys: str, url: tuple[str, str] | None = None) -> ChatReply:
    """Every "you may not" in chat (round 21D). A key whose feature the
    shop's plan lacks is answered with the plan; anything else with the
    permission lead it always had. Plan first: a role grant is irrelevant
    until the plan has the feature (spec §5.3)."""
    for key in keys:
        feature = entitlements.feature_of(key)
        if feature is not None and not _plan_has(feature):
            return _plan_refusal(feature, language)
    return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language), quick_reply_url=url)
```

- [ ] **Step 5: Set the context where the membership and the role are known**

`handle_chat_message` — beside `token = _ROAD.set(...)`:

```python
    plan_token = _PLAN.set(None)
```
and in its `finally:` beside `_ROAD.reset(token)`:
```python
        _PLAN.reset(plan_token)
```

`_route_chat_message`, customer branch — directly after `context: dict = {}`:

```python
        _PLAN.set((entitlements.PlanView.from_payload(member.get("plan")), False))
```

staff branch — replace `permission_keys = list(context.get("permission_keys") or [])` with:

```python
        # Round 21D: role grants MINUS what the plan locks — in one place,
        # so every `"x" in set(permission_keys)` below is plan-aware
        # without being edited (spec §5.1). Order preserved.
        plan = entitlements.PlanView.from_payload(member.get("plan"))
        role_keys = list(context.get("permission_keys") or [])
        effective, _locked = entitlements.effective_keys(role_keys, plan)
        permission_keys = [key for key in role_keys if key in effective]
        _PLAN.set((plan, bool(context.get("is_owner")) or "setting.manage" in effective))
```

- [ ] **Step 6: The central gate and `suggest_what_you_can_do`**

In `_execute_intent`, directly after `needed = required_permission(req_action, req_entity)` and before the `if (needed is None or …)` block:

```python
    # Round 21D — the plan, before the permission (spec §5.3): unknown
    # feature → wrong OA → PLAN → permission → handler. The feature comes
    # from the model's (action, entity): the key family, or a named check
    # for a feature that shares its key with always-on work.
    if needed is not None and _oa_allows(ctx.oa, needed):
        feature = entitlements.feature_for_intent(req_action, req_entity, needed)
        if feature is not None and not _plan_has(feature):
            _note_road(road="plan_locked")
            return _plan_refusal(feature, language)
```

In `suggest_what_you_can_do`, as the first statements (before `held = set(permission_keys)` — a Starter technician holds nothing, and "ask for a permission" is the wrong sentence for them):

```python
    if requested_entity:
        feature = entitlements.feature_for_intent(
            requested_action or "", requested_entity,
            required_permission(requested_action or "", requested_entity),
        )
        if feature is not None and not _plan_has(feature):
            return _plan_refusal(feature, language).text
```

- [ ] **Step 7: Convert the 115 bare sites — by script, then read the diff**

Save as `/tmp/21d-no-permission.py` and run it from the worktree:

```python
"""Round 21D Task 7 — every bare no-permission reply becomes _no_permission(...).

The key is read from the guard 1-3 lines above the reply; a reply with no
key ("not a member on this channel") gets none. Exits 1 and lists what it
could not convert — those are converted by hand."""
import re
import sys
from pathlib import Path

PATH = Path("application/chann_app/services/chat.py")
BARE = "ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language)"
HELD = r"(?:set\(permission_keys(?: or \[\])?\)|held|keys|granted)"
PATTERNS = (
    (re.compile(r'"([a-z_]+(?:\.[a-z_]+)*)"\s+not in ' + HELD), "lits"),
    (re.compile(r"not\s+(?:held|set\(permission_keys\))\s*&\s*([A-Z_]+)"), "set"),
    (re.compile(r"not\s+\{([^}]*)\}\s*&\s*" + HELD), "setlit"),
    (re.compile(r"(CHART_PERMISSION\.get\([^)]*\))\s+not in " + HELD), "expr"),
    (re.compile(r"\b(needed)\b.*not in " + HELD), "var"),
)
NO_KEY_PRECEDING = ("if member is None:",)
# _handle_reminder_list: its CS branch sits between the followup.read guard and the reply.
FAR_GUARDS = {")": '"followup.read"'}


def keys_for(lines: list[str], i: int) -> str | None:
    prev1 = lines[i - 1].strip()
    if prev1 in NO_KEY_PRECEDING:
        return ""
    for back in range(1, 4):
        prev = lines[i - back]
        if prev.strip().startswith(("async def", "def ")):
            break
        for rx, kind in PATTERNS:
            m = rx.search(prev)
            if not m:
                continue
            if kind == "lits":
                return ", ".join(f'"{k}"' for k in rx.findall(prev))
            if kind == "set":
                return f"*{m.group(1)}"
            if kind == "setlit":
                return m.group(1).strip()
            return m.group(1)
    return FAR_GUARDS.get(prev1)


def main() -> int:
    lines = PATH.read_text(encoding="utf-8").split("\n")
    left = []
    for i, line in enumerate(lines):
        if BARE not in line:
            continue
        keys = keys_for(lines, i)
        head, tail = line.split(BARE, 1)
        url = re.match(r"^,\s*quick_reply_url=(.*)\)\s*$", tail)
        if keys is None or (not url and tail.strip() != ")"):
            left.append(f"{i + 1}: {line.strip()}")
            continue
        args = ["language"] + ([keys] if keys else []) + ([f"url={url.group(1)}"] if url else [])
        lines[i] = f"{head}_no_permission({', '.join(args)})"
    PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"left for hand conversion: {len(left)}")
    for row in left:
        print("  " + row)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
```

```bash
cd ~/stage-fix/r21d && /tmp/dv/bin/python /tmp/21d-no-permission.py
grep -c "_no_permission(language" application/chann_app/services/chat.py
git diff -U0 application/chann_app/services/chat.py | grep '^+.*_no_permission(language' | sed 's/^+ *//' | sort | uniq -c | sort -rn
```
Expected: `left for hand conversion: 0`; 115 converted (on `404cfe3` the script was dry-run on a copy and converted all 115). **Read the grouped diff**: every key must be the one its guard tests. Known shapes, each checked on the dry run: `*TEAM_VIEW_KEYS` (2), `*WORK_VIEW_KEYS`, `*DEAL_VIEW_KEYS`, `*PRODUCT_VIEW_KEYS`, `"approval.manage", "approval.view"`, `CHART_PERMISSION.get(kind, "view_reports")`, `needed` (9), `"followup.read"` for `_handle_reminder_list`, and four keyless `_no_permission(language)` after `if member is None:`. If the count differs because an earlier task added a site, convert the new one by hand the same way.

- [ ] **Step 8: The fakes learn the plan**

`tests/unit/test_phase6_chat.py`, in `FakeDataClient`, directly after `authorization_context`:

```python
    async def license_plan(self, license_id):
        # Round 21D: the shop's plan and its usage. `_plan` None is what an
        # older Data tier sends — the Application then acts as Pro.
        return {"plan": getattr(self, "_plan", None),
                "usage": {"members": getattr(self, "_members_count", 1),
                          "members_limit": (getattr(self, "_plan", None) or {}).get("limits", {}).get("members", 15),
                          "ai_reports_used": 0, "ai_reports_allowance": 30, "ai_reports_month": "2026-09"}}
```

The agent-test channel (so shipped scenarios can say which plan they run on):
- `scripts/agent-test/agent_test_runner/scenario.py:52` → `ACTOR_KEYS = {"oa", "role", "language", "permissions", "line_user_id", "plan"}`, and after the `oa` validation: `if actor.get("plan") and actor["plan"] not in ("starter", "pro", "enterprise", "enterprise_plus"): _fail(where, f"unknown actor plan {actor['plan']!r}")`
- `scripts/agent-test/scenario.schema.json` → in `$defs.actor.properties`: `"plan": {"enum": ["starter", "pro", "enterprise", "enterprise_plus"], "description": "fake backend only: the shop's sales plan (round 21D). Omitted = what an older Data tier sends, which the Application treats as Pro."}`
- `scripts/agent-test/run.py:189` → pass `plan=actor.get("plan"),` to `backend.send(...)`
- `FakeBackend.send(self, *, message, oa, role, language, permissions, ai, refs, plan=None)`: after `ctx = self._t._ctx(...)`:
  ```python
        if plan:
            from plan_fixtures import plan_payload
            ctx.memberships[0]["plan"] = plan_payload(plan)
            self.client._plan = ctx.memberships[0]["plan"]
  ```
- `DbBackend.send(..., plan=None)`: accept and ignore it (the db backend's shop is created by the real registration road and is Pro by the server default); if `plan` is set, append the note `"actor.plan is fake-backend only; the db shop is Pro"` once, the way `permissions` is noted.
- `scripts/agent-test/scenarios/api-keys.yaml` → under `actor:` add `plan: enterprise  # round 21D: making a key is an Enterprise feature`.

- [ ] **Step 9: Run the new tests, then the chat suites, the sims and the scenarios**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_chat_refusal.py tests/unit/test_round21d_no_bare_refusal.py -q
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit -q -p no:cacheprovider -x -k "chat or phase6 or round2 or assistant" \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors" | tail -3
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/measure-capabilities.py | tail -1
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/simulate-day.py | tail -1 && /tmp/dv/bin/python scripts/dev/simulate-edge-cases.py | tail -1
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/agent-test/run.py | tail -2
```
Expected: the new tests pass; `measure-capabilities` still `· 0 with no handler · 0 raising`; both sims `0 FINDINGS`; agent-test `… passed · 0 failed`. A chat test that now fails on a Pro-default fake is one of: `tests/unit/test_round21b_api_chat.py` (making a key) or a two-step approval-policy test — give its ctx `ctx.memberships[0]["plan"] = plan_payload("enterprise")` with the round-21D comment. Anything else is a regression.

- [ ] **Step 10: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): chat names the plan before the permission — one refusal, the central gate, and every bare no-permission reply asks the plan first" && git log --oneline -1
```

---

### Task 8: The LINE OAs on a plan without the feature — technicians, customers, invites

**Files:**
- Modify: `application/chann_app/services/entitlements.py` (the §7 sentences)
- Modify: `application/chann_app/services/chat.py` — `_route_chat_message`: the technician gate directly after the suspended check (`:26691-26694`, anchor `grep -n 'TENANT_SUSPENDED, language).format' application/chann_app/services/chat.py`), the customer gate at the top of `if ctx.oa == "customer":` (`:26696`); `_handle_invite_request` (`:484-500`); `_handle_document_send` (`:24909`, after its permission guard)
- Modify: `application/chann_app/services/registration.py` — `_redeem_invite_reply` (`:811-842`), `_link_and_continue` (`:570-590`)
- Modify: `application/chann_app/services/notify.py:23-58` (`TYPE_TO_OA` gains `member_limit_reached`, `plan_changed`)
- Test: `tests/unit/test_round21d_oa_behaviour.py`

**Interfaces:**
- Consumes (Task 7): `chat._PLAN`, `_plan_has`, `_plan_refusal`, `_plan_reply_from`; (Task 5) `entitlements.plan_for`, `PLAN_LABELS`, `is_plan_refusal`, `MEMBER_LIMIT_REACHED`; (Task 4) the 403 bodies carrying `company_name` / `company_phone`; (Task 3) the 409 `member_limit_reached` body carrying `license_id`, `company_name`, `owner_chann_uid`.
- Produces: `entitlements.TECH_INVITE_PLAN_LOCKED`, `TECH_OA_PLAN_LOCKED`, `CUSTOMER_OA_PLAN_LOCKED`, `CUSTOMER_OA_PLAN_LOCKED_NO_PHONE`, `MEMBER_LIMIT_JOIN_REFUSED`, `MEMBER_LIMIT_OWNER_NOTICE`; `registration._tell_owner_turned_away(client, body) -> None`; notification types `member_limit_reached`, `plan_changed` (Sales OA).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21d_oa_behaviour.py`:

```python
"""Round 21D — the three OAs on a plan without the feature (spec §7).

A technician of a downgraded shop is told once, per message, that the
shop switched service off — profile and language still work; a customer
of a shop without the Customer LINE link hears how to reach the shop; the
storefront (platform-wide) still works; an invite code made before a
downgrade is refused and not spent."""
from __future__ import annotations

import json

import httpx
import pytest

from chann_app.config import settings
from chann_app.data_client import DataTierError
from chann_app.services import registration
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _say(client, message, *, oa, plan="starter", ai=None, role=None):
    ctx = _ctx(primary_role=role or oa, oa=oa)
    ctx.memberships[0]["plan"] = plan_payload(plan)
    transport = _ai(json.dumps(ai or {"action": "suggest", "entity": None, "fields": {}, "missing": []}))
    return await handle_chat_message(client, message=message, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=transport))


class TestTheTechnicianOA:
    async def test_anything_about_the_shop_gets_the_notice(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read", "ticket.update"])
        reply = await _say(client, "งานของฉัน", oa="technician")
        assert reply.text == ("ร้าน บริษัททดสอบ ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ Starter) — "
                              "งานและรายงานเดิมยังอยู่ครบ แจ้งเจ้าของร้านถ้าต้องการเปิดใช้")

    async def test_switching_language_still_works(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read"])
        reply = await _say(client, "english please", oa="technician")
        assert "ปิดงานบริการ" not in reply.text

    async def test_on_pro_nothing_changes(self):
        client = FakeDataClient(role="technician", permission_keys=["ticket.read"])
        reply = await _say(client, "งานของฉัน", oa="technician", plan="pro")
        assert "ปิดงานบริการ" not in reply.text


class TestTheCustomerOA:
    async def test_a_linked_customer_hears_how_to_reach_the_shop(self):
        client = FakeDataClient(role="customer", permission_keys=[],
                                company_profile={"company_name": "บริษัททดสอบ", "company_phone": "021234567"})
        reply = await _say(client, "แอร์ไม่เย็น", oa="customer")
        assert reply.text == "ร้าน บริษัททดสอบ ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ 021234567"

    async def test_without_a_phone_the_line_is_left_out(self):
        client = FakeDataClient(role="customer", permission_keys=[])
        reply = await _say(client, "แอร์ไม่เย็น", oa="customer")
        assert reply.text == "ร้าน บริษัททดสอบ ยังไม่เปิดให้บริการลูกค้าทาง LINE"

    async def test_the_storefront_still_works(self):
        client = FakeDataClient(role="customer", permission_keys=[],
                                storefront_results=[{"product_name": "แอร์ 12000 BTU", "company_name": "ร้านอื่น",
                                                     "company_code": "ABCD2345", "unit_price": "15000"}])
        reply = await _say(client, "สินค้าทั้งหมด", oa="customer")
        assert "ยังไม่เปิดให้บริการลูกค้าทาง LINE" not in reply.text


class TestInvites:
    async def test_starter_owner_asking_for_a_technician_code_hears_the_plan(self):
        client = FakeDataClient(role="owner", permission_keys=["member.manage", "setting.manage"])
        client._is_owner = True
        client._plan = plan_payload("starter")
        reply = await _say(client, "ขอรหัสเชิญช่าง", oa="sales", role="owner")
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป")
        assert not [r for r in client.recorded if r[0] == "create_invite"]

    async def test_at_the_limit_no_code_is_made(self):
        client = FakeDataClient(role="owner", permission_keys=["member.manage"])
        client._plan = plan_payload("starter")
        client._members_count = 5
        reply = await _say(client, "ขอรหัสเชิญทีมขาย", oa="sales", role="owner")
        assert reply.text == ("ร้านมีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว — "
                              "เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม")


class _Redeeming(FakeDataClient):
    def __init__(self, body, status, **kw):
        super().__init__(**kw)
        self._body, self._status = body, status

    async def redeem_invite(self, *, invite_code, chann_uid, display_name=None, oa=None):
        raise DataTierError(self._status, str(self._body), self._body)


class TestRedeemingACode:
    async def test_a_technician_code_on_a_downgraded_shop(self):
        client = _Redeeming({"error": "plan_required", "feature": "feature.service", "plan": "starter",
                             "min_plan": "pro", "company_name": "ร้านแอร์เย็น"}, 403)
        ctx = _ctx(primary_role="technician", oa="technician")
        text = await registration._redeem_invite_reply(client, "ABCDEFGHJK", ctx, "th", oa="technician")
        assert text == ("รหัสนี้ใช้ไม่ได้ตอนนี้ — ร้าน ร้านแอร์เย็น ใช้แพ็กเกจ Starter "
                        "ซึ่งยังไม่มีงานบริการและทีมช่าง แจ้งเจ้าของร้านได้เลย")

    async def test_a_full_shop_refuses_the_joiner_and_tells_the_owner(self):
        client = _Redeeming({"error": "member_limit_reached", "limit": 5, "plan": "starter",
                             "license_id": "11111111-1111-1111-1111-111111111111",
                             "company_name": "ร้านแอร์เย็น", "owner_chann_uid": "CHN-OWNER"}, 409)
        client._line_targets = {"CHN-OWNER": "U-owner-line"}
        ctx = _ctx(primary_role="sales", oa="sales")
        text = await registration._redeem_invite_reply(client, "ABCDEFGHJK", ctx, "th", oa="sales")
        assert text == ("ร้าน ร้านแอร์เย็น มีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว ยังเข้าร่วมไม่ได้ — "
                        "แจ้งเจ้าของร้านได้เลย")
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert told and told[0][2] == "CHN-OWNER" and told[0][3] == "member_limit_reached"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_oa_behaviour.py -q
```
Expected: FAIL — the technician gets the ticket list, the customer gets the report flow, the code is minted, the redeem answers `BAD_CODE`.

- [ ] **Step 3: The sentences (spec §7, verbatim where the spec has them)**

Append to `application/chann_app/services/entitlements.py`:

```python
#: Spec §7.2 — a technician invite code typed on a shop without service.
TECH_INVITE_PLAN_LOCKED = {
    "th": "รหัสนี้ใช้ไม่ได้ตอนนี้ — ร้าน {company} ใช้แพ็กเกจ {plan} ซึ่งยังไม่มีงานบริการและทีมช่าง แจ้งเจ้าของร้านได้เลย",
    "en": "This code can't be used right now — {company} is on the {plan} plan, which has no service jobs or technicians. Let the shop owner know.",
}
#: Spec §7.2 — an existing technician of a shop that downgraded.
TECH_OA_PLAN_LOCKED = {
    "th": "ร้าน {company} ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ {plan}) — งานและรายงานเดิมยังอยู่ครบ แจ้งเจ้าของร้านถ้าต้องการเปิดใช้",
    "en": "{company} has service jobs switched off for now ({plan} plan) — earlier jobs and reports are all kept. Tell the shop owner if you need them.",
}
#: Spec §7.3 — the Customer OA of a shop without the Customer LINE link.
CUSTOMER_OA_PLAN_LOCKED = {
    "th": "ร้าน {company} ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ {phone}",
    "en": "{company} does not serve customers on LINE yet — reach the shop on {phone}",
}
CUSTOMER_OA_PLAN_LOCKED_NO_PHONE = {
    "th": "ร้าน {company} ยังไม่เปิดให้บริการลูกค้าทาง LINE",
    "en": "{company} does not serve customers on LINE yet",
}
#: Spec §5.7 — the person whose join was turned away, and the owner, who
#: must hear it (a failed join is never silent).
MEMBER_LIMIT_JOIN_REFUSED = {
    "th": "ร้าน {company} มีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว ยังเข้าร่วมไม่ได้ — แจ้งเจ้าของร้านได้เลย",
    "en": "{company} already has all {limit} users its {plan} plan allows, so you can't join yet — let the shop owner know.",
}
MEMBER_LIMIT_OWNER_NOTICE = {
    "th": "มีคนใช้รหัสเชิญเข้าร้าน {company} ไม่สำเร็จ เพราะผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว — เอาสมาชิกที่ไม่ได้ใช้ออก หรือติดต่อทีม Chann เพื่ออัปเกรด",
    "en": "Someone could not join {company} with an invite code: all {limit} users of the {plan} plan are taken. Remove someone who no longer uses it, or contact Chann to upgrade.",
}
```

`application/chann_app/services/notify.py` — add to `TYPE_TO_OA`:

```python
    # Round 21D: the owner hears a turned-away join and a plan change.
    "member_limit_reached": "sales",
    "plan_changed": "sales",
```

- [ ] **Step 4: The two OA gates (`chat.py`, `_route_chat_message`)**

Directly after the suspended check's `return ChatReply(...)` block:

```python
    plan_here = entitlements.PlanView.from_payload(member.get("plan"))
    if ctx.oa == "technician" and not plan_here.has("feature.service") \
            and _language_switch_requested(message) is None and not _looks_like_profile_edit(message):
        # Spec §7.2: once per message; profile, language and the shop
        # switch (handled above) still work — they are not the shop's feature.
        _note_road(road="plan_locked")
        return ChatReply(text=_t(entitlements.TECH_OA_PLAN_LOCKED, language).format(
            company=member.get("company_name") or "", plan=plan_here.label))
```

First statements inside `if ctx.oa == "customer":` (before `permission_keys: list[str] = []`):

```python
        if not plan_here.has("feature.customer_line_link"):
            # Spec §7.3: the storefront is platform-wide, not this shop's
            # feature — it still answers. Everything about the shop gets
            # one sentence; the link row is kept.
            browsed = await maybe_handle_storefront(client, message=message, ctx=ctx, language=language)
            if browsed is not None:
                return browsed
            phone = ""
            try:
                phone = str((await client.get_company_profile(str(license_id)) or {}).get("company_phone") or "")
            except Exception:  # noqa: BLE001 — the sentence works without the number
                log.exception("could not read the shop's phone for a locked customer reply")
            table = entitlements.CUSTOMER_OA_PLAN_LOCKED if phone else entitlements.CUSTOMER_OA_PLAN_LOCKED_NO_PHONE
            _note_road(road="plan_locked")
            return ChatReply(text=_t(table, language).format(company=member.get("company_name") or "", phone=phone))
```

- [ ] **Step 5: Minting an invite refuses early (`chat.py`, `_handle_invite_request`)**

After the `member.manage` check, before `client.create_invite(...)`:

```python
    # Round 21D (spec §5.7, §7.2): say it before a code is handed out — a
    # technician code needs service; any code needs a free seat.
    plan, usage = await entitlements.plan_for(client, str(ctx.license_id))
    if role == "technician" and not plan.has("feature.service"):
        return _plan_refusal("feature.service", language)
    if plan.members_limit is not None and int(usage.get("members") or 0) >= plan.members_limit:
        return ChatReply(text=_t(entitlements.MEMBER_LIMIT_REACHED, language).format(
            limit=plan.members_limit, plan=plan.label))
    try:
        invite = await client.create_invite(
            str(ctx.license_id),
            {"role": role, "max_uses": 1, "expires_in_days": 7},
            actor_id=ctx.chann_uid,
        )
    except DataTierError as exc:
        if entitlements.is_plan_refusal(exc):
            return _plan_reply_from(exc.structured or {}, language)
        raise
```

(replacing the bare `invite = await client.create_invite(...)` statement).

`_handle_document_send` — directly after its `if needed not in set(permission_keys):` guard (which Task 7 turned into `_no_permission`):

```python
    if not _plan_has("feature.customer_line_link"):
        # The trigger road reaches here without the central gate.
        return _plan_refusal("feature.customer_line_link", language)
```

- [ ] **Step 6: Redeeming and linking say why (`registration.py`)**

Import `from . import entitlements` at the top. In `_redeem_invite_reply`, first lines of the `except Exception as exc:` block:

```python
        body = getattr(exc, "structured", None)
        if isinstance(body, dict) and body.get("error") == "plan_required":
            # Spec §7.2: the code is not spent; it works after an upgrade.
            return _t(entitlements.TECH_INVITE_PLAN_LOCKED, language).format(
                company=body.get("company_name") or "",
                plan=entitlements.PLAN_LABELS.get(str(body.get("plan")), str(body.get("plan") or "")))
        if isinstance(body, dict) and body.get("error") == "member_limit_reached":
            await _tell_owner_turned_away(client, body)
            return _t(entitlements.MEMBER_LIMIT_JOIN_REFUSED, language).format(
                company=body.get("company_name") or "", limit=body.get("limit"),
                plan=entitlements.PLAN_LABELS.get(str(body.get("plan")), str(body.get("plan") or "")))
```

and the helper, directly above `_redeem_invite_reply`:

```python
async def _tell_owner_turned_away(client: DataClient, body: dict) -> None:
    """Spec §5.7: a join refused by the limit is never silent — the owner
    hears it on the Sales OA. Best effort: the joiner's answer never waits
    on it."""
    from .notify import send_notification

    owner, license_id = str(body.get("owner_chann_uid") or ""), str(body.get("license_id") or "")
    if not owner or not license_id:
        return
    plan = entitlements.PLAN_LABELS.get(str(body.get("plan")), str(body.get("plan") or ""))
    words = {"company": body.get("company_name") or "", "limit": body.get("limit"), "plan": plan}
    try:
        await send_notification(
            client, license_id=license_id, target_chann_uid=owner,
            target_line_user_id=await client.line_target_of(owner),
            type="member_limit_reached",
            message=entitlements.MEMBER_LIMIT_OWNER_NOTICE["th"].format(**words),
            message_en=entitlements.MEMBER_LIMIT_OWNER_NOTICE["en"].format(**words),
            entity_type="license", entity_id=license_id, oa="sales",
        )
    except Exception:  # noqa: BLE001
        log.exception("could not tell the owner of %s about a refused join", license_id)
```

In `_link_and_continue`, first lines of its `except Exception as exc:`:

```python
        body = getattr(exc, "structured", None)
        if isinstance(body, dict) and body.get("error") == "plan_required":
            # Spec §7.3: no link row is written; say how to reach the shop.
            phone = str(body.get("company_phone") or "")
            table = entitlements.CUSTOMER_OA_PLAN_LOCKED if phone else entitlements.CUSTOMER_OA_PLAN_LOCKED_NO_PHONE
            return _t(table, language).format(company=body.get("company_name") or company_name or company_code,
                                              phone=phone)
```

(`registration.py` defines its own `_t` — `grep -n '^def _t' application/chann_app/services/registration.py`; if it does not, use `table["en" if language == "en" else "th"]`.)

- [ ] **Step 7: Run to verify, then the registration and chat suites**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_oa_behaviour.py tests/unit/test_phase65_registration.py tests/unit/test_persona_members.py tests/unit/test_round19v_shop_code.py -q
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/agent-test/run.py | tail -2
```
Expected: all pass; agent-test `0 failed` (every shipped scenario runs on the Pro default, which has service and the customer link).

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the technician and customer OAs say when the shop's plan has switched their side off, invites refuse before a code is made, and a turned-away join reaches the owner" && git log --oneline -1
```

---

### Task 9: AI report credits come from the plan, and Starter's three basic reports

**Files:**
- Modify: `data/chann_data/repositories/phase2.py:277-296` (`QUOTA_KEY`/`USAGE_KEY`/`DEFAULT_QUOTA`/`ai_chart_allowance`)
- Modify: `application/chann_app/services/chart_quota.py:26-46` (`DEFAULT_QUOTA` → the Pro fallback from `entitlements`)
- Modify: `application/chann_app/routers_phase2.py` — `ai_report_ask` (`:5275-5320`), `ai_report_run` (`:5375`), `basic_report` (`:5416`)
- Modify: `application/chann_app/services/chat.py` — `_handle_ai_report` (`:31145`, after its `view_reports` guard), `_handle_basic_report` (`:31041`, after its guard, and its buttons at `:31064-31080`), `_handle_basic_report_picture` (`:30989`, first lines)
- Test: `tests/integration/test_round21d_plan_data.py` (one class appended), `tests/unit/test_round21d_credits.py`

**Interfaces:**
- Consumes (Task 3): `PlanRepository.payload`; (Task 5) `entitlements.AI_REPORTS`, `SERVICE_BASIC_REPORTS`, `UNKNOWN_PLAN`, `TenantPrincipal.require_feature`; (Task 7) `_plan_has`, `_plan_refusal`.
- Produces: `LicenseSettingRepository.ai_chart_allowance(scope)` = the plan's allowance with the admin override honoured on Pro and up (owner decision Q3); `chart_quota.FAIL_OPEN_ALLOWANCE`; the Starter AI road refused before any model call; the two service reports refused on a plan without service, on both surfaces; buttons under a basic report that never offer a locked report or a locked picture.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_round21d_plan_data.py`:

```python
class TestTheCreditAllowance:
    """Owner decision Q3 (24 ก.ย. 2569): Enterprise Plus starts at 100; the
    override is honoured on Pro and up; values already set keep working;
    Starter is 0 — locked, never "used up"."""

    @pytest.mark.parametrize("code,override,expected", [
        ("pro", None, 30), ("pro", 45, 45), ("pro", 0, 0), ("enterprise", None, 100),
        ("enterprise_plus", None, 100), ("enterprise_plus", 400, 400), ("starter", 45, 0),
    ])
    def test_allowance(self, migrated_db, make_shop, code, override, expected):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop(code, 1)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            if override is not None:
                LicenseSettingRepository(s).upsert(scope, "ai_chart_quota", override)
                s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).ai_chart_allowance(scope) == expected

    def test_the_last_credit_and_the_one_after(self, migrated_db, make_shop):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop("pro", 1)
        scope = TenantScope(license_id=license_id)
        with Session(migrated_db) as s:
            LicenseSettingRepository(s).upsert(scope, "ai_chart_quota", 1)
            s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(scope, month="2026-09") == (True, 1, 1)
            s.commit()
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(scope, month="2026-09") == (False, 1, 1)

    def test_starter_spends_nothing(self, migrated_db, make_shop):
        from chann_data.repositories.phase2 import LicenseSettingRepository

        license_id, _ = make_shop("starter", 1)
        with Session(migrated_db) as s:
            assert LicenseSettingRepository(s).consume_ai_chart(
                TenantScope(license_id=license_id), month="2026-09") == (False, 0, 0)
```

Create `tests/unit/test_round21d_credits.py`:

```python
"""Round 21D — AI reports on Starter are locked before any model call; the
two service basic reports need the service feature (owner decision Q4);
the five-report buttons never offer what the plan locks."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chann_app import routers_phase2
from chann_app.config import settings
from chann_app.services import authorization, chart_quota, entitlements
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import LICENSE_ID, FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


class _Counting(httpx.MockTransport):
    def __init__(self, content: str):
        self.calls = 0

        def handler(request):
            self.calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}],
                                             "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        super().__init__(handler)


async def _say(message, *, plan, ai=None, keys=("view_reports", "deal.read", "setting.manage")):
    client = FakeDataClient(role="owner", permission_keys=list(keys))
    client._is_owner = True
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload(plan)
    transport = _Counting(json.dumps(ai or {"action": "read", "entity": "report", "fields": {}, "missing": []}))
    reply = await handle_chat_message(client, message=message, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=transport))
    return reply, transport.calls, client


class TestTheAiRoadOnStarter:
    async def test_the_typed_prefix_declines_before_the_model(self):
        reply, calls, client = await _say("สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน", plan="starter")
        assert reply.text.startswith("🔒 «ถามรายงานด้วย AI» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter")
        assert "รายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) ยังถามได้ฟรีเสมอ" in reply.text
        assert calls == 0
        assert not [r for r in client.recorded if r[0] == "consume_ai_chart_quota"]

    async def test_a_service_basic_report_on_starter_is_the_service_refusal(self):
        reply, _calls, _client = await _say(
            "งานซ่อมค้างแยกตามช่าง", plan="starter",
            ai={"action": "read", "entity": "report", "fields": {"type": "jobs"}, "missing": [],
                "report": "open_jobs_by_tech"})
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง»")

    async def test_the_free_three_still_answer_and_offer_only_what_the_plan_has(self):
        reply, _calls, _client = await _say(
            "ยอดมูลค่าดีลทั้งหมด", plan="starter",
            ai={"action": "read", "entity": "report", "fields": {"type": "sales"}, "missing": [],
                "report": "pipeline_value"})
        assert "🔒" not in reply.text
        offered = " ".join(text for _label, text in reply.quick_replies)
        assert "open_jobs_by_tech" not in offered and "satisfaction_avg" not in offered
        assert "ดูเป็นรูป" not in " ".join(label for label, _t in reply.quick_replies)


def _app(code: str, keys: set[str]) -> TestClient:
    principal = authorization.build_principal(
        license_id=LICENSE_ID, chann_uid="CHN-S-000001", role="owner", is_owner=True, role_keys=keys,
        audience="sales", license_status="active", plan_payload=plan_payload(code))

    async def override_client():
        yield FakeDataClient()

    async def override_principal():
        return principal

    app = FastAPI()
    app.include_router(routers_phase2.router)
    app.dependency_overrides[routers_phase2.get_data_client] = override_client
    app.dependency_overrides[routers_phase2.get_tenant_principal] = override_principal
    return TestClient(app)


class TestTheDashboardRoutes:
    def test_the_service_basic_reports_need_service(self):
        http = _app("starter", {"view_reports"})
        out = http.get(f"/api/v1/licenses/{LICENSE_ID}/reports/basic/satisfaction_avg")
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "feature.service"

    def test_running_an_edited_report_on_starter_is_the_ai_refusal(self):
        http = _app("starter", {"view_reports"})
        out = http.post(f"/api/v1/licenses/{LICENSE_ID}/reports/ai/run", json={"spec": {"entity": "deals"}})
        assert out.status_code == 403 and out.json()["detail"]["feature"] == "quota.ai_reports_per_month"


def test_the_fail_open_allowance_is_pros():
    assert chart_quota.FAIL_OPEN_ALLOWANCE == entitlements.UNKNOWN_PLAN["limits"]["ai_reports_per_month"] == 30
    assert not hasattr(chart_quota, "DEFAULT_QUOTA")
```

Check the real body model of `/reports/ai/run` first — `grep -n 'class AiReportRunBody' -A6 application/chann_app/routers_phase2.py` — and send the smallest body it accepts; the plan check must run **before** the body's spec is used, so an otherwise-valid body is enough.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_credits.py -q
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_plan_data.py -q -k Credit
```
Expected: FAIL — the prefix reaches the model, the service report answers, `enterprise` gives 30, `starter` with an override gives 45, `DEFAULT_QUOTA` still exists.

- [ ] **Step 3: The Data tier's allowance**

In `data/chann_data/repositories/phase2.py`, replace `DEFAULT_QUOTA = 30` and `ai_chart_allowance` with:

```python
    #: Round 21D: the allowance is the PLAN's (chann_data/plans.py), with
    #: this shop's `ai_chart_quota` override honoured on Pro and up (owner,
    #: 24 ก.ย. 2569). The setting's name stays — no rename for a word.
    def ai_chart_allowance(self, scope: TenantScope) -> int:
        from .plan_repo import PlanRepository

        return int(PlanRepository(self._s).payload(scope.license_id)["limits"]["ai_reports_per_month"])
```

(`QUOTA_KEY` and `USAGE_KEY` stay: `consume_ai_chart` still reads `USAGE_KEY`.)

- [ ] **Step 4: The Application's fail-open allowance**

In `application/chann_app/services/chart_quota.py`, replace `DEFAULT_QUOTA = 30` with:

```python
from . import entitlements

#: When the Data tier cannot answer, the spend fails open with Pro's
#: allowance — the same Pro the principal falls back to (round 21D).
FAIL_OPEN_ALLOWANCE = int(entitlements.UNKNOWN_PLAN["limits"]["ai_reports_per_month"])
```

and use `FAIL_OPEN_ALLOWANCE` in the two places `DEFAULT_QUOTA` was used in `spend_one`.

- [ ] **Step 5: The dashboard routes**

`ai_report_ask` — replace the `if key is not None:` block and add the AI check after it:

```python
    if key is not None:
        if key in entitlements.SERVICE_BASIC_REPORTS:
            # Owner decision Q4: Starter sees three basic reports.
            principal.require_feature("feature.service")
        try:
            report = await basic_reports.fetch(client, license_id=license_id, key=key)
        except DataTierError as exc:
            raise _propagate(exc)
        return {"basic": report, "text": basic_reports.as_text(report, language), "free": True}
    # Round 21D: every other question is the AI road — locked on Starter,
    # before the model is asked anything (spec §5.6).
    principal.require_feature(entitlements.AI_REPORTS)
```

`ai_report_run` — directly after `principal.require("view_reports")`: `principal.require_feature(entitlements.AI_REPORTS)`.

`basic_report` — directly after `principal.require("view_reports")`:

```python
    if key in entitlements.SERVICE_BASIC_REPORTS:
        principal.require_feature("feature.service")
```

- [ ] **Step 6: Chat**

`_handle_ai_report` — directly after its `view_reports` guard:

```python
    if not _plan_has(entitlements.AI_REPORTS):
        # Round 21D: locked, not used up — no model call, no credit. The
        # typed "สร้างรายงานด้วย AI:" prefix declines here (a word declining
        # is allowed; spec §5.3).
        return _plan_refusal(entitlements.AI_REPORTS, language)
```

`_handle_basic_report_picture` — first statement: the same three lines (the picture is the metered road).

`_handle_basic_report` — directly after its `view_reports` guard:

```python
    if key in entitlements.SERVICE_BASIC_REPORTS and not _plan_has("feature.service"):
        return _plan_refusal("feature.service", language)
```

and its buttons offer only what the plan has — replace the button construction (anchor `buttons: list[tuple[str, str]] = [(`) with:

```python
    buttons: list[tuple[str, str]] = []
    if _plan_has(entitlements.AI_REPORTS):
        # The picture is the metered road; Starter has none (round 21D).
        buttons.append((
            _t(BASIC_REPORT_PICTURE, language),
            _t(BASIC_REPORT_PICTURE_SAYS, language).format(
                title=basic_reports.TITLES[key][said]),
        ))
    # The others, every time — the ones this plan has (owner decision Q4).
    buttons += [
        (_t(BASIC_REPORT_SHORT.get(other) or basic_reports.TITLES[other], language),
         f"{BASIC_REPORT_PREFIX} {other}")
        for other in basic_reports.REPORT_KEYS
        if other != key and (other not in entitlements.SERVICE_BASIC_REPORTS or _plan_has("feature.service"))
    ]
```

- [ ] **Step 7: Run to verify, then the 21C report suites**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_credits.py tests/unit/test_round21c_reports_chat.py tests/unit/test_round21c_basic_reports.py tests/unit/test_round20c_chart_quota.py tests/unit/test_ai_reports.py -q
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21d_plan_data.py tests/integration/test_ai_reports_data.py tests/integration/test_round21c_basic_reports.py -q
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/agent-test/run.py --only round21c-reports | tail -2
```
Expected: all pass. The 21C tests run on the Pro default, which keeps all five reports and 30 credits — a 21C test that asserted `30` because it was `DEFAULT_QUOTA` still sees 30 (Pro's allowance); one that read `DEFAULT_QUOTA` by name must read `FAIL_OPEN_ALLOWANCE` (say so in its docstring).

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): AI report credits come from the plan with the admin top-up on Pro and up, Starter's AI road declines before the model, and Starter sees three basic reports" && git log --oneline -1
```

---

### Task 10: Chat reads the plan — "ร้านใช้แพ็กเกจอะไร", measured with the real model first

The dashboard gains a plan card (Task 12), so chat must answer the same question (owner rule 2). This is the round's **only prompt change**, so it follows `docs/MODEL_FIRST.md` in order: ask the deployed model **before** touching anything, change the prompt only if the model does not already read these sentences, and compare **per sentence** after.

**Files:**
- Modify: `application/chann_app/services/chat.py` — `ACTION_PERMISSIONS` (`:103-273`, after the `api_key` rows), `ENTITY_DASHBOARD_PAGE` (`:30751`), the `_execute_intent` dispatch (after the `assignment_rule` branch, `:30246`), `_handle_plan_read` + its copy (new, directly above `_handle_survey_summary`, `:29742`)
- Modify: `application/chann_app/services/ai/intent.py` — the `entity="plan"` block, directly after the `entity="api_key"` block (`:461-471`)
- Modify: `scripts/dev/check-parity.py` — `URL_ENTITIES` gains `("plan", "plan")`
- Create: `/tmp/21d-plan-sentences.txt` (measurement input, not committed)
- Test: `tests/unit/test_round21d_plan_read.py`

**Interfaces:**
- Consumes (Task 5): `entitlements.plan_for`, `feature_label`, `PLAN_LABELS`, `sales_contact`; (Task 7) `_no_permission`, `_PLAN`.
- Produces: `ACTION_PERMISSIONS[("read", "plan")] = "setting.manage"`; `ENTITY_DASHBOARD_PAGE["plan"] = ("company", …)`; `async _handle_plan_read(client, *, ctx, license_id, permission_keys, language) -> ChatReply`; the handoff's before/after readings.

- [ ] **Step 1: Measure BEFORE — the three sentences and their neighbours**

```bash
cat > /tmp/21d-plan-sentences.txt <<'EOF'
# the three the dashboard's plan card answers (spec §6.1)
ร้านใช้แพ็กเกจอะไร
เหลือเครดิตรายงาน AI เท่าไหร่
เชิญได้อีกกี่คน
# the same question, other words
แพ็กเกจของร้าน
what plan are we on
# neighbours that must NOT move
ข้อมูลบริษัท
รหัสร้านเราคืออะไร
ดูรหัสเชิญ
ขอรหัสเชิญทีมขาย
รายการ API key
สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน
ยอดมูลค่าดีลทั้งหมด
EOF
cd ~/stage-fix/r21d && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py --oa sales --role owner \
  --file /tmp/21d-plan-sentences.txt | tee /tmp/21d-plan-before.txt
```
(≈ $0.006 for the 12 sentences.) **Read every line.** Record the raw JSON for all twelve — it goes into the handoff (Task 15). If the model already returns one consistent `(action, entity)` for the first five (e.g. `read`/`setting` with a telling field), map that shape in `_handle_plan_read`'s dispatch **instead of** changing the prompt, and skip Step 4 — but the `entity="plan"` block is still required by `test_every_conversational_capability_is_in_the_prompt` once `("read", "plan")` is registered, so in practice the block is added; the measurement decides whether it changes anything else.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_round21d_plan_read.py`:

```python
"""Round 21D — "ร้านใช้แพ็กเกจอะไร" in chat, the twin of the dashboard's plan
card (spec §6.1, owner rule 2)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from chann_app.config import settings
from chann_app.services import chat as C
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[2]
READ_PLAN = {"action": "read", "entity": "plan", "fields": {}, "missing": []}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


async def _ask(code: str, *, keys=("setting.manage",), members=3, used=None):
    client = FakeDataClient(role="owner", permission_keys=list(keys))
    client._is_owner = True
    client._plan = plan_payload(code)
    client._members_count = members
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = client._plan
    return await handle_chat_message(client, message="ร้านใช้แพ็กเกจอะไร", ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=_ai(json.dumps(READ_PLAN))))


def test_it_is_registered_the_way_every_capability_is():
    assert C.ACTION_PERMISSIONS[("read", "plan")] == "setting.manage"
    assert C.ENTITY_DASHBOARD_PAGE["plan"][0] == "company"
    prompt = (ROOT / "application/chann_app/services/ai/intent.py").read_text(encoding="utf-8")
    assert 'entity="plan"' in prompt


async def test_starter():
    reply = await _ask("starter", members=3)
    lines = reply.text.split("\n")
    assert lines[0] == "แพ็กเกจของร้าน: Starter"
    assert "ผู้ใช้ 3/5 คน" in reply.text
    assert "ถามรายงานด้วย AI: มีในแพ็กเกจ Pro ขึ้นไป" in reply.text
    assert "ยังไม่มีในแพ็กเกจนี้:" in reply.text
    assert len(lines) <= 6


async def test_enterprise_plus_has_no_cap():
    reply = await _ask("enterprise_plus", members=120)
    assert "ผู้ใช้ 120 คน · ไม่จำกัด" in reply.text
    assert "ยังไม่มีในแพ็กเกจนี้" not in reply.text


async def test_it_needs_setting_manage():
    reply = await _ask("pro", keys=("customer.read",))
    assert "แพ็กเกจของร้าน" not in reply.text
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_plan_read.py -q
```
Expected: FAIL — `KeyError: ('read', 'plan')`.

- [ ] **Step 4: The prompt block**

In `application/chann_app/services/ai/intent.py`, directly after the `entity="api_key"` block (before `- entity="survey"`):

```text
- entity="plan" — the shop's sales plan with Chann (Starter, Pro,
  Enterprise, Enterprise Plus): which plan it is on, what the plan
  includes, how many AI report credits are left this month, how many more
  people can join. Not the shop's details (entity="setting"), not an
  invite code (entity="invite"), not a report.
  action="read". Examples: "ร้านใช้แพ็กเกจอะไร", "แพ็กเกจของร้าน",
    "เหลือเครดิตรายงาน AI เท่าไหร่", "เชิญได้อีกกี่คน", "what plan are we on".
```

- [ ] **Step 5: Register, dispatch, answer (`chat.py`)**

`ACTION_PERMISSIONS`, after the three `api_key` rows:

```python
    # Round 21D — the shop's plan, read (the dashboard's plan card is the
    # twin). setting.manage: the people who can act on it.
    ("read", "plan"): "setting.manage",
```

`ENTITY_DASHBOARD_PAGE`: `"plan": ("company", {"th": "ข้อมูลบริษัท (แพ็กเกจ)", "en": "Company details (plan)"}),`

`_execute_intent`, directly after the `assignment_rule` dispatch:

```python
    if intent.get("entity") == "plan":
        return await _handle_plan_read(
            client, ctx=ctx, license_id=license_id, permission_keys=permission_keys, language=language,
        )
```

Directly above `async def _handle_survey_summary(`:

```python
PLAN_READ_HEAD = {"th": "แพ็กเกจของร้าน: {plan}", "en": "The shop's plan: {plan}"}
PLAN_READ_USERS = {"th": "ผู้ใช้ {n}/{limit} คน", "en": "Users {n}/{limit}"}
PLAN_READ_USERS_UNCAPPED = {"th": "ผู้ใช้ {n} คน · ไม่จำกัด", "en": "Users {n} · no cap"}
PLAN_READ_CREDITS = {"th": "เครดิตรายงาน AI เดือนนี้ {used}/{allowance}", "en": "AI report credits this month {used}/{allowance}"}
PLAN_READ_NO_AI = {"th": "ถามรายงานด้วย AI: มีในแพ็กเกจ {min_plan} ขึ้นไป", "en": "AI reports: from the {min_plan} plan"}
PLAN_READ_LOCKED = {"th": "ยังไม่มีในแพ็กเกจนี้: {features}", "en": "Not in this plan: {features}"}
PLAN_READ_BUTTON = {"th": "ดูแพ็กเกจบนหน้าจอ", "en": "Open the plan"}


async def _handle_plan_read(
    client: DataClient, *, ctx: ResolvedContext, license_id, permission_keys: list[str], language: str,
) -> ChatReply:
    """Round 21D — the plan card in words: the plan, users n/limit, AI
    credits used/allowance, and what the plan does not have. Six lines at
    most. Reads the Data tier (usage moves with every join)."""
    if "setting.manage" not in set(permission_keys):
        return _no_permission(language, "setting.manage")
    plan, usage = await entitlements.plan_for(client, str(license_id))
    n = int(usage.get("members") or 0)
    lines = [_t(PLAN_READ_HEAD, language).format(plan=plan.label)]
    lines.append(
        _t(PLAN_READ_USERS, language).format(n=n, limit=plan.members_limit)
        if plan.members_limit is not None else _t(PLAN_READ_USERS_UNCAPPED, language).format(n=n)
    )
    if plan.has(entitlements.AI_REPORTS):
        lines.append(_t(PLAN_READ_CREDITS, language).format(
            used=int(usage.get("ai_reports_used") or 0), allowance=plan.ai_allowance))
    else:
        lines.append(_t(PLAN_READ_NO_AI, language).format(min_plan=plan.min_label(entitlements.AI_REPORTS)))
    missing = [key for key, _min in plan.locked if key != entitlements.AI_REPORTS]
    if missing:
        lines.append(_t(PLAN_READ_LOCKED, language).format(features=" · ".join(
            f"{entitlements.feature_label(key, language)} ({plan.min_label(key)}+)" for key in missing)))
    url = dashboard_link("company", ctx.oa)
    return ChatReply(text="\n".join(lines),
                     quick_reply_url=(_t(PLAN_READ_BUTTON, language), url) if url else None,
                     intent={"action": "read", "entity": "plan"})
```

`scripts/dev/check-parity.py`, `URL_ENTITIES` — first entry: `("plan", "plan"),  # Round 21D: GET licenses/X/plan is the plan card`. Until Task 12 draws the card, `check-parity.py` lists `plan.read` under "IN CHAT BUT NOT IN THE DASHBOARD" — **expected here, and Task 12 clears it; do not add an `ACCEPTED` entry.**

- [ ] **Step 6: Run the test, the capability measure and the prompt pins**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_plan_read.py tests/unit/test_chat_read_intents.py tests/unit/test_round20i_last_gaps.py -q
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_phase6_chat.py -q -k "prompt or conversational"
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/measure-capabilities.py | grep -E " plan |=== "
```
Expected: pass; `measure-capabilities` lists `sales plan read setting.manage answers` and ends `· 0 with no handler · 0 raising`.

- [ ] **Step 7: Measure AFTER — per sentence, never on totals**

```bash
cd ~/stage-fix/r21d && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py --oa sales --role owner \
  --file /tmp/21d-plan-sentences.txt | tee /tmp/21d-plan-after.txt
diff /tmp/21d-plan-before.txt /tmp/21d-plan-after.txt
```
Required, sentence by sentence: the first five read `{"action": "read", "entity": "plan", …}`; the seven neighbours read **exactly what they read before**. If a neighbour moved, the block's wording is wrong — change the wording (never add a trigger word) and measure again. Keep both files; the twelve before/after readings go into the handoff (Task 15).

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): chat answers which plan the shop is on, its users and its AI credits — the one prompt block this round adds, measured per sentence before and after" && git log --oneline -1
```

---

### Task 11: Background work and customer pushes leave a locked feature alone

Sweeps and pushes have no principal, so they read the plan the tenant row or the plan route carries (spec §5.9): the job-SLA and approval-SLA sweeps skip shops without service; every push to a **customer** is skipped on a shop without the Customer LINE link (the notification row is still written); a new document renders with the system template on a shop without custom documents (already-issued PDFs are frozen and untouched). The chat-SLA sweep is the Data tier's (Task 4). **The Daily Summary has no job section to omit** — `services/reminders.py::sweep_due_follow_ups` sends follow-ups only (verify: `grep -n "ticket" application/chann_app/services/reminders.py` finds none in the digest); nothing changes there, and the handoff says so.

**Files:**
- Modify: `application/chann_app/services/job_sla.py:220-231` (`sweep_jobs` — the tenant filter)
- Modify: `application/chann_app/services/approval_sla.py:48-59` (`sweep_reports` — the same filter)
- Modify: `application/chann_app/services/notify.py:77-160` (`send_notification`)
- Modify: `application/chann_app/services/approval.py:531-580` (`send_survey`)
- Modify: `application/chann_app/services/chat.py:8169-8190` (`_notify_customer`)
- Modify: `application/chann_app/services/documents/selection.py:85-118` (`resolve_tenant_template`)
- Test: `tests/unit/test_round21d_background.py`

**Interfaces:**
- Consumes (Task 3): `plan` on every `platform_tenants()` row; (Task 5) `entitlements.entitled`, `entitlements.plan_for`.
- Produces: `sweep_jobs` / `sweep_reports` never list a no-service shop's work; `send_notification(...)` returns the row with `delivery_line=False` for a customer-OA notification on a shop without the link; `send_survey(...)` returns `"plan_locked"` there; `resolve_tenant_template(...)` returns `(None, None)` on a shop without custom documents.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21d_background.py`:

```python
"""Round 21D — the platform's own work honours the plan (spec §5.9)."""
from __future__ import annotations

import pytest

from chann_app.services import approval, approval_sla, job_sla, notify
from chann_app.services.documents import selection
from plan_fixtures import plan_payload

pytestmark = pytest.mark.asyncio


class _Shops:
    """Three shops: one without service, one with, one from an older Data
    tier that sends no plan (→ Pro, so it keeps being swept)."""

    def __init__(self):
        self.asked: list[str] = []
        self.pushed: list = []
        self.rows: list = []

    async def platform_tenants(self, **_kw):
        return [{"id": "L-starter", "status": "active", "plan": plan_payload("starter")},
                {"id": "L-pro", "status": "active", "plan": plan_payload("pro")},
                {"id": "L-old", "status": "active"}]

    async def list_tickets(self, license_id, *a, **k):
        self.asked.append(license_id)
        return []

    async def list_service_reports(self, license_id, status=None):
        self.asked.append(license_id)
        return []

    async def list_license_settings(self, license_id):
        return []

    async def license_plan(self, license_id):
        return {"plan": plan_payload("starter" if license_id == "L-starter" else "pro"), "usage": {}}

    async def list_document_templates(self, license_id, document_type=None):
        self.asked.append(license_id)
        return []

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kw):
        self.rows.append((license_id, kw))
        return {"id": "n1", **kw}


async def test_the_job_sweep_skips_a_shop_without_service():
    shops = _Shops()
    await job_sla.sweep_jobs(shops)
    assert shops.asked == ["L-pro", "L-old"]


async def test_the_approval_sweep_skips_a_shop_without_service(monkeypatch):
    async def four_hours(_client, _license_id):
        return {"approval": 4}

    monkeypatch.setattr(approval_sla, "sla_settings", four_hours)
    shops = _Shops()
    await approval_sla.sweep_reports(shops)
    assert shops.asked == ["L-pro", "L-old"]


async def test_a_customer_push_on_a_shop_without_the_link_is_recorded_not_pushed(monkeypatch):
    pushed = []

    async def fake_push(*args, **kwargs):
        pushed.append(args)
        return ["m1"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    shops = _Shops()
    await notify.send_notification(shops, license_id="L-starter", target_chann_uid="CHN-C", target_line_user_id="U-c",
                                   type="document_sent", message="ใบเสนอราคา")
    assert pushed == []
    assert shops.rows and shops.rows[0][1]["delivery_line"] is False
    await notify.send_notification(shops, license_id="L-pro", target_chann_uid="CHN-C", target_line_user_id="U-c",
                                   type="document_sent", message="ใบเสนอราคา")
    assert len(pushed) == 1


async def test_a_staff_push_is_not_the_customer_link(monkeypatch):
    pushed = []

    async def fake_push(*args, **kwargs):
        pushed.append(args)
        return ["m1"]

    monkeypatch.setattr(notify, "push_text", fake_push)
    await notify.send_notification(_Shops(), license_id="L-starter", target_chann_uid="CHN-O", target_line_user_id="U-o",
                                   type="followup_due", message="นัดวันนี้")
    assert len(pushed) == 1


async def test_the_survey_is_not_pushed(monkeypatch):
    async def must_not_push(*a, **k):
        raise AssertionError("pushed")

    monkeypatch.setattr(approval, "push_messages", must_not_push)
    out = await approval.send_survey(_Shops(), license_id="L-starter", survey={"id": "s1", "ticket_id": "t1"})
    assert out == "plan_locked"


async def test_a_new_document_uses_the_system_template():
    shops = _Shops()
    assert await selection.resolve_tenant_template(shops, "L-starter", "quote") == (None, None)
    assert "L-starter" not in shops.asked
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_background.py -q
```
Expected: FAIL — `L-starter` is swept, the push goes out, the survey is `"failed"`/pushed, templates are listed.

- [ ] **Step 3: The two SLA sweeps**

In `job_sla.sweep_jobs` and `approval_sla.sweep_reports`, replace the `license_ids = [...]` comprehension with:

```python
        from . import entitlements

        # Round 21D (spec §5.9): a shop whose plan has no service jobs is
        # not swept; a row with no plan (an older Data tier) is Pro.
        license_ids = [
            str(t.get("id") or "") for t in tenants
            if str(t.get("status") or "") != "suspended" and entitlements.entitled(t.get("plan"), "feature.service")
        ]
```

- [ ] **Step 4: Customer pushes**

`notify.send_notification` — directly before `row = await client.create_notification(...)`:

```python
    target_oa = oa or TYPE_TO_OA.get(type, DEFAULT_OA)
    if target_oa == "customer" and delivery_line and license_id:
        # Round 21D (spec §3.4): a shop without the Customer LINE link does
        # not push to customers. The row is still written — it is the record
        # of what the shop did — just not delivered over LINE.
        from . import entitlements

        plan, _usage = await entitlements.plan_for(client, str(license_id))
        if not plan.has("feature.customer_line_link"):
            delivery_line = False
```

`approval.send_survey` — first statements after `license_id = str(license_id)`:

```python
    from . import entitlements

    plan, _usage = await entitlements.plan_for(client, license_id)
    if not plan.has("feature.customer_line_link"):
        # Round 21D: no push to customers on a shop without the link.
        return "plan_locked"
```

`chat._notify_customer` — first statements after `if not uid: return`:

```python
    license_id = str((ticket or {}).get("license_id") or "")
    if license_id:
        plan, _usage = await entitlements.plan_for(client, license_id)
        if not plan.has("feature.customer_line_link"):
            return
```

(check first with `grep -n '"license_id"' application/chann_app/services/chat.py | sed -n 1,5p` that ticket dicts carry `license_id`; `TicketOut` does — `grep -n 'class TicketOut' -A6 data/chann_data/schemas.py`. If a caller passes a ticket without it, add a `license_id` keyword to `_notify_customer` and pass it from its callers instead.)

- [ ] **Step 5: The renderer's template choice**

`selection.resolve_tenant_template` — first statements of the body:

```python
    from .. import entitlements

    plan, _usage = await entitlements.plan_for(client, str(license_id))
    if not plan.has("feature.custom_documents"):
        # Round 21D (spec §3.4): new documents use the system template on a
        # plan without custom documents; issued PDFs keep their frozen
        # data_snapshot and are not touched. The shop's templates stay.
        return None, None
```

- [ ] **Step 6: Run to verify, then everything these files feed**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_background.py tests/unit/test_chat_sla.py tests/unit/test_sla_round19.py tests/unit/test_round20w_sla_warning.py tests/unit/test_round21c_document_send.py tests/unit/test_completion_notice.py -q
```
Expected: all pass. A fake that has no `license_plan` method makes `plan_for` fail open as Pro (it logs and continues) — that is the intended direction, not an error to silence. If a fake without `license_plan` now fills the log in a test that asserts on log output, add `license_plan` to that fake returning `{"plan": None, "usage": {}}`.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): sweeps skip a shop without service, customers are not pushed to on a shop without the LINE link, and new documents use the system template without custom documents" && git log --oneline -1
```

---

### Task 12: Dashboard — locked nav with its reason, `<PlanLocked>`, the plan card, disabled-with-reason lines

**Before writing any TSX: invoke the `ui-ux-pro-max` skill** (owner's standing rule, 20 ก.ย. 2569) and run its searches for this task's four patterns, pasting the findings into the task report:

```bash
python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "locked feature upgrade prompt paywall" --domain ux
python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "disabled button reason helper text" --domain ux
python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "navigation item disabled lock badge" --domain ux
python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "usage meter plan limits card" --domain ux
```
Apply what they say within these rules from the spec: a locked nav entry is **shown disabled with a lock and a visible second line "Pro ขึ้นไป" to people holding `setting.manage` (or the owner) and hidden from everyone else** (§5.4, §8.1); every disabled control has its reason as **visible text under it**, never only a tooltip (§5.4); reason text uses `--ink-soft` (≥ 4.5:1 on white); the OA accent stays `#178a50`.

**Files:**
- Create: `presentation/app/liff/_plan.tsx`
- Modify: `presentation/app/liff/sales/_session.ts` (`SalesSession`, `EMPTY`, `fetchMe`, `setSession`)
- Modify: `presentation/app/liff/_nav-model.tsx` (`NavEntry`, the sales entries, `lockedBy`)
- Modify: `presentation/app/liff/_nav.tsx` (`NavFrame` props and `groups`, `RailLink`)
- Modify: `presentation/app/liff/sales/_components.tsx` (`AppShell` props → `NavFrame`)
- Modify: `presentation/app/liff/sales/_shell.tsx` (the locked page for a locked route)
- Modify: `presentation/app/liff/sales/_format.ts` (`ApiFailure`, `readFailure`, `describeFailure`)
- Modify: pages — `company/CompanyProfile.tsx` (plan card before the subscription section, `:535`), `members/MemberManagement.tsx` (users line under the invites heading, `:555`), `roles/RoleManagement.tsx` (create button, `:278-283`), `approvals/settings/ApprovalSettings.tsx` (reason under the policy box, `:136-170`), `reports/ai/AiReports.tsx` (three cards on a plan without service; the question box), `invoices/InvoiceList.tsx` (`sendBlocked`, `:528`), `quotes/[id]/QuoteDetail.tsx` (note `:617-625`, button `:663`)
- Modify: `presentation/app/globals.css` (four rules, appended)
- Modify: `presentation/lib/i18n/th.ts`, `presentation/lib/i18n/en.ts` (`dashboard.plan`, first key inside `dashboard: {`)
- Test: `tests/unit/test_round21d_ui.py`

**Interfaces:**
- Consumes (Task 5): `GET /api/phase2/licenses/{id}/me/permissions` → `plan`, `plan_locked_keys`, `sales_contact`; `GET /api/phase2/licenses/{id}/plan` → `{plan, usage}`; 403 bodies `{"error": "plan_required", "feature", "plan", "min_plan"}` and `{"error": "member_limit_reached", "limit", "plan"}`.
- Produces (`presentation/app/liff/_plan.tsx`): `type PlanInfo`, `type SalesContact`, `PLAN_LABELS`, `planHas(plan, key): boolean`, `minPlanLabel(plan, key): string`, `<PlanLocked feature plan canUpgrade contact />`, `<UpgradeContact contact />`, `<PlanReason>`, `<PlanCard token licenseId canUpgrade contact />`, `LOCK_ICON`
- Produces (session): `SalesSession.plan: PlanInfo | null`, `SalesSession.salesContact: SalesContact`
- Produces (nav): `NavEntry.feature?: string`, `NavEntry.lockMode?: "page" | "notice"`, `lockedBy(entry, plan): string | null`

- [ ] **Step 1: Write the failing test (the pages are read as text, the house pattern)**

Create `tests/unit/test_round21d_ui.py`:

```python
"""Round 21D — the dashboard draws the plan (spec §5.4, §8). Read as text,
like every UI test in this repo; the pictures are looked at in Task 15."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIFF = ROOT / "presentation/app/liff"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


PLAN = _read("presentation/app/liff/_plan.tsx")
NAV_MODEL = _read("presentation/app/liff/_nav-model.tsx")
NAV = _read("presentation/app/liff/_nav.tsx")
SHELL = _read("presentation/app/liff/sales/_shell.tsx")
SESSION = _read("presentation/app/liff/sales/_session.ts")
FORMAT = _read("presentation/app/liff/sales/_format.ts")
TH = _read("presentation/lib/i18n/th.ts")
EN = _read("presentation/lib/i18n/en.ts")


class TestTheNav:
    def test_each_locked_page_declares_its_feature(self):
        for key, feature in (("chats", "feature.live_chat"), ("tickets", "feature.service"),
                             ("teams", "feature.service"), ("reports", "feature.service"),
                             ("satisfaction", "feature.service"), ("approvals", "feature.service"),
                             ("warranties", "feature.warranty"), ("templates", "feature.custom_documents"),
                             ("apiKeys", "feature.external_api")):
            line = next(l for l in NAV_MODEL.splitlines() if f'key: "{key}"' in l and "/liff/sales/" in l)
            assert f'feature: "{feature}"' in line, key

    def test_ai_reports_and_roles_stay_open(self):
        for key in ("aiReports", "roles"):
            line = next(l for l in NAV_MODEL.splitlines() if f'key: "{key}"' in l and "/liff/sales/" in l)
            assert "feature:" not in line, key

    def test_templates_and_api_keys_stay_readable(self):
        for key in ("templates", "apiKeys"):
            line = next(l for l in NAV_MODEL.splitlines() if f'key: "{key}"' in l and "/liff/sales/" in l)
            assert 'lockMode: "notice"' in line, key

    def test_a_locked_entry_is_shown_to_whoever_can_upgrade_and_hidden_from_the_rest(self):
        assert "export function lockedBy(" in NAV_MODEL
        assert "canUpgrade" in NAV and "lockedBy(" in NAV
        assert "rail-lock-reason" in NAV            # the reason is text, not a title=


class TestTheLockedPage:
    def test_the_shell_renders_it_for_a_locked_route(self):
        assert "<PlanLocked" in SHELL and "lockedBy(" in SHELL

    def test_the_owner_gets_the_contact_and_everyone_else_the_ask_owner_line(self):
        assert "canUpgrade ?" in PLAN and "askOwner" in PLAN
        assert "openExternal(" in PLAN              # LIFF opens links through liff.openWindow

    def test_no_contact_no_button(self):
        assert "contact?.url" in PLAN and "contactNone" in PLAN


class TestTheSession:
    def test_me_carries_the_plan(self):
        assert "plan?: PlanInfo" in SESSION or "plan: PlanInfo | null" in SESSION
        assert "sales_contact" in SESSION


class TestThePages:
    def test_the_plan_card_is_on_the_company_page(self):
        page = _read("presentation/app/liff/sales/company/CompanyProfile.tsx")
        assert "<PlanCard" in page
        assert "/plan`" in PLAN

    def test_ai_reports_on_starter(self):
        page = _read("presentation/app/liff/sales/reports/ai/AiReports.tsx")
        assert 'planHas(session.plan, "quota.ai_reports_per_month")' in page
        assert 'planHas(session.plan, "feature.service")' in page
        assert "aiLocked" in page

    def test_every_disabled_control_has_its_reason(self):
        for rel, reason in (("roles/RoleManagement.tsx", "rolesLocked"),
                            ("approvals/settings/ApprovalSettings.tsx", "approvalLocked"),
                            ("members/MemberManagement.tsx", "inviteAtLimit"),
                            ("invoices/InvoiceList.tsx", "sendLineLocked"),
                            ("quotes/[id]/QuoteDetail.tsx", "sendLineLocked")):
            assert reason in _read(f"presentation/app/liff/sales/{rel}"), rel

    def test_a_plan_refusal_is_translated(self):
        assert '"plan_required"' in FORMAT and '"member_limit_reached"' in FORMAT


class TestTheWords:
    KEYS = ("lockedTitle", "lockedShop", "contactButton", "contactNone", "askOwner", "fromPlan",
            "cardTitle", "users", "usersUnlimited", "aiCredits", "inviteAtLimit", "aiLocked",
            "approvalLocked", "approvalFirstOnly", "rolesLocked", "sendLineLocked", "planRequired",
            "memberLimit")

    def test_both_languages(self):
        for key in self.KEYS:
            assert re.search(rf"\b{key}:", TH), key
            assert re.search(rf"\b{key}:", EN), key

    def test_the_spec_copy(self):
        for text in ("ฟีเจอร์นี้อยู่ในแพ็กเกจ {min_plan} ขึ้นไป",
                     "ร้านของคุณใช้แพ็กเกจ {plan} · ข้อมูลเดิมยังอยู่ครบ ไม่มีอะไรถูกลบ",
                     "ติดต่อทีม Chann เพื่ออัปเกรด", "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย",
                     "ผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว",
                     "ถามรายงานด้วย AI มีในแพ็กเกจ Pro ขึ้นไป · รายงานพื้นฐานด้านบนใช้ได้ฟรีเสมอ",
                     "อนุมัติหลายระดับมีในแพ็กเกจ Enterprise ขึ้นไป",
                     "สร้างบทบาทเองมีในแพ็กเกจ Pro ขึ้นไป · ใช้บทบาทมาตรฐานได้ตามปกติ",
                     "ส่งทางไลน์มีในแพ็กเกจ Pro ขึ้นไป"):
            assert text in TH, text
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_ui.py -q
```
Expected: collection ERROR (`_plan.tsx` does not exist).

- [ ] **Step 3: The words**

In `presentation/lib/i18n/th.ts`, first key inside `dashboard: {`:

```ts
    // Round 21D — the shop's sales plan (spec §8). Plan names are product
    // names and are never translated.
    plan: {
      cardTitle: "แพ็กเกจ {plan}",
      users: "ผู้ใช้ {n}/{limit} คน",
      usersUnlimited: "{n} คน · ไม่จำกัด",
      aiCredits: "เครดิตรายงาน AI เดือนนี้ {used}/{allowance}",
      fromPlan: "{plan} ขึ้นไป",
      lockedTitle: "ฟีเจอร์นี้อยู่ในแพ็กเกจ {min_plan} ขึ้นไป",
      lockedShop: "ร้านของคุณใช้แพ็กเกจ {plan} · ข้อมูลเดิมยังอยู่ครบ ไม่มีอะไรถูกลบ",
      contactButton: "ติดต่อทีม Chann เพื่ออัปเกรด",
      contactNone: "ติดต่อทีม Chann CRM AI ที่ดูแลร้านของคุณ",
      askOwner: "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย",
      inviteAtLimit: "ผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว",
      aiLocked: "ถามรายงานด้วย AI มีในแพ็กเกจ Pro ขึ้นไป · รายงานพื้นฐานด้านบนใช้ได้ฟรีเสมอ",
      approvalLocked: "อนุมัติหลายระดับมีในแพ็กเกจ Enterprise ขึ้นไป",
      approvalFirstOnly: "ใช้เฉพาะขั้นแรกตามแพ็กเกจ {plan}",
      rolesLocked: "สร้างบทบาทเองมีในแพ็กเกจ Pro ขึ้นไป · ใช้บทบาทมาตรฐานได้ตามปกติ",
      sendLineLocked: "ส่งทางไลน์มีในแพ็กเกจ Pro ขึ้นไป",
      planRequired: "«{feature}» อยู่ในแพ็กเกจ {min_plan} ขึ้นไป — ร้านนี้ใช้แพ็กเกจ {plan}",
      memberLimit: "ร้านมีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว",
      included: "มีในแพ็กเกจ",
      notIncluded: "ยังไม่มี",
      features: {
        "feature.customer_line_link": { label: "ผูก LINE ลูกค้ากับร้าน", desc: "ลูกค้าผูก LINE กับร้าน รับเอกสารและสถานะงานทาง LINE" },
        "feature.live_chat": { label: "แชทกับลูกค้า", desc: "แชทกับลูกค้า (Live Chat + รูป) และตั้งค่า SLA การตอบแชท" },
        "feature.service": { label: "งานบริการ / งานซ่อม และทีมช่าง", desc: "LINE ช่าง · รับงาน เช็คอิน ส่งรูปหน้างาน · รายงานการซ่อม PDF + ลายเซ็น · แบบประเมินหลังงานเสร็จ" },
        "feature.warranty": { label: "ทะเบียนเครื่อง / ประกัน", desc: "บันทึก Serial Number รุ่นที่ซื้อ-ระยะประกัน · Import CSV · ตรวจสถานะประกันจากงานซ่อม" },
        "feature.custom_documents": { label: "แบบฟอร์มเอกสารของร้านเอง", desc: "AI ร่างแบบฟอร์ม / อัปโหลด Word Template" },
        "feature.custom_roles": { label: "สร้างและแก้บทบาทเอง", desc: "บทบาทมาตรฐานใช้ได้ทุกแพ็กเกจ · กำหนดบทบาทเองได้" },
        "feature.multi_level_approval": { label: "ขั้นตอนอนุมัติหลายระดับ", desc: "อนุมัติรายงานการซ่อมมากกว่าหนึ่งขั้น" },
        "feature.external_api": { label: "เชื่อมต่อ API ภายนอก", desc: "ให้ระบบบัญชี / ERP อ่านและเขียนข้อมูลร้านผ่าน API" },
        "quota.ai_reports_per_month": { label: "ถามรายงานด้วย AI", desc: "ถามรายงานเป็นประโยค ได้ตาราง/กราฟ" },
      },
    },
```

`presentation/lib/i18n/en.ts`, the same keys (the type is `typeof th`, so a missing key fails `tsc`):

```ts
    plan: {
      cardTitle: "{plan} plan",
      users: "Users {n}/{limit}",
      usersUnlimited: "{n} users · no cap",
      aiCredits: "AI report credits this month {used}/{allowance}",
      fromPlan: "{plan} and up",
      lockedTitle: "This feature is included from the {min_plan} plan",
      lockedShop: "Your shop is on {plan} · nothing already recorded has been deleted",
      contactButton: "Contact Chann to upgrade",
      contactNone: "Contact the Chann CRM AI team that looks after your shop",
      askOwner: "If you need it, let the shop owner know",
      inviteAtLimit: "All {limit} users of the {plan} plan are taken",
      aiLocked: "AI reports are included from the Pro plan · the basic reports above are always free",
      approvalLocked: "Multi-level approval is included from the Enterprise plan",
      approvalFirstOnly: "Only the first step applies on the {plan} plan",
      rolesLocked: "Custom roles are included from the Pro plan · the standard roles work as usual",
      sendLineLocked: "Sending on LINE is included from the Pro plan",
      planRequired: "«{feature}» is included from the {min_plan} plan — this shop is on {plan}",
      memberLimit: "The shop has all {limit} users its {plan} plan allows",
      included: "Included",
      notIncluded: "Not included",
      features: {
        "feature.customer_line_link": { label: "Customer LINE link", desc: "Customers link their LINE to the shop and get documents and job updates there" },
        "feature.live_chat": { label: "Customer chat", desc: "Live chat with customers (with pictures) and the chat reply SLA" },
        "feature.service": { label: "Service jobs and technicians", desc: "Technician LINE · take jobs, check in, site photos · repair report PDF + signature · after-job survey" },
        "feature.warranty": { label: "Warranty register", desc: "Serial numbers, model, warranty period · CSV import · warranty check from a job" },
        "feature.custom_documents": { label: "Custom document templates", desc: "AI-drafted forms / upload a Word template" },
        "feature.custom_roles": { label: "Custom roles", desc: "Standard roles on every plan · make your own roles" },
        "feature.multi_level_approval": { label: "Multi-level approval", desc: "Approve repair reports in more than one step" },
        "feature.external_api": { label: "External API", desc: "Let an accounting system / ERP read and write the shop's data" },
        "quota.ai_reports_per_month": { label: "AI reports", desc: "Ask for a report in a sentence, get a table or chart" },
      },
    },
```

- [ ] **Step 4: `_plan.tsx` — the shared pieces**

Create `presentation/app/liff/_plan.tsx`:

```tsx
"use client";

import { ReactNode, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { openExternal, proxyHeaders } from "./_shared";

/**
 * Round 21D — the shop's sales plan on the dashboard (spec §5.4, §8).
 *
 * The plan comes from /me/permissions (the Application's PlanView), so no
 * page fetches it on its own; only the plan card and the members page ask
 * for usage (GET …/plan). The API refuses a locked feature regardless —
 * this file decides what is DRAWN.
 */
export type PlanInfo = {
  code: string;
  label: string;
  features: string[];
  locked: Record<string, string>;
  limits: { members: number | null; ai_reports_per_month: number };
  known?: boolean;
};

export type SalesContact = { label: string; url: string } | null;

export type PlanUsage = {
  members?: number;
  members_limit?: number | null;
  ai_reports_used?: number;
  ai_reports_allowance?: number;
};

export const PLAN_LABELS: Record<string, string> = {
  starter: "Starter", pro: "Pro", enterprise: "Enterprise", enterprise_plus: "Enterprise Plus",
};

const ORDERED_FEATURES = [
  "feature.customer_line_link", "feature.live_chat", "feature.service", "feature.warranty",
  "feature.custom_documents", "feature.custom_roles", "feature.multi_level_approval",
  "feature.external_api",
] as const;

/** Does the plan have this key? An unanswered /me (no plan) draws
 *  everything — the same direction fetchPermissions fails in; the server
 *  still refuses what the plan locks. */
export function planHas(plan: PlanInfo | null | undefined, key: string): boolean {
  if (!plan) return true;
  if (key === "limit.members") return true;
  if (key === "quota.ai_reports_per_month") return !(key in (plan.locked ?? {}));
  return (plan.features ?? []).includes(key);
}

/** "Pro" — the lowest plan that has the key, as the server computed it. */
export function minPlanLabel(plan: PlanInfo | null | undefined, key: string): string {
  const code = plan?.locked?.[key] ?? "pro";
  return PLAN_LABELS[code] ?? "Pro";
}

export const LOCK_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);

/** A disabled control's reason, as text under it (never only title=). */
export function PlanReason({ children }: { children: ReactNode }) {
  return <p className="plan-reason">{children}</p>;
}

/** The upgrade button — only when CHANN_SALES_CONTACT has a URL (owner
 *  decision Q5); otherwise the sentence that says who to ask. */
export function UpgradeContact({ contact }: { contact: SalesContact }) {
  const { t } = useLanguage();
  if (contact?.url) {
    const url = contact.url;
    return (
      <button type="button" className="btn" data-variant="primary" onClick={() => openExternal(url)}>
        {t.dashboard.plan.contactButton}
      </button>
    );
  }
  return <PlanReason>{t.dashboard.plan.contactNone}</PlanReason>;
}

/** Spec §8.2 — the page a locked feature shows instead of itself (or above
 *  itself, for the read-only pages). */
export function PlanLocked({
  feature, plan, canUpgrade, contact,
}: { feature: string; plan: PlanInfo | null; canUpgrade: boolean; contact: SalesContact }) {
  const { t } = useLanguage();
  const p = t.dashboard.plan;
  const f = (p.features as Record<string, { label: string; desc: string }>)[feature];
  return (
    <section className="card plan-locked" role="status">
      <span className="plan-locked-icon">{LOCK_ICON}</span>
      <h2>{p.lockedTitle.replace("{min_plan}", minPlanLabel(plan, feature))}</h2>
      <p>{f ? `${f.label}: ${f.desc}` : feature}</p>
      <p className="card-meta">{p.lockedShop.replace("{plan}", plan?.label ?? "")}</p>
      {canUpgrade ? <UpgradeContact contact={contact} /> : <PlanReason>{p.askOwner}</PlanReason>}
    </section>
  );
}

/** Spec §8.4 — the plan card on the company page (setting.manage). */
export function PlanCard({
  token, licenseId, canUpgrade, contact,
}: { token: string; licenseId: string; canUpgrade: boolean; contact: SalesContact }) {
  const { t } = useLanguage();
  const p = t.dashboard.plan;
  const [plan, setPlan] = useState<PlanInfo | null>(null);
  const [usage, setUsage] = useState<PlanUsage>({});
  useEffect(() => {
    if (!token || !licenseId) return;
    let live = true;
    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/plan`, {
          headers: proxyHeaders(token, licenseId),
        });
        if (!response.ok) return;
        const body = (await response.json()) as { plan: PlanInfo; usage: PlanUsage };
        if (live) {
          setPlan(body.plan);
          setUsage(body.usage ?? {});
        }
      } catch {
        // The card is information; a failed read leaves the page usable.
      }
    })();
    return () => {
      live = false;
    };
  }, [token, licenseId]);
  if (!plan) return null;
  const n = usage.members ?? 0;
  const users = plan.limits.members == null
    ? p.usersUnlimited.replace("{n}", String(n))
    : p.users.replace("{n}", String(n)).replace("{limit}", String(plan.limits.members));
  const features = p.features as Record<string, { label: string; desc: string }>;
  return (
    <section className="section plan-card" style={{ marginBottom: 16 }}>
      <div className="section-head">
        <h2>{p.cardTitle.replace("{plan}", plan.label)}</h2>
      </div>
      <p className="card-meta">{users}</p>
      <p className="card-meta">
        {planHas(plan, "quota.ai_reports_per_month")
          ? p.aiCredits.replace("{used}", String(usage.ai_reports_used ?? 0))
              .replace("{allowance}", String(plan.limits.ai_reports_per_month))
          : p.aiLocked}
      </p>
      <ul className="plan-features">
        {ORDERED_FEATURES.map((key) => {
          const on = planHas(plan, key);
          return (
            <li key={key} data-on={on ? "true" : "false"}>
              <span aria-hidden="true">{on ? "✓" : LOCK_ICON}</span>
              <span>{features[key]?.label ?? key}</span>
              {!on && <span className="plan-reason">{p.fromPlan.replace("{plan}", minPlanLabel(plan, key))}</span>}
              <span className="sr-only">{on ? p.included : p.notIncluded}</span>
            </li>
          );
        })}
      </ul>
      {canUpgrade && <UpgradeContact contact={contact} />}
    </section>
  );
}
```

(`openExternal` and `proxyHeaders` are exported from `_shared.ts` — `grep -n 'export function openExternal\|export function proxyHeaders' presentation/app/liff/_shared.ts`.)

- [ ] **Step 5: The session carries the plan**

`presentation/app/liff/sales/_session.ts`: import `import type { PlanInfo, SalesContact } from "../_plan";`; add to `SalesSession`:

```ts
  /** Round 21D: the shop's plan (null until /me answers — then nothing is
   *  drawn as locked; the server refuses anyway). */
  plan: PlanInfo | null;
  /** The upgrade contact, for whoever can act on it (null otherwise). */
  salesContact: SalesContact;
```

`EMPTY` gains `plan: null, salesContact: null,`. `fetchMe` returns `plan` and `salesContact` read from `body.plan ?? null` and `body.sales_contact ?? null` (both in the typed `body` and in the two fallback returns as `null`), and `setSession({...})` passes `plan: me.plan, salesContact: me.salesContact`.

- [ ] **Step 6: The nav**

`_nav-model.tsx` — `NavEntry` gains:

```ts
  /** Round 21D: the plan feature this page is (spec §5.4). A locked entry
   *  is drawn disabled with its reason for whoever can upgrade, and hidden
   *  from everyone else. */
  feature?: string;
  /** "page" (default): the locked page replaces it. "notice": the page
   *  stays readable under the lock panel (templates, API keys — the data
   *  is the shop's and nothing is deleted, spec §3.4). */
  lockMode?: "page" | "notice";
```

In the sales groups add `feature: "…"` to exactly these entries: `chats` → `"feature.live_chat"`; `tickets`, `teams`, `reports`, `satisfaction`, `approvals` → `"feature.service"`; `warranties` → `"feature.warranty"`; `templates` → `"feature.custom_documents", lockMode: "notice"`; `apiKeys` → `"feature.external_api", lockMode: "notice"`. `aiReports` and `roles` get none (the page stays; only controls inside lock). And append:

```ts
/** The feature that locks this entry on this plan, or null. */
export function lockedBy(entry: NavEntry, plan: PlanInfo | null | undefined): string | null {
  if (!entry.feature) return null;
  return planHas(plan, entry.feature) ? null : entry.feature;
}
```
(with `import { planHas, type PlanInfo } from "./_plan";`).

`_nav.tsx` — `NavFrame` gains props `plan?: PlanInfo | null; canUpgrade?: boolean;`; `groups` becomes:

```tsx
  const groups = useMemo(() => {
    const perms = permissions ?? new Set<string>();
    return navGroups(t, audience)
      .map((group) => ({
        ...group,
        // Round 21D: a locked entry is drawn for whoever can act on it,
        // with its reason; hidden from everyone else (spec §5.4).
        entries: group.entries.filter(
          (entry) => mayOpen(entry, perms, isOwner) && (!lockedBy(entry, plan) || canUpgrade),
        ),
      }))
      .filter((group) => group.entries.length > 0);
  }, [t, audience, permissions, isOwner, plan, canUpgrade]);
```

and `RailLink` takes `lockedLabel?: string` (passed as `lockedBy(entry, plan) ? t.dashboard.plan.fromPlan.replace("{plan}", minPlanLabel(plan, entry.feature!)) : undefined`) and renders, inside the `Link`:

```tsx
      <span className="rail-icon">{lockedLabel ? LOCK_ICON : entry.icon}</span>
      <span className="rail-label">
        {entry.label}
        {lockedLabel && <span className="rail-lock-reason">{lockedLabel}</span>}
      </span>
```

with `data-locked={lockedLabel ? "true" : undefined}` on the `Link` and `aria-label={lockedLabel ? `${entry.label} — ${lockedLabel}` : entry.label}`. Tapping it still navigates — to the locked page (§8.1: "Tap → the locked page, not nothing").

`_components.tsx` — `AppShell` gains props `plan?: PlanInfo | null; canUpgrade?: boolean;` and passes them to `NavFrame`.

- [ ] **Step 7: The shell draws the locked page**

`_shell.tsx`:

```tsx
import { usePathname } from "next/navigation";

import { currentKey, lockedBy, navGroups } from "../_nav-model";
import { PlanLocked } from "../_plan";
```

inside `SalesShell`, before `return`:

```tsx
  const pathname = usePathname() ?? "";
  const entries = navGroups(t, "sales").flatMap((group) => group.entries);
  const here = entries.find((entry) => entry.key === currentKey(entries, pathname));
  const locked = here ? lockedBy(here, session.plan) : null;
  const canUpgrade = session.isOwner || session.permissions.has("setting.manage");
  const lockPanel = locked ? (
    <PlanLocked feature={locked} plan={session.plan} canUpgrade={canUpgrade} contact={session.salesContact} />
  ) : null;
```

pass `plan={session.plan}` and `canUpgrade={canUpgrade}` to `AppShell`, and replace `{children}` with:

```tsx
      {/* Round 21D: a page opened directly (bookmark, deep link) that the
          plan locks shows the locked panel instead of itself — or above
          itself for a read-only page (spec §5.4, §3.4). */}
      {lockPanel && here?.lockMode !== "notice" ? lockPanel : <>{lockPanel}{children}</>}
```

- [ ] **Step 8: The pages**

`company/CompanyProfile.tsx` — before `<section className="section" style={{ marginBottom: 16 }}>` of the subscription block:

```tsx
      {/* Round 21D — the plan card (spec §8.4); setting.manage opens this page. */}
      <PlanCard token={session.token} licenseId={session.licenseId}
                canUpgrade={session.isOwner || session.permissions.has("setting.manage")}
                contact={session.salesContact} />
```

`members/MemberManagement.tsx` — fetch `GET /api/phase2/licenses/${licenseId}/plan` alongside the invites (same effect, same headers) into `const [planUsage, setPlanUsage] = useState<{members?: number} | null>(null)`, and directly under `<p className="card-meta">{m.invitesIntro}</p>`:

```tsx
              {session.plan && session.plan.limits.members != null && (planUsage?.members ?? 0) >= session.plan.limits.members && (
                <PlanReason>
                  {t.dashboard.plan.inviteAtLimit
                    .replace("{limit}", String(session.plan.limits.members))
                    .replace("{plan}", session.plan.label)}
                </PlanReason>
              )}
```

`roles/RoleManagement.tsx` — the create button:

```tsx
        {canManageRoles && (
          <div>
            <button type="button" className="btn" data-variant="primary" onClick={openCreate}
                    disabled={busy || !planHas(session.plan, "feature.custom_roles")}>
              {t.role.createCustomRole}
            </button>
            {!planHas(session.plan, "feature.custom_roles") && <PlanReason>{t.dashboard.plan.rolesLocked}</PlanReason>}
          </div>
        )}
```

`approvals/settings/ApprovalSettings.tsx` — under the `flow-summary` `<pre>`:

```tsx
          {!planHas(session.plan, "feature.multi_level_approval") && (
            <PlanReason>
              {(workflow?.rules_json?.steps?.length ?? 0) > 1
                ? t.dashboard.plan.approvalFirstOnly.replace("{plan}", session.plan?.label ?? "")
                : t.dashboard.plan.approvalLocked}
            </PlanReason>
          )}
```

`reports/ai/AiReports.tsx`:
- in `load`, first line: `if (SERVICE_KEYS.includes(key) && !planHas(session.plan, "feature.service")) return;` with `const SERVICE_KEYS: readonly string[] = ["open_jobs_by_tech", "satisfaction_avg"];` beside `BASIC_KEYS` — keep `Promise.allSettled(BASIC_KEYS.map((key) => load(key)))` exactly (pinned by `test_round21c_reports_ui.py`);
- where the cards render, map over `BASIC_KEYS.filter((key) => !SERVICE_KEYS.includes(key) || planHas(session.plan, "feature.service"))` (owner decision Q4: Starter sees three);
- the question box: `const aiOn = planHas(session.plan, "quota.ai_reports_per_month");` → the `<textarea>` and the submit `<button>` get `|| !aiOn` in `disabled`, the example chips are not rendered when `!aiOn`, and directly under the `<textarea>`'s `<label>`: `{!aiOn && <PlanReason>{t.dashboard.plan.aiLocked}</PlanReason>}`.

`invoices/InvoiceList.tsx` — `sendBlocked` first line: `if (!planHas(session.plan, "feature.customer_line_link")) return t.dashboard.plan.sendLineLocked;` (the reason line under the button already renders `sendBlocked(open)`).

`quotes/[id]/QuoteDetail.tsx` — `const lineOn = planHas(session.plan, "feature.customer_line_link");`; the `note` chain gets a first branch `canUpdate && !lineOn ? t.dashboard.plan.sendLineLocked :`, and the send button's `disabled` gains `|| !lineOn`.

Each page imports what it uses from `"../_plan"` / `"../../_plan"` / `"../../../_plan"` (the file is `presentation/app/liff/_plan.tsx`).

- [ ] **Step 9: A plan refusal in words (`_format.ts`)**

`ApiFailure` gains `feature: string; minPlan: string; plan: string; limit: number | null;`, filled in `readFailure` from `body.feature`, `body.min_plan`, `body.plan`, `body.limit` (and `""`/`null` in the string-detail branch). `describeFailure` gets, before the `failure.code && reasons[failure.code]` line:

```ts
  if (failure.code === "plan_required") {
    const p = t.dashboard.plan;
    const label = (p.features as Record<string, { label: string }>)[failure.feature]?.label ?? failure.feature;
    return p.planRequired.replace("{feature}", label)
      .replace("{min_plan}", PLAN_LABELS[failure.minPlan] ?? failure.minPlan)
      .replace("{plan}", PLAN_LABELS[failure.plan] ?? failure.plan);
  }
  if (failure.code === "member_limit_reached") {
    return t.dashboard.plan.memberLimit.replace("{limit}", String(failure.limit ?? ""))
      .replace("{plan}", PLAN_LABELS[failure.plan] ?? failure.plan);
  }
```

- [ ] **Step 10: The styles**

Append to `presentation/app/globals.css`:

```css
/* Round 21D — the plan. A reason is text a phone can read (never only a
   tooltip); --ink-soft keeps it at ≥ 4.5:1 on white. */
.plan-reason { margin: 6px 0 0; font-size: 0.85rem; color: var(--ink-soft); }
.rail-link[data-locked] .rail-label { display: flex; flex-direction: column; line-height: 1.2; }
.rail-lock-reason { font-size: 0.75rem; color: var(--ink-soft); }
.plan-locked { display: grid; gap: 8px; justify-items: start; padding: 20px; }
.plan-locked-icon svg { width: 28px; height: 28px; color: var(--accent); }
.plan-features { list-style: none; margin: 8px 0 12px; padding: 0; display: grid; gap: 6px; }
.plan-features li { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.plan-features li svg { width: 16px; height: 16px; color: var(--ink-soft); }
```

- [ ] **Step 11: Run the UI test, the boundary checks, typecheck and build**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_ui.py tests/unit/test_round21c_reports_ui.py tests/unit/test_round21c_invoice_ui.py tests/boundary -q
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/check-routes.py | tail -2 && /tmp/dv/bin/python scripts/dev/check-i18n-usage.py | tail -2
cd ~/stage-fix/r21d/presentation && npm run typecheck && rm -rf .next && NEXT_TELEMETRY_DISABLED=1 npm run build > /tmp/21d-next-build.log 2>&1; tail -3 /tmp/21d-next-build.log; rm -rf .next
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/check-parity.py | tail -3
```
Expected: tests pass; `check-routes` ends `api_principal refuses on plan`; typecheck and build clean; **`check-parity` now ends `every capability is reachable from both surfaces`** (the plan card's `GET …/plan` is the dashboard's `plan.read`). Remove `.next` afterwards (Cloud Shell disk).

- [ ] **Step 12: Check placement against the ui-ux findings**

The LIFF pages need a LINE login, so they cannot be screenshotted from Cloud Shell; the pictures are drawn and **looked at** in Task 15 (the `sales-plan` guide scene) and on DEV by the owner (checklist X-3…X-6). Here, walk the diff once against the ui-ux-pro-max findings and write in the report, per control, where its reason line sits: the rail's lock line (expanded rail: under the label; collapsed rail: only the lock glyph, the label and reason in `aria-label`), the roles create button, the approval box, the members invites heading, the AI question box, the invoice and quote send buttons. Any control whose reason is only in `title=` is a defect — fix it.

- [ ] **Step 13: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the dashboard shows the plan — locked entries with their reason for whoever can upgrade, a locked page, the plan card, and a visible reason under every control the plan disables" && git log --oneline -1
```

---

### Task 13: The admin console — set the plan, see usage against limits, the top-up, and the downgrade that refuses

**Invoke the `ui-ux-pro-max` skill first** (owner rule): `python3 ~/.claude/skills/ui-ux-pro-max/scripts/search.py "confirm dialog destructive change summary" --domain ux` and `… "select with blocked option explanation" --domain ux`; paste the findings in the report. The console is the operator's own Thai tool (`lib/admin-copy.ts`).

**Files:**
- Modify: `application/chann_app/services/entitlements.py` (the owner's plan-change notice)
- Modify: `application/chann_app/routers_admin.py` — `platform_tenants` (`:835`), `platform_tenant` (`:847-872`), `platform_tenant_update` (`:900-970`)
- Modify: `presentation/app/admin/_types.ts` (`TenantSummary`, `TenantDetail`, `TenantEditFields`)
- Modify: `presentation/app/admin/page.tsx` (a `แพ็กเกจ` column and filter)
- Modify: `presentation/app/admin/tenants/[id]/TenantEdit.tsx` (plan select, the preview in the confirm, the refusal, usage lines, the relabelled top-up)
- Modify: `presentation/lib/admin-copy.ts` (`tenants`, `tenant.edit`)
- Test: `tests/unit/test_round21d_admin.py`

**Interfaces:**
- Consumes (Task 3): `PATCH /internal/v1/platform/tenants/{id}` with `plan_code` (409 `plan_member_limit`, 422 `unknown_plan`), `GET …/plan-preview`, `?plan=`, `plan`/`plan_code`/`usage` on the tenant rows; (Task 5) `DataClient.platform_plan_preview`, `platform_tenants(plan=)`, `entitlements.PLAN_ORDER`, `feature_label`, `is_plan_refusal`.
- Produces: `GET /api/v1/platform/tenants?plan=`; `GET /api/v1/platform/tenants/{id}` adds `plan_preview`; `PATCH /api/v1/platform/tenants/{id}` accepts `plan_code` and answers the Data tier's refusal body **unchanged** (409 with `message` "ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น <plan>"); an empty `ai_chart_quota` clears the override; `entitlements.plan_change_text(old, new, company, language) -> str`; notification `plan_changed` to the owner on the Sales OA.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21d_admin.py`:

```python
"""Round 21D — the platform admin sets the plan (spec §9; owner decision
Q2, 24 ก.ย. 2569: a downgrade over the limit is refused, in words)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chann_app import routers_admin
from chann_app.data_client import DataTierError
from chann_app.services import entitlements
from plan_fixtures import plan_payload

ROOT = Path(__file__).resolve().parents[2]
REFUSAL = {"error": "plan_member_limit", "members": 9, "limit": 5, "inactivate": 4, "plan": "starter",
           "plan_label": "Starter", "message": "ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter"}


class _Admin:
    def __init__(self, *, refuse=None):
        self.recorded: list = []
        self._refuse = refuse
        self.code = "pro"

    async def platform_tenant(self, license_id):
        return {"id": license_id, "company_name": "ร้านแอร์เย็น", "owner_chann_uid": "CHN-OWNER",
                "plan_code": self.code, "plan": plan_payload(self.code), "usage": {"members": 9}}

    async def platform_tenants(self, *, q=None, status=None, plan=None):
        self.recorded.append(("platform_tenants", plan))
        return []

    async def platform_plan_preview(self, license_id):
        return {"current": "pro", "previews": {}}

    async def list_license_settings(self, license_id):
        return []

    async def update_tenant(self, license_id, changes, actor_id=None):
        self.recorded.append(("update_tenant", changes))
        if self._refuse:
            raise DataTierError(self._refuse[0], str(self._refuse[1]), self._refuse[1])
        self.code = changes.get("plan_code", self.code)
        return await self.platform_tenant(license_id)

    async def put_license_setting(self, license_id, key, value, actor_id=None):
        self.recorded.append(("put_license_setting", key, value))

    async def delete_license_setting(self, license_id, key, actor_id=None):
        self.recorded.append(("delete_license_setting", key))

    async def line_target_of(self, chann_uid):
        return "U-owner"

    async def get_display_preferences(self, chann_uid):
        return {}

    async def create_notification(self, license_id, **kw):
        self.recorded.append(("create_notification", kw["target_chann_uid"], kw["type"], kw["message"]))
        return {"id": "n1"}


def _http(client) -> TestClient:
    async def override_client():
        yield client

    async def override_admin():
        return {"sub": "00000000-0000-0000-0000-00000000000a", "username": "root"}

    app = FastAPI()
    app.include_router(routers_admin.router)
    app.dependency_overrides[routers_admin.get_data_client] = override_client
    app.dependency_overrides[routers_admin.require_admin] = override_admin
    return TestClient(app)


class TestTheRoutes:
    def test_setting_the_plan_tells_the_owner(self, monkeypatch):
        pushed = []

        async def fake_push(*a, **k):
            pushed.append(a)
            return ["m"]

        monkeypatch.setattr("chann_app.services.notify.push_text", fake_push)
        client = _Admin()
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"plan_code": "enterprise"})
        assert out.status_code == 200, out.text
        assert ("update_tenant", {"plan_code": "enterprise"}) in client.recorded
        told = [r for r in client.recorded if r[0] == "create_notification"]
        assert told and told[0][1] == "CHN-OWNER" and told[0][2] == "plan_changed"
        assert told[0][3].startswith("แพ็กเกจของ ร้านแอร์เย็น เปลี่ยนเป็น Enterprise แล้ว")
        assert "เชื่อมต่อ API ภายนอก" in told[0][3]

    def test_a_downgrade_over_the_limit_comes_back_in_words(self):
        out = _http(_Admin(refuse=(409, REFUSAL))).patch("/api/v1/platform/tenants/L1", json={"plan_code": "starter"})
        assert out.status_code == 409
        assert out.json()["detail"] == REFUSAL

    def test_an_unknown_plan(self):
        out = _http(_Admin(refuse=(422, {"error": "unknown_plan", "message": "unknown plan 'gold'"}))).patch(
            "/api/v1/platform/tenants/L1", json={"plan_code": "gold"})
        assert out.status_code == 422 and out.json()["detail"]["error"] == "unknown_plan"

    def test_an_empty_top_up_clears_the_override(self):
        client = _Admin()
        out = _http(client).patch("/api/v1/platform/tenants/L1", json={"ai_chart_quota": ""})
        assert out.status_code == 200, out.text
        assert ("delete_license_setting", "ai_chart_quota") in client.recorded

    def test_the_list_filters_by_plan(self):
        client = _Admin()
        assert _http(client).get("/api/v1/platform/tenants?plan=starter").status_code == 200
        assert ("platform_tenants", "starter") in client.recorded
        assert _http(client).get("/api/v1/platform/tenants?plan=gold").status_code == 422

    def test_the_tenant_page_carries_the_preview(self):
        out = _http(_Admin()).get("/api/v1/platform/tenants/L1").json()
        assert out["plan_preview"] == {"current": "pro", "previews": {}}


class TestTheNotice:
    def test_gained_and_locked_are_named(self):
        text = entitlements.plan_change_text(plan_payload("pro"), plan_payload("starter"), "ร้านแอร์เย็น", "th")
        lines = text.split("\n")
        assert lines[0] == "แพ็กเกจของ ร้านแอร์เย็น เปลี่ยนเป็น Starter แล้ว"
        assert lines[1].startswith("ล็อกไว้ (ข้อมูลเดิมยังอยู่ครบ): ")
        assert len(lines) <= 3


class TestTheConsole:
    EDIT = (ROOT / "presentation/app/admin/tenants/[id]/TenantEdit.tsx").read_text(encoding="utf-8")
    LIST = (ROOT / "presentation/app/admin/page.tsx").read_text(encoding="utf-8")
    COPY = (ROOT / "presentation/lib/admin-copy.ts").read_text(encoding="utf-8")

    def test_the_plan_select_and_its_preview(self):
        assert "plan_code" in self.EDIT and "plan_preview" in self.EDIT
        for code in ("starter", "pro", "enterprise", "enterprise_plus"):
            assert code in self.EDIT or code in self.COPY

    def test_a_refused_downgrade_offers_no_confirm(self):
        assert "refused" in self.EDIT and "planRefused" in self.EDIT

    def test_usage_against_limits_and_the_relabelled_top_up(self):
        assert "usage" in self.EDIT
        assert "เพิ่มโควต้ารายงาน AI" in self.COPY

    def test_the_list_has_a_plan_column_and_filter(self):
        assert "plan_code" in self.LIST and 'name="plan"' in self.LIST
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_admin.py -q
```
Expected: FAIL (no `plan_code` handling, `AttributeError: plan_change_text`, the console has no plan).

- [ ] **Step 3: The owner's notice (`entitlements.py`)**

```python
PLAN_CHANGED = {"th": "แพ็กเกจของ {company} เปลี่ยนเป็น {plan} แล้ว", "en": "{company} is now on the {plan} plan"}
PLAN_CHANGED_GAINED = {"th": "เปิดใช้: {features}", "en": "Now included: {features}"}
PLAN_CHANGED_LOCKED = {"th": "ล็อกไว้ (ข้อมูลเดิมยังอยู่ครบ): {features}",
                       "en": "Locked (everything already recorded is kept): {features}"}


def plan_change_text(old: dict | None, new: dict | None, company: str, language: str = "th") -> str:
    """Spec §3.4 — what the owner is told when the admin changes the plan:
    the new plan, then one line for what it gained and one for what it
    locked. Three lines at most."""
    lang = "en" if language == "en" else "th"
    before, after = PlanView.from_payload(old), PlanView.from_payload(new)
    keys = [k for k in FEATURE_LABELS if k != MEMBERS]
    gained = [k for k in keys if after.has(k) and not before.has(k)]
    locked = [k for k in keys if before.has(k) and not after.has(k)]
    lines = [PLAN_CHANGED[lang].format(company=company, plan=after.label)]
    if gained:
        lines.append(PLAN_CHANGED_GAINED[lang].format(features=" · ".join(feature_label(k, lang) for k in gained)))
    if locked:
        lines.append(PLAN_CHANGED_LOCKED[lang].format(features=" · ".join(feature_label(k, lang) for k in locked)))
    return "\n".join(lines)
```

- [ ] **Step 4: The admin routes (`routers_admin.py`)**

Import `from .services import entitlements`.

`platform_tenants` gains `plan: str | None = None`:

```python
    if plan and plan not in entitlements.PLAN_ORDER:
        raise HTTPException(status_code=422, detail={"error": "unknown_plan"})
    return await client.platform_tenants(q=q, status=status_filter, plan=plan)
```

`platform_tenant` — after the `ai_chart_quota` merge, before `return row`:

```python
    # Round 21D — what each other plan would lock, for the confirm dialog.
    try:
        row["plan_preview"] = await client.platform_plan_preview(license_id)
    except Exception:  # noqa: BLE001 — the page still opens without it
        log.exception("could not read the plan preview of %s", license_id)
        row["plan_preview"] = None
```

`platform_tenant_update`:
- after the `TENANT_TEXT_FIELDS` loop: `if "plan_code" in body: changes["plan_code"] = str(body.get("plan_code") or "").strip()`, and read the tenant **before** the write when the plan changes: `before = await client.platform_tenant(license_id) if "plan_code" in changes else None`
- the `ai_chart_quota` block: an empty value clears the override —

```python
    if "ai_chart_quota" in body:
        raw = body.get("ai_chart_quota")
        try:
            if raw is None or str(raw).strip() == "":
                # Round 21D: empty = the plan's own allowance.
                await client.delete_license_setting(license_id, "ai_chart_quota", actor_id=actor)
            else:
                try:
                    quota = max(0, int(str(raw).strip()))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail={"error": "ai_chart_quota_must_be_a_number"}) from None
                await client.put_license_setting(license_id, "ai_chart_quota", quota, actor_id=actor)
            quota_written = True
        except DataTierError as exc:
            code = exc.status_code if 400 <= exc.status_code < 500 else 502
            raise HTTPException(status_code=code, detail={"error": "tenant_update_failed", "reason": exc.detail}) from exc
```

- the final `except DataTierError as exc:` passes a plan refusal through **unchanged** (the console shows its `message`):

```python
    except DataTierError as exc:
        body_ = exc.structured or {}
        if entitlements.is_plan_refusal(exc) or body_.get("error") == "unknown_plan":
            raise HTTPException(status_code=exc.status_code, detail=body_) from exc
        code = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=code, detail={"error": "tenant_update_failed", "reason": exc.detail}) from exc
```

- after a successful write, when the plan changed, tell the owner (best effort):

```python
    if before is not None and str(before.get("plan_code")) != str(saved.get("plan_code")):
        await _tell_owner_plan_changed(client, license_id, before, saved)
    return saved
```

(restructure the `try` so each of its three outcomes assigns `saved` instead of returning — the quota-only re-read `saved = await client.platform_tenant(license_id)`, `saved = await client.set_license_status(...)`, `saved = await client.update_tenant(...)` — and keep the `nothing_to_update` 422 as it is), with the helper below the route:

```python
async def _tell_owner_plan_changed(client: DataClient, license_id: str, before: dict, after: dict) -> None:
    """Spec §3.4: the shop's owner hears it on the Sales OA — the new plan,
    what it gained, what it locked. Best effort: the admin's save stands."""
    from .services.notify import send_notification

    owner = str(after.get("owner_chann_uid") or before.get("owner_chann_uid") or "")
    if not owner:
        return
    company = str(after.get("company_name") or "")
    try:
        await send_notification(
            client, license_id=license_id, target_chann_uid=owner,
            target_line_user_id=await client.line_target_of(owner), type="plan_changed",
            message=entitlements.plan_change_text(before.get("plan"), after.get("plan"), company, "th"),
            message_en=entitlements.plan_change_text(before.get("plan"), after.get("plan"), company, "en"),
            entity_type="license", entity_id=license_id, oa="sales",
        )
    except Exception:  # noqa: BLE001
        log.exception("could not tell the owner of %s about the plan change", license_id)
```

- [ ] **Step 5: The console**

`presentation/lib/admin-copy.ts`:
- `tenants`: `planLabel: "แพ็กเกจ"`, `anyPlan: "ทุกแพ็กเกจ"`, `columns.plan: "แพ็กเกจ"`
- `tenant.edit`: 
```ts
      plan: "แพ็กเกจ",
      planHint: "มีผลทันที ไม่มีข้อมูลถูกลบ — ฟีเจอร์ที่ไม่อยู่ในแพ็กเกจจะถูกล็อก และกลับมาเมื่อเปลี่ยนกลับ",
      planNames: { starter: "Starter", pro: "Pro", enterprise: "Enterprise", enterprise_plus: "Enterprise Plus" } as Record<string, string>,
      planConfirm: (plan: string) => `เปลี่ยนแพ็กเกจเป็น ${plan}`,
      planLocks: "จะล็อก:",
      planKeeps: "ไม่มีข้อมูลถูกลบ · เปลี่ยนกลับเมื่อไรก็ได้ ข้อมูลเดิมกลับมาครบ",
      planRefused: (n: number, plan: string) => `ต้องปิดใช้งาน (inactivate) สมาชิก ${n} คนก่อนลดแพ็กเกจเป็น ${plan}`,
      planRefusedWhere: "ปิดใช้งานได้ที่การ์ดสมาชิกด้านล่าง — ปุ่ม \"ถอดออก\"",
      usageUsers: (n: number, limit: number | null) => (limit == null ? `ผู้ใช้ ${n} คน · ไม่จำกัด` : `ผู้ใช้ ${n}/${limit} คน`),
      usageAi: (used: number, allowance: number) => `เครดิตรายงาน AI เดือนนี้ ${used}/${allowance}`,
      featureNames: {
        "feature.customer_line_link": "ผูก LINE ลูกค้ากับร้าน", "feature.live_chat": "แชทกับลูกค้า",
        "feature.service": "งานบริการ / งานซ่อม และทีมช่าง", "feature.warranty": "ทะเบียนเครื่อง / ประกัน",
        "feature.custom_documents": "แบบฟอร์มเอกสารของร้านเอง", "feature.custom_roles": "สร้างและแก้บทบาทเอง",
        "feature.multi_level_approval": "ขั้นตอนอนุมัติหลายระดับ", "feature.external_api": "เชื่อมต่อ API ภายนอก",
      } as Record<string, string>,
      countWords: {
        open_tickets: "ใบงานเปิดอยู่", technicians: "ช่าง", warranties: "ทะเบียนเครื่อง", open_chats: "แชทเปิดอยู่",
        linked_customers: "ลูกค้าผูก LINE", templates: "แบบฟอร์ม", custom_roles: "บทบาทที่สร้างเอง",
        steps: "ขั้นอนุมัติ", live_keys: "key ใช้งานอยู่",
      } as Record<string, string>,
```
  and replace the three AI-quota strings:
```ts
      aiChartQuota: "เพิ่มโควต้ารายงาน AI",
      aiChartQuotaHint: "เว้นว่าง = ตามแพ็กเกจ (Pro 30 · Enterprise 100 · Enterprise Plus 100 ครั้งต่อเดือน) · ตัวเลข = โควต้าต่อเดือนของร้านนี้ · ใช้ได้กับ Pro ขึ้นไป · 0 = ปิดการถามรายงานด้วย AI",
      aiChartDefault: "ตามแพ็กเกจ",
      aiChartStarter: "Starter ไม่มีการถามรายงานด้วย AI — เปลี่ยนแพ็กเกจก่อน",
```

`presentation/app/admin/_types.ts`: `TenantSummary` gains `plan_code?: string; plan?: { code: string; label: string; limits: { members: number | null; ai_reports_per_month: number } } | null;`; `TenantDetail` gains
```ts
  usage?: { members: number; members_limit: number | null; ai_reports_used: number; ai_reports_allowance: number } | null;
  plan_preview?: { current: string; previews: Record<string, {
    plan: string; label: string; locks: { feature: string; counts: Record<string, number> }[];
    members: number; limit: number | null; refused: boolean; inactivate: number;
  }> } | null;
```
and `TenantEditFields` gains `plan_code: string;`.

`presentation/app/admin/page.tsx`: `const PLANS = ["", "starter", "pro", "enterprise", "enterprise_plus"] as const;`, read `plan` from `searchParams` (`{ q?: string; status?: string; plan?: string }`), `if (plan) params.set("plan", plan);`, a second `<label className="pa-field">{copy.planLabel}<select name="plan" defaultValue={plan}>…</select></label>` beside the status filter (option text `ADMIN.tenant.edit.planNames[p] ?? copy.anyPlan`), a `<th>{copy.columns.plan}</th>` after the status column and `<td>{ADMIN.tenant.edit.planNames[t.plan_code ?? "pro"] ?? t.plan_code}</td>` in each row (the `colSpan` of the empty row goes from 9 to 10).

`presentation/app/admin/tenants/[id]/TenantEdit.tsx`:
- `fieldsOf` gains `plan_code: tenant.plan_code ?? "pro",`
- view mode, after the AI quota `<dd>`: 
```tsx
          <dt>{edit.plan}</dt>
          <dd>
            {edit.planNames[tenant.plan_code ?? "pro"] ?? tenant.plan_code}
            {tenant.usage && (
              <span className="pa-muted"> · {edit.usageUsers(tenant.usage.members, tenant.usage.members_limit)}
                {" · "}{edit.usageAi(tenant.usage.ai_reports_used, tenant.usage.ai_reports_allowance)}</span>
            )}
          </dd>
```
- edit mode, a select before the AI quota field:
```tsx
        <label className="pa-field">{edit.plan}
          <select value={form.plan_code} onChange={(e) => set("plan_code", e.target.value)} disabled={busy}>
            {(["starter", "pro", "enterprise", "enterprise_plus"] as const).map((code) => (
              <option key={code} value={code}>{edit.planNames[code]}</option>
            ))}
          </select>
          <span className="pa-muted" style={{ fontSize: 12 }}>{edit.planHint}</span>
          {refused && (
            <span className="pa-note pa-note-error" role="alert">
              {edit.planRefused(refused.inactivate, refused.label)} · {edit.planRefusedWhere}
            </span>
          )}
        </label>
```
  with, above the JSX, `const preview = form.plan_code !== initial.plan_code ? tenant.plan_preview?.previews?.[form.plan_code] : undefined;` and `const refused = preview?.refused ? preview : undefined;`
- the AI quota input is `disabled={busy || form.plan_code === "starter"}`, with `{form.plan_code === "starter" && <span className="pa-muted" style={{ fontSize: 12 }}>{edit.aiChartStarter}</span>}` under it and `placeholder={edit.aiChartDefault}`
- the save button is `disabled={busy || Boolean(refused)}` (the confirm is not offered for a refused downgrade — spec §9), and `save()` asks before a plan change, listing what locks with its counts:
```tsx
    if (diff.plan_code !== undefined && preview) {
      const affects = preview.locks.map((lock) => {
        const counts = Object.entries(lock.counts).filter(([, n]) => n > 0)
          .map(([word, n]) => `${edit.countWords[word] ?? word} ${n}`).join(" · ");
        return `${edit.planLocks} ${edit.featureNames[lock.feature] ?? lock.feature}${counts ? ` (${counts})` : ""}`;
      });
      const ok = await ask({ action: edit.planConfirm(preview.label), target: tenant.company_name,
                              affects, reversible: edit.planKeeps, confirmLabel: edit.planConfirm(preview.label) });
      if (!ok) return;
    }
```
  using `const { request: confirming, ask, close: closeConfirm } = useConfirm();` and rendering `<ConfirmDialogBase request={confirming} onClose={closeConfirm} copy={{ cancel: ADMIN.confirm.keepIt, permanent: ADMIN.confirm.cannotUndo }} />` at the end of the edit-mode `<section>` (the same import and props `TenantActions.tsx` uses).
- a 409 from the save shows the server's own sentence: `res.reason` already carries `message` (`reasonOf` reads it), so the existing `setNote({ text: … copy.actions.reason(res.reason) … })` path shows "ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น <plan>" if a stale page sends it anyway.

- [ ] **Step 6: Run to verify, then the admin suites, typecheck and build**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_admin.py tests/unit/test_backend_fixes.py tests/unit/test_round20c_chart_quota.py -q
cd ~/stage-fix/r21d/presentation && npm run typecheck && rm -rf .next && NEXT_TELEMETRY_DISABLED=1 npm run build > /tmp/21d-next-build.log 2>&1; tail -3 /tmp/21d-next-build.log; rm -rf .next
```
Expected: all pass; typecheck and build clean. A 20C test that sent `ai_chart_quota: ""` expecting 422 now gets the clearing behaviour — the owner's decision Q3 ("empty = plan default") replaces it; update that assertion and say so.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "feat(round21d): the admin console sets a shop's plan with what it locks in the confirm, refuses a downgrade over the user limit in words, and shows usage against the plan" && git log --oneline -1
```

---

### Task 14: The rich menu — no per-plan menu, and every tile answers on Starter

Spec §5.8 / owner decision Q6: **no per-plan rich menu this round** — the menu is per OA and language (`services/richmenu.py`, `scripts/richmenu/generate.py`), per LINE user, while the plan is per shop. Nothing in the menu changes. What this task proves is the spec's claim that makes that safe: *every tile either sends a text through chat (which answers or refuses on plan) or opens a LIFF page (which shows the locked panel)*. Checking it found one tile that does not: **"ทีมช่าง"** reaches `_maybe_handle_teams` by phrase, and its list branch is gated on `TEAM_VIEW_KEYS` (which includes `customer.read`), so a Starter shop would get a list of technician teams — the service feature. The fix is decline-only (a word may refuse on plan, never act).

**Files:**
- Modify: `application/chann_app/services/chat.py:962-1110` (`_maybe_handle_teams` — every branch that matched a team phrase refuses on plan first)
- Test: `tests/unit/test_round21d_richmenu.py`

**Interfaces:**
- Consumes (Task 7): `_plan_has`, `_plan_refusal`; (Task 8) the technician/customer OA gates; `scripts/richmenu/generate.py::TILES`.
- Produces: nothing new — a test that walks the real tile table.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21d_richmenu.py`:

```python
"""Round 21D — no per-plan rich menu (spec §5.8, owner decision Q6), which is
safe only if every tile, on a Starter shop, either works or refuses on plan.
Walks the REAL tile table, so a tile added later is covered."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from chann_app.config import settings
from chann_app.services.chat import handle_chat_message
from plan_fixtures import plan_payload
from test_phase6_chat import FakeDataClient, _ai, _ctx

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[2]


def _tiles():
    spec = importlib.util.spec_from_file_location("richmenu_generate", ROOT / "scripts/richmenu/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TILES


TILES = _tiles()
SUGGEST = json.dumps({"action": "suggest", "entity": None, "fields": {}, "missing": []})
#: The Sales tiles that ARE the service feature on a Starter shop.
SERVICE_TILES = {"รายการรออนุมัติ", "ทีมช่าง"}


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "qwen/qwen3.6-35b-a3b")


def _texts(oa: str) -> list[str]:
    return [tile[3]["text"] for page in ("main", "more") for tile in TILES[oa][page] if tile[3]["type"] == "message"]


@pytest.mark.parametrize("text", _texts("sales"))
async def test_a_sales_tile_on_starter(text):
    client = FakeDataClient(role="owner", permission_keys=[
        "customer.read", "deal.read", "followup.read", "product.read", "team.manage", "setting.manage",
        "ticket.read", "approval.view"])
    client._is_owner = True
    ctx = _ctx(primary_role="owner", oa="sales")
    ctx.memberships[0]["plan"] = plan_payload("starter")
    reply = await handle_chat_message(client, message=text, ctx=ctx,
                                      ai_client=httpx.AsyncClient(transport=_ai(SUGGEST)))
    if text in SERVICE_TILES:
        assert reply.text.startswith("🔒 «งานบริการ / งานซ่อม และทีมช่าง»"), (text, reply.text)
    else:
        assert "🔒" not in reply.text, (text, reply.text)


def test_the_uri_tiles_open_pages_the_nav_model_can_lock():
    nav = (ROOT / "presentation/app/liff/_nav-model.tsx").read_text(encoding="utf-8")
    for oa in ("sales",):
        for page in ("main", "more"):
            for tile in TILES[oa][page]:
                action = tile[3]
                if action["type"] == "uri" and action["uri"].endswith("/chats"):
                    line = next(l for l in nav.splitlines() if 'key: "chats"' in l and "/liff/sales/" in l)
                    assert 'feature: "feature.live_chat"' in line
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_richmenu.py -q
```
Expected: `test_a_sales_tile_on_starter[ทีมช่าง]` FAILS (the team list is answered); the others pass (Tasks 7–8 already cover them — "รายการรออนุมัติ" refuses through `_no_permission(language, "approval.manage", "approval.view")`). If another tile fails, it is a real gap of the same kind: fix it the same way and add it to `SERVICE_TILES` only if it IS the service feature.

- [ ] **Step 3: Decline on plan in `_maybe_handle_teams`**

Read the function; it has one `if` branch per team phrase (the list via `TEAM_LIST_PHRASES`, create via `TEAM_CREATE_TRIGGERS`, add-member via `_TEAM_ADD_RE`, lead via `_TEAM_LEAD_RE`, remove/delete). As the **first statement inside each branch that matched** (before any permission check or Data call), add:

```python
        if not _plan_has("feature.service"):
            # Round 21D: technician teams are the service feature (spec
            # §4.1). The phrase only declines here — it acts on nothing.
            return _plan_refusal("feature.service", language)
```

Do **not** add it before the matches (the function also returns `None` for sentences that are not about teams, which must fall through untouched), and do not widen any phrase table.

- [ ] **Step 4: Run to verify, then the tile and team suites**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_richmenu.py tests/unit/test_chat_review_a.py tests/unit/test_phase6_chat.py -q -k "tile or team or richmenu or Tile or Team"
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/check-triggers.py | tail -2
```
Expected: all pass; `check-triggers.py` shows no new trigger table (nothing was added — only a plan decline inside existing branches).

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r21d && git add -A && git commit -q -m "test(round21d): every rich-menu tile answers or refuses on plan on a Starter shop — no per-plan menu is needed; the team tile now declines on plan" && git log --oneline -1
```

---

### Task 15: The checkers learn the plan family, the scenarios, the guide and its picture, the handoff, the tester's section X

**Files:**
- Modify: `scripts/dev/check-perms.py` (the plan-key family; every permission key classified)
- Modify: `scripts/dev/check-parity.py` (the plan section + `ACCEPTED_PLAN`)
- Create: `scripts/agent-test/scenarios/round21d-plan-starter.yaml`, `scripts/agent-test/scenarios/round21d-plan-pro-unchanged.yaml`
- Modify: `application/chann_app/services/guides.py` — the `ai-reports` step's credit sentence (`:454`), the `api` step (`:523-541`), the `members` step (`:492-521`)
- Modify: `scripts/dev/render-guide-images.py:405-416` (`sales_members` — the plan card on the members page)
- Modify: `docs/SESSION_HANDOFF.md` (a "รอบ 21D" entry above "รอบ 21C"; replace `__21C_SHA__` with the deployed `449819e`)
- Modify: `~/CHECKLIST-หลัง-deploy.md` (section **X**, outside the repo)
- Test: `tests/unit/test_round21d_checkers.py`

**Interfaces:**
- Consumes: `chann_data.plans.ENTITLEMENT_KEYS`, `chann_data.permissions.PERMISSION_KEYS`, `chann_app.services.entitlements.{feature_of, feature_for_intent, ALWAYS_ON_PERMISSIONS}`, `chann_app.services.chat.ACTION_PERMISSIONS`, the `feature: "…"` entries of `_nav-model.tsx` (Task 12), `actor.plan` (Task 7).
- Produces: `check-perms.py` ends `every plan key in use is one of the ten · every permission key is classified` (exit 1 otherwise); `check-parity.py` prints a `PLAN` section and ends `every plan-gated capability is gated the same on both surfaces` before its existing last line.

- [ ] **Step 1: Write the failing test — green on the tree, red on a broken copy**

Create `tests/unit/test_round21d_checkers.py`:

```python
"""Round 21D — the two checkers know the plan (spec §10). Each is proven
in both directions: clean on the real tree, and failing on a file that
uses a key outside the ten (round 20K: a checker that cannot go red
proves nothing)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"scripts/dev/{script}"], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def test_check_perms_is_clean():
    out = _run("check-perms.py")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "every plan key in use is one of the ten · every permission key is classified" in out.stdout


def test_check_perms_goes_red_on_an_unknown_plan_key():
    probe = ROOT / "presentation/app/liff/_round21d_probe.tsx"
    probe.write_text('export const x = (p: unknown) => planHas(p as never, "feature.gold");\n', encoding="utf-8")
    try:
        out = _run("check-perms.py")
    finally:
        probe.unlink()
    assert out.returncode == 1 and "feature.gold" in out.stdout


def test_check_parity_has_a_clean_plan_section():
    out = _run("check-parity.py")
    assert "every plan-gated capability is gated the same on both surfaces" in out.stdout, out.stdout[-1500:]
    assert "every capability is reachable from both surfaces" in out.stdout
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_checkers.py -q
```
Expected: FAIL — neither sentence is printed yet.

- [ ] **Step 3: `check-perms.py` — the ten keys, and every permission key classified**

Append to `scripts/dev/check-perms.py`:

```python
# ---------------------------------------------------------------- round 21D
# The plan family (spec §10): a feature./quota./limit. key anywhere in code
# must be one of the ten in chann_data/plans.py, and every permission key
# must be either in a PERMISSION_FEATURE family or named in
# ALWAYS_ON_PERMISSIONS — a new key has to be classified, not forgotten.
sys.path.insert(0, "application")
from chann_data.plans import ENTITLEMENT_KEYS  # noqa: E402
from chann_app.services.entitlements import ALWAYS_ON_PERMISSIONS, feature_of  # noqa: E402

PLAN_KEY = r"((?:feature|quota|limit)\.[a-z_]+)"
plan_used: dict[str, str] = {}
python_files = [*Path("application/chann_app").rglob("*.py"), *Path("data/chann_data").rglob("*.py")]
for f in python_files:
    text = f.read_text()
    for pattern in (rf'require_feature\("{PLAN_KEY}"', rf'entitled\([^,]+,\s*"{PLAN_KEY}"',
                    rf'\.has\("{PLAN_KEY}"\)', rf'_plan_has\("{PLAN_KEY}"\)', rf'_plan_refusal\("{PLAN_KEY}"'):
        for key in re.findall(pattern, text):
            plan_used.setdefault(key, str(f))
for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    for pattern in (rf'feature: "{PLAN_KEY}"', rf'planHas\([^,()]+,\s*"{PLAN_KEY}"\)'):
        for key in re.findall(pattern, text):
            plan_used.setdefault(key, str(f))

problems = [f"  {key}   ({where}) — not one of the ten plan keys"
            for key, where in sorted(plan_used.items()) if key not in ENTITLEMENT_KEYS]
problems += [f"  {key} — in no PERMISSION_FEATURE family and not in ALWAYS_ON_PERMISSIONS"
             for key in sorted(PERMISSION_KEYS) if feature_of(key) is None and key not in ALWAYS_ON_PERMISSIONS]
print(f"checked {len(plan_used)} plan keys in use")
if problems:
    print("\nPLAN KEYS:")
    print("\n".join(problems))
    raise SystemExit(1)
print("every plan key in use is one of the ten · every permission key is classified")
```

- [ ] **Step 4: `check-parity.py` — gated in chat but not on the dashboard**

Append to `scripts/dev/check-parity.py` **before** its final `if chat_only or dash_only:` block (so its last line stays the one the deploy script greps):

```python
# ---------------------------------------------------------------- the plan (round 21D)
# The failure this catches: a Starter shop refused in LINE and served on the
# screen, or the other way round (spec §10). For every capability chat
# routes, the feature its (action, entity) resolves to must be the feature
# the dashboard page for that entity declares in _nav-model.tsx.
from chann_app.services.entitlements import feature_for_intent  # noqa: E402

#: nav entry key → the entity its page is about.
NAV_ENTITY = {
    "chats": "chat_session", "tickets": "ticket", "teams": "team", "reports": "service_report",
    "satisfaction": "survey", "approvals": "approval", "warranties": "warranty", "apiKeys": "api_key",
    "roles": "role", "invoices": "invoice", "quotes": "quote",
}
#: Deliberately different on the two surfaces — each with the reason.
ACCEPTED_PLAN = {
    ("role", "create"): "the roles page stays (standard roles on every plan); its create/edit controls lock with rolesLocked",
    ("role", "update"): "same page, same reason",
    ("api_key", "read"): "the API page stays readable under the lock (lockMode notice); listing and revoking are open on both surfaces",
    ("api_key", "delete"): "revoking is open on both surfaces — a downgraded owner must be able to cut an outside system off",
    ("invoice", "send"): "the invoice page stays; its send button is disabled with sendLineLocked",
    ("quote", "send"): "the quote page stays; its send button is disabled with sendLineLocked",
}
nav_text = Path("presentation/app/liff/_nav-model.tsx").read_text()
nav_feature = dict(re.findall(r'key: "(\w+)", href: "/liff/sales/[^"]*"[^\n]*?feature: "([a-z_.]+)"', nav_text))
page_of = {entity: key for key, entity in NAV_ENTITY.items()}
plan_gaps = []
for (action, entity), key in sorted(ACTION_PERMISSIONS.items(), key=lambda p: (p[0][1], p[0][0])):
    page = page_of.get(entity)
    if page is None or (entity, action) in ACCEPTED_PLAN:
        continue
    in_chat, on_screen = feature_for_intent(action, entity, key), nav_feature.get(page)
    if in_chat != on_screen:
        plan_gaps.append(f"PLAN  {entity}/{action}: chat={in_chat or '—'}  dashboard={on_screen or '—'}")
print(f"{len(ACCEPTED_PLAN)} plan differences accepted on purpose")
if plan_gaps:
    print("\n".join(plan_gaps))
    print("Either gate both surfaces on the same feature or add it to ACCEPTED_PLAN with a reason.")
else:
    print("every plan-gated capability is gated the same on both surfaces")
```

and make a plan gap fail the run: at the very end of the file, after the existing final `print`, add `raise SystemExit(1 if plan_gaps or chat_only or dash_only else 0)` **only if** the file does not already exit non-zero on gaps (check how the gate treats it — `grep -n SystemExit scripts/dev/check-parity.py`; the deploy script greps its last line, so the existing last line must stay last on a clean run).

- [ ] **Step 5: The scenarios — the shipped contract for Starter, and for every shop that is Pro today**

Create `scripts/agent-test/scenarios/round21d-plan-starter.yaml`:

```yaml
# Round 21D — a Starter shop (spec §11.4). Every reading is a model answer
# the scenario supplies (no scenario reaches a real model); the three that
# the model reads are the shapes ask-model.py returned for these sentences
# (Task 10 / the handoff). The sixth person's refused join is a Data-tier
# transaction and is proven on Postgres: tests/integration/test_round21d_plan_data.py.
name: round21d-plan-starter
description: a Starter shop hears which plan has a feature, keeps its three free reports, and cannot mint a technician code
backend: fake
actor:
  oa: sales
  role: owner
  language: th
  permissions: all
  plan: starter

steps:
  - send:
      message: เปิดงานซ่อมให้สมชาย
      ai: {action: create, entity: ticket, fields: {target_name: สมชาย}, missing: []}
    expect:
      contains: ["🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter",
                 "ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ"]
      is_not: [permission, generic_error, not_a_feature]

  - send:
      message: ลงทะเบียนประกัน SN123
      ai: {action: create, entity: warranty, fields: {serial_number: SN123}, missing: []}
    expect:
      contains: ["🔒 «ทะเบียนเครื่อง / ประกัน» อยู่ในแพ็กเกจ Pro ขึ้นไป"]
      is_not: [permission, generic_error, not_a_feature]

  - send: "สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน"
    expect:
      contains: ["🔒 «ถามรายงานด้วย AI» อยู่ในแพ็กเกจ Pro ขึ้นไป",
                 "รายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) ยังถามได้ฟรีเสมอ"]
      used_ai: false

  - send:
      message: ยอดมูลค่าดีลทั้งหมด
      ai: {action: read, entity: report, fields: {type: sales}, missing: [], report: pipeline_value}
    expect:
      contains: ["มูลค่าดีลทั้งหมด"]
      not_contains: ["🔒"]
      max_lines: 15
      is_not: [permission, generic_error, not_a_feature]
    # The buttons under it (no "ดูเป็นรูป", no service reports on Starter)
    # are pinned in tests/unit/test_round21d_credits.py — the scenario
    # language has no "quick replies exclude".

  - send: ขอรหัสเชิญช่าง
    expect:
      contains: ["🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป"]
      used_ai: false
```

Create `scripts/agent-test/scenarios/round21d-plan-pro-unchanged.yaml` — the regression that matters most, since every existing shop becomes Pro:

```yaml
# Round 21D — every shop that exists today is Pro after migration 0039. The
# same sentences as the Starter scenario must do what they did before:
# nothing here may say "🔒".
name: round21d-plan-pro-unchanged
description: a Pro shop is served exactly as before 21D
backend: fake
actor:
  oa: sales
  role: owner
  language: th
  permissions: all
  plan: pro

steps:
  - send:
      message: เปิดงานซ่อมให้สมชาย
      ai: {action: create, entity: ticket, fields: {target_name: สมชาย}, missing: []}
    expect:
      not_contains: ["🔒"]
      is_not: [permission, generic_error, not_a_feature]

  - send:
      message: ยอดมูลค่าดีลทั้งหมด
      ai: {action: read, entity: report, fields: {type: sales}, missing: [], report: pipeline_value}
    expect:
      contains: ["มูลค่าดีลทั้งหมด"]
      quick_replies_include: ["ดูเป็นรูป"]
      not_contains: ["🔒"]

  - send: ขอรหัสเชิญช่าง
    expect:
      not_contains: ["🔒"]
      used_ai: false

  - send:
      message: ร้านใช้แพ็กเกจอะไร
      ai: {action: read, entity: plan, fields: {}, missing: []}
    expect:
      contains: ["แพ็กเกจของร้าน: Pro"]
```

Run them (and every shipped scenario — the contract is the whole set):

```bash
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/agent-test/run.py | tail -3
```
Expected: `… scenarios · N passed · 0 failed`. A Starter step that fails because the reply took a road the scenario did not expect is information: read the reply, and if the reply is right and the expectation wrong, fix the expectation **and say so**; if the reply is wrong, fix the code.

- [ ] **Step 6: The guide — and no new help-menu topic**

The help menu is at its 15-line limit (round 21B added the tenth sales topic; `render_help_menu` prints one line per step), so the plan goes into existing steps, not a new one. In `application/chann_app/services/guides.py`:

`ai-reports` step — replace `(ค่าเริ่มต้น 30 ครั้งต่อเดือน)` with `(ตามแพ็กเกจ: Pro 30 · Enterprise 100 ครั้งต่อเดือน)` and `(30 by default)` with `(by plan: Pro 30 · Enterprise 100 a month)`, and add after that line:

```python
                    {"th": "แพ็กเกจ Starter ถามได้เฉพาะรายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) — ฟรีเสมอ", "en": "On the Starter plan only the basic reports are asked (pipeline value · won this month · outstanding) — always free"},
```

`api` step — add as the last `how` line:

```python
                    {"th": "มีในแพ็กเกจ Enterprise ขึ้นไป · ร้านที่ลดแพ็กเกจ key ยังอยู่ ดูและเพิกถอนได้ ใช้งานได้อีกเมื่ออัปเกรด", "en": "Included from the Enterprise plan · after a downgrade the keys stay, can be listed and revoked, and work again on upgrade"},
```

`members` step — a group before `{"group": {"th": "จากแชท", ...}}`:

```python
                    {"group": {"th": "แพ็กเกจของร้าน", "en": "The shop's plan"}},
                    {"th": "หน้าจอ > ข้อมูลบริษัท: การ์ดแพ็กเกจ — ผู้ใช้ n/สูงสุด · เครดิตรายงาน AI เดือนนี้ · ฟีเจอร์ที่มี/ยังไม่มี", "en": "Home > Company: the plan card — users n/limit · AI credits this month · what the plan includes", "type": "ร้านใช้แพ็กเกจอะไร"},
                    {"th": "ผู้ใช้ครบตามแพ็กเกจแล้ว รหัสเชิญใหม่จะไม่ออก — นำคนที่ไม่ได้ใช้ออกก่อน หรือติดต่อทีม Chann เพื่ออัปเกรด", "en": "At the plan's user limit no new invite is issued — remove someone who no longer uses it, or contact Chann to upgrade"},
                    {"th": "ฟีเจอร์ที่ไม่อยู่ในแพ็กเกจขึ้นรูปกุญแจพร้อมบอกว่ามีในแพ็กเกจไหน — ข้อมูลเดิมไม่หาย", "en": "A feature outside the plan shows a lock and which plan has it — nothing already recorded is lost"},
```

(`"ร้านใช้แพ็กเกจอะไร"` is a `type`, not a `commands` entry: it is read by the model, not a trigger — `test_every_command_in_the_guide_is_a_chat_trigger` checks `commands` only.)

```bash
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/render-guides.py && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_guides.py -q
```

- [ ] **Step 7: The picture — drawn by the script, then LOOKED at**

In `scripts/dev/render-guide-images.py`, `sales_members`: directly after `c.dash_frame(...)`, draw the plan card the members/company pages now carry:

```python
    c.card("แพ็กเกจ Pro", ["ผู้ใช้ 9/15 คน · เครดิตรายงาน AI เดือนนี้ 12/30",
                            "มีครบ: งานบริการ · ประกัน · แชทลูกค้า · LINE ลูกค้า",
                            "ยังไม่มี: อนุมัติหลายระดับ · API ภายนอก — Enterprise ขึ้นไป"],
           [("ติดต่อทีม Chann เพื่ออัปเกรด", True)])
```

and shorten the scene's final `c.note(...)` to `"เพิ่มช่าง: \"ขอรหัสเชิญช่าง\" · ผู้ใช้ครบตามแพ็กเกจ รหัสใหม่จะไม่ออก · เจ้าของร้านนำออกไม่ได้"` so it still fits. (No lock emoji: the guide fonts do not carry it — words instead.)

```bash
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/render-guide-images.py --only sales-members && /tmp/dv/bin/python scripts/dev/render-guide-images.py --check
```

**Open `application/chann_app/static/help/sales-members.png` and look at it** (the Read tool shows images). No test can see it: check the card is inside the frame, no Thai word is cut, the table and the removed-member card still fit below, nothing overlaps the note. Re-run until it does. Never edit the PNG.

- [ ] **Step 8: The handoff entry**

In `docs/SESSION_HANDOFF.md`: replace every `__21C_SHA__` with `449819e` (21C is deployed), then insert above `**รอบ 21C (23 ก.ย.`:

```markdown
**รอบ 21D (24 ก.ย. — DEV `__21D_SHA__`, ต่อจาก `449819e`): ตัวควบคุมฟีเจอร์ตามแพ็กเกจ — Starter · Pro · Enterprise · Enterprise Plus**

- **เจ้าของ:** *"ตัวควบคุมฟีเจอร์ตาม plan"* + ตารางแพ็กเกจ (`docs/superpowers/specs/2026-09-24-sales-plans-source.html`) · spec
  `docs/superpowers/specs/2026-09-24-plan-entitlements-design.md` · plan `docs/superpowers/plans/2026-09-24-plan-entitlements.md` ·
  เจ้าของตัดสิน 5 ข้อ (24 ก.ย.): backfill ขึ้น (Pro / Enterprise เมื่อมี API key · อนุมัติหลายขั้น · >15 คน / Enterprise Plus >50) ·
  **ลดแพ็กเกจถูกปฏิเสธถ้าสมาชิกที่ใช้งานเกินขีดจำกัดของแพ็กเกจใหม่** ("ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น …") ·
  Enterprise Plus เริ่ม 100 เครดิต เติมได้ Pro ขึ้นไป · Starter เห็นรายงานพื้นฐาน 3 ตัว · ปุ่มอัปเกรดเปิด `CHANN_SALES_CONTACT` (ไม่ตั้ง = ไม่มีปุ่ม)
- **Data:** `plans.py` (ตารางเดียว ทดสอบเทียบ HTML ทีละช่อง) · migration **0039_license_plan** (+CHECK, backfill, assertion ว่าไม่มีร้านเกินขีด) ·
  `PlanRepository` (seat ใน transaction เดียวกับการเข้าร่วม/เปิดกลับ/ย้าย, การเปลี่ยนแพ็กเกจ, preview) · แพ็กเกจเดินไปกับ membership /
  api-key resolve / tenant rows · cache `license_plan:<id>` แยกจาก `permissions:*` (PATCH แพ็กเกจลบคีย์เดียว)
- **Application:** `entitlements.py` · principal ลบ key ที่แพ็กเกจล็อกในที่เดียว + `require_feature` · 403 `plan_required` รูปเดียวทุกผิว ·
  API ภายนอก 403 ต่ำกว่า Enterprise (หลังตรวจ key) · credits ตามแพ็กเกจ
- **แชท (model-first):** คำปฏิเสธ §6.2 · ประตูกลาง plan ก่อน permission · `_no_permission` แทน 115 จุดที่ตอบ "ไม่มีสิทธิ์" เปล่า ๆ ·
  prompt เพิ่ม `entity="plan"` บล็อกเดียว — **ผล ask-model ก่อน/หลัง ทีละประโยค:** <วางผลจริงทั้ง 12 ประโยคจาก /tmp/21d-plan-before.txt และ /tmp/21d-plan-after.txt>
- **OA:** ช่างของร้านที่ลดแพ็กเกจได้ประโยคเดียวต่อข้อความ (โปรไฟล์/ภาษายังใช้ได้) · ลูกค้าได้เบอร์ร้าน · storefront ยังใช้ได้ ·
  รหัสเชิญช่างที่ออกก่อนลดแพ็กเกจไม่ถูกใช้ไป · คนที่เข้าร่วมไม่ได้เพราะเต็ม → เจ้าของได้แจ้ง
- **จอ (ui-ux-pro-max):** เมนูที่ล็อกขึ้นกุญแจ + "Pro ขึ้นไป" เฉพาะคนที่อัปเกรดได้ · หน้าล็อก · การ์ดแพ็กเกจ · เหตุผลใต้ปุ่มที่ปิดทุกปุ่ม ·
  admin: เลือกแพ็กเกจ + ยืนยันพร้อมรายการที่จะล็อก · ลดแพ็กเกจเกินขีด = ไม่มีปุ่มยืนยัน
- **ตั้งใจไม่ทำ / ข้อจำกัดที่รู้:** rich menu ไม่แยกตามแพ็กเกจ (ทุกปุ่มตอบหรือปฏิเสธตามแพ็กเกจ — พิสูจน์ใน `test_round21d_richmenu.py`) ·
  Daily Summary ไม่มีส่วนงานซ่อมให้ตัด (แจ้งเตือนเฉพาะนัด) · `/me/permissions` ไม่ส่ง usage (การ์ดและหน้าสมาชิกขอ `…/plan` เอง) ·
  อ่าน/เพิกถอน API key ยังได้หลังลดแพ็กเกจ (สร้างไม่ได้) · แจ้งเจ้าของเรื่องเปลี่ยนแพ็กเกจเป็น best-effort
- เทสต์: `tests/unit/test_round21d_{plan_matrix,migration_literals,entitlements,principal,ext_api,chat_refusal,no_bare_refusal,oa_behaviour,credits,plan_read,background,ui,admin,richmenu,checkers}.py`,
  `tests/integration/test_round21d_{migration,plan_data,feature_checks}.py`, scenario `round21d-plan-starter.yaml` + `round21d-plan-pro-unchanged.yaml` ·
  รูป `sales-members` วาดใหม่และ**ดูด้วยตาแล้ว**
- **หลัง deploy ต้องพิสูจน์ของจริง:** ร้านทดสอบ → Starter ใน `/admin` → X-1…X-9 ใน `~/CHECKLIST-หลัง-deploy.md` → กลับเป็น Pro → ข้อมูลเดิมครบ
```

(Paste the real readings where the `<…>` marker is before committing Task 16 — the marker must not reach the patch; Task 16's verify block greps for it.)

- [ ] **Step 9: The tester's section X**

Append to `~/CHECKLIST-หลัง-deploy.md`:

```markdown
## X. รอบ 21D (`__21D_SHA__`) — ตัวควบคุมฟีเจอร์ตามแพ็กเกจ

ใช้ร้านทดสอบร้านเดียว ตั้งเป็น **Starter** ใน `/admin` ก่อน (ถ้ามีสมาชิกเกิน 5 คน ระบบจะไม่ให้ลด — ข้อ X-1)

- [ ] **X-1** `/admin` > ร้านทดสอบ > แก้ไข > แพ็กเกจ: ร้านที่มีผู้ใช้เกิน 5 คน เลือก Starter → ขึ้น "ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น Starter" และ**ไม่มีปุ่มบันทึก** · ถอดออกให้เหลือ 5 → เลือก Starter → กล่องยืนยันบอก "จะล็อก: …" พร้อมจำนวน → ยืนยัน → เจ้าของร้านได้ข้อความใน LINE ว่าแพ็กเกจเปลี่ยน
- [ ] **X-2** Sale OA (เจ้าของ): "เปิดงานซ่อมให้สมชาย" → "🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter" + ข้อมูลเดิมยังอยู่ + ติดต่อทีม Chann (มีปุ่มถ้าตั้ง `CHANN_SALES_CONTACT`) · พนักงานขายพิมพ์ประโยคเดียวกัน → "แจ้งเจ้าของร้านได้เลย"
- [ ] **X-3** "สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน" → ปฏิเสธตามแพ็กเกจ + บอกว่ารายงานพื้นฐานถามได้ฟรี · "ยอดมูลค่าดีลทั้งหมด" → ได้ตัวเลข (ไม่มีปุ่ม "ดูเป็นรูป") · หน้ารายงาน AI มีการ์ด **3** ใบ และกล่องถามปิดพร้อมเหตุผลใต้กล่อง
- [ ] **X-4** แดชบอร์ด (เจ้าของ): เมนู แชทลูกค้า / งานซ่อม / ทีม / รายงาน / ความพึงพอใจ / อนุมัติ / ประกัน / แบบฟอร์ม / API ขึ้น**กุญแจ + "Pro ขึ้นไป" (หรือ Enterprise ขึ้นไป)** · แตะ → หน้าล็อก · พนักงานขาย: เมนูพวกนั้น**ไม่แสดง** · เปิด URL `/liff/sales/tickets` ตรง ๆ → หน้าล็อก
- [ ] **X-5** หน้าข้อมูลบริษัท: การ์ด "แพ็กเกจ Starter" ผู้ใช้ n/5 · ถามรายงานด้วย AI มีใน Pro ขึ้นไป · รายการฟีเจอร์ ✓/กุญแจ · Sale OA "ร้านใช้แพ็กเกจอะไร" → ได้ข้อมูลเดียวกัน
- [ ] **X-6** บทบาท: ปุ่ม "สร้างบทบาท" ปิดพร้อมเหตุผล · ใบแจ้งหนี้/ใบเสนอราคา: ปุ่มส่งทางไลน์ปิดพร้อม "ส่งทางไลน์มีในแพ็กเกจ Pro ขึ้นไป" · ตั้งการอนุมัติ: เหตุผลใต้กล่อง
- [ ] **X-7** "ขอรหัสเชิญช่าง" → ปฏิเสธตามแพ็กเกจ · ร้านมีผู้ใช้ 5 คน "ขอรหัสเชิญทีมขาย" → "ร้านมีผู้ใช้ครบ 5 คนตามแพ็กเกจ Starter แล้ว …" · รหัสทีมขายที่ออกไว้ก่อน ให้คนที่ 6 พิมพ์ → "ยังเข้าร่วมไม่ได้" และเจ้าของได้แจ้ง
- [ ] **X-8** LINE ช่าง (ช่างเดิมของร้าน): พิมพ์อะไรก็ได้ → "ร้าน … ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ Starter) …" · "english please" ยังเปลี่ยนภาษาได้ · LINE ลูกค้า (ลูกค้าที่ผูกไว้): "แอร์ไม่เย็น" → "ร้าน … ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ …" · "สินค้าทั้งหมด" ยังใช้ได้
- [ ] **X-9** API ภายนอก: `curl -H "Authorization: Bearer <key ของร้านนี้>" …/api/ext/v1/me` → 403 `plan_required` · key ผิด → ยังเป็น 401
- [ ] **X-10** `/admin` ตั้งกลับเป็น **Pro** → ทุกข้อข้างบนกลับมาเหมือนเดิม **ข้อมูลเดิมครบ** (ใบงาน ประกัน แชท แบบฟอร์ม บทบาท) · ร้านอื่นที่ไม่ได้แตะเป็น Pro/Enterprise ตาม backfill และใช้งานได้ตามปกติ
```

- [ ] **Step 10: Run to verify, then commit**

```bash
cd ~/stage-fix/r21d && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21d_checkers.py tests/unit/test_guides.py -q
cd ~/stage-fix/r21d && for n in check-perms check-parity; do /tmp/dv/bin/python scripts/dev/$n.py | tail -2; done
cd ~/stage-fix/r21d && git add -A && git commit -q -m "chore(round21d): the checkers know the plan family, the Starter and Pro scenarios, the guide says what each plan has, the handoff and the tester's section X" && git log --oneline -1
```

---

### Task 16: The full gate, the patch, the deploy script, and the runtime proof

**Files:**
- Create: `~/round21d-deploy.sh` (from `~/round21c-deploy.sh` — the last round **with** a migration)
- Create: `~/round21d-v1-<N>.patch`
- Verify: the whole gate on **this** tree; `tests/integration` on `pg21c` (5434); a fresh-clone apply; `/health` on DEV.

- [ ] **Step 1: The full gate on THIS tree**

`~/stage-fix/tools/gate.sh` starts with `cd ~/stage-fix/registry` — run a copy pointed here, or it tests the previous round:

```bash
mkdir -p ~/stage-fix/out && sed 's#cd ~/stage-fix/registry#cd ~/stage-fix/r21d#' ~/stage-fix/tools/gate.sh > ~/stage-fix/out/gate-21d.sh
grep -n "cd ~/stage-fix/r21d" ~/stage-fix/out/gate-21d.sh
```
Launch it as a **tracked background task** (the harness's `run_in_background`), command: `cd ~/stage-fix/r21d && setsid nohup bash ~/stage-fix/out/gate-21d.sh > ~/stage-fix/out/gate-21d.log 2>&1 < /dev/null`, and do not say it is running until the task id comes back. Wait for `=== gate done (clean)` (≈ 10 min; never two gates at once). Then, separately:

```bash
cd ~/stage-fix/r21d && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5434/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration -q -p no:cacheprovider | tail -1
cd ~/stage-fix/r21d && /tmp/dv/bin/python scripts/dev/render-guides.py --check && /tmp/dv/bin/python scripts/dev/render-guide-images.py --check
cd ~/stage-fix/r21d/presentation && npm run typecheck && rm -rf .next && NEXT_TELEMETRY_DISABLED=1 npm run build > /tmp/21d-next-build.log 2>&1; tail -2 /tmp/21d-next-build.log; rm -rf .next ~/.npm/_cacache
```
All must be clean — integration `… passed`, **none skipped for a missing database** (a skip is not a pass).

- [ ] **Step 2: The real model, because chat changed**

```bash
cd ~/stage-fix/r21d && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/simulate-phrasings.py --real | tail -5
CONVERSE_ROOT=~/stage-fix/r21d OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python ~/stage-fix/tools/converse.py \
  ~/stage-fix/scenarios/smoke.json --report /tmp/21d-converse-smoke.json --quiet | tail -5
```
**Read the output, not the exit code** (CLAUDE.md, 18 ก.ย.: the offline run reports `0 long replies` because it never asks the model). Every shop in these runs has no plan in its membership, i.e. **Pro** — so any reply containing "🔒" is a regression in the Pro path and stops the round. Record both results in the handoff entry.

- [ ] **Step 3: The handoff has its real readings**

```bash
cd ~/stage-fix/r21d && grep -n "<วางผลจริง" docs/SESSION_HANDOFF.md
```
Must print nothing — paste the twelve before/after readings from `/tmp/21d-plan-before.txt` / `/tmp/21d-plan-after.txt` (Task 10) and the Step 2 results into the 21D entry, then `git add -A && git commit -q -m "docs(round21d): the handoff carries the model's readings before and after, and the real-model runs"`.

- [ ] **Step 4: One patch, validated on a fresh clone**

```bash
cd ~/stage-fix/r21d && git fetch -q origin && git log -1 --pretty='%h %s' origin/main   # must be 449819e, round 21C
cd ~/stage-fix/r21d && git status --porcelain | head -3                                  # must be empty
cd ~/stage-fix/r21d && git diff --binary origin/main > /tmp/r21d.patch && N=$(wc -l < /tmp/r21d.patch) \
  && cp /tmp/r21d.patch ~/round21d-v1-$N.patch && echo ~/round21d-v1-$N.patch
rm -rf /tmp/fc-21d && git clone -q ~/chann-crm-ai/.git /tmp/fc-21d && cd /tmp/fc-21d \
  && git checkout -q 449819e && git apply --3way --check ~/round21d-v1-$N.patch && echo CLEAN
```
`CLEAN` or stop. (If `origin/main` is no longer `449819e`, someone deployed in between — rebase `r21d` onto it first: `git rebase --onto origin/main 404cfe3 r21d`, rerun Step 1.)

- [ ] **Step 5: The deploy script**

`cp ~/round21c-deploy.sh ~/round21d-deploy.sh` and change, in order:

1. the header comment → round 21D: *"ตัวควบคุมฟีเจอร์ตามแพ็กเกจ — Starter · Pro · Enterprise · Enterprise Plus"*, the five owner decisions in one line each, `ต้อง deploy รอบ 21C ก่อน (base 449819e) · รอบนี้ **มี migration** (0039_license_plan — คอลัมน์ plan_code + CHECK + backfill ขึ้น + assertion ไม่มีร้านเกินขีด)`;
2. `PATCH_NAME="round21d-v1-$N.patch"`, `PATCH_LINES=$N`;
3. `COMMIT_SUBJECT='feat(round21d): a licence has a sales plan — Starter, Pro, Enterprise, Enterprise Plus — and every surface refuses what the plan does not include, naming the plan that does'`;
4. `BASE_SUBJECT` = the 21C subject exactly (`git log -1 --pretty=%s origin/main`);
5. **one** Postgres port for both the probe and the URL (the 21B/21C scripts once probed one port and used another):
   ```bash
   PG_PORT="${PG_PORT:-5434}"
   PG_URL="postgresql+psycopg://postgres:pg@127.0.0.1:${PG_PORT}/chann_test"
   ```
   and every `pg_isready -h 127.0.0.1 -p 5435` → `pg_isready -h 127.0.0.1 -p "$PG_PORT"`, the HALT hint → `docker start pg21c` / `docker run -d --name pg21c -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=chann_test -p 5434:5432 postgres:16-alpine`; then `grep -n "5435" ~/round21d-deploy.sh` must print nothing;
6. the STAGE 2 verify block, replacing 21C's greps with symbols only this round introduces:
   ```bash
   grep -qF 'EXPECTED_MIGRATION_HEAD = "0039_license_plan"' data/chann_data/main.py              || die "verify: migration head ไม่ใช่ 0039"
   [ -f database/alembic/versions/0039_license_plan.py ]                                         || die "verify: ไม่มี migration 0039"
   grep -qF 'down_revision = "0038_deal_closed_at"' database/alembic/versions/0039_license_plan.py || die "verify: 0039 ไม่ได้ต่อจาก 0038"
   grep -qF 'def plan_moves' database/alembic/versions/0039_license_plan.py                      || die "verify: ไม่มี assertion ร้านเกินขีด"
   grep -qF 'ROW_MAP' data/chann_data/plans.py                                                   || die "verify: ไม่มีตารางแพ็กเกจ"
   grep -qF 'class PlanRepository' data/chann_data/repositories/plan_repo.py                     || die "verify: ไม่มี PlanRepository"
   grep -qF 'def check_change' data/chann_data/repositories/plan_repo.py                         || die "verify: ลดแพ็กเกจไม่ถูกปฏิเสธ"
   grep -qF 'def k_license_plan' data/chann_data/cache.py                                        || die "verify: ไม่มี cache key ของแพ็กเกจ"
   grep -qF 'class PlanView' application/chann_app/services/entitlements.py                      || die "verify: ไม่มี PlanView"
   grep -qF 'def require_feature' application/chann_app/services/authorization.py                || die "verify: ไม่มี require_feature"
   grep -qF 'require_feature("feature.external_api")' application/chann_app/auth/api_key.py      || die "verify: API ภายนอกไม่ถูกล็อกตามแพ็กเกจ"
   grep -qF 'def _no_permission' application/chann_app/services/chat.py                          || die "verify: ไม่มี _no_permission"
   grep -qF '("read", "plan")' application/chann_app/services/chat.py                            || die "verify: แชทอ่านแพ็กเกจไม่ได้"
   grep -qF 'entity="plan"' application/chann_app/services/ai/intent.py                          || die "verify: prompt ไม่มี plan"
   grep -qF 'export function PlanLocked' presentation/app/liff/_plan.tsx                         || die "verify: ไม่มีหน้าล็อก"
   grep -qF 'plan_preview' "presentation/app/admin/tenants/[id]/TenantEdit.tsx"                  || die "verify: admin ไม่มี preview แพ็กเกจ"
   grep -qF 'CHANN_SALES_CONTACT' infrastructure/terraform/cloud_run.tf                          || die "verify: terraform ไม่ส่ง CHANN_SALES_CONTACT"
   [ -f scripts/agent-test/scenarios/round21d-plan-starter.yaml ]                                || die "verify: ไม่มี scenario Starter"
   [ -f scripts/agent-test/scenarios/round21d-plan-pro-unchanged.yaml ]                          || die "verify: ไม่มี scenario Pro"
   grep -q '<วางผลจริง' docs/SESSION_HANDOFF.md && die "verify: handoff ยังไม่มีผล ask-model จริง"
   info "symbol ครบ 20 จุด (21D)"
   ```
7. STAGE 3: keep the two SmartBrowz `--deselect` lines **exactly as they are**; keep `check-parity`'s `every capability is reachable from both surfaces` grep and add, after it: `grep -q "every plan-gated capability is gated the same on both surfaces" /tmp/dv-check-parity.out || die "check-parity: แผนแพ็กเกจสองฝั่งไม่ตรงกัน"`;
8. STAGE 5 migration comment and info line: `# 0039 เพิ่ม licenses.plan_code NOT NULL DEFAULT 'pro' + CHECK แล้ว backfill ขึ้น — data tier 21C ไม่อ่านคอลัมน์นี้ migrate ก่อน data จึงปลอดภัย` · `info "migration ผ่าน (head ควรเป็น 0039_license_plan — log ของ job มีจำนวนร้านต่อแพ็กเกจ)"`;
9. STAGE 6, after the SmartBrowz block — the owner-supplied contact (owner decision Q5), never printed, line added when the tfvars has none:
   ```bash
   if [ -f "$HOME/.chann_sales_contact" ]; then
     export CHANN_SALES_CONTACT_VALUE="$(head -1 "$HOME/.chann_sales_contact")"
     python3 - <<'PYEOF'
   import os, re
   path = "infrastructure/terraform/envs/dev/terraform.tfvars"
   text = open(path, encoding="utf-8").read()
   value = os.environ["CHANN_SALES_CONTACT_VALUE"].replace('"', '')
   if re.search(r'^chann_sales_contact\s*=', text, flags=re.M):
       text = re.sub(r'^(chann_sales_contact\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"' + value + '"', text, count=1, flags=re.M)
   else:
       text = text.rstrip("\n") + '\nchann_sales_contact = "' + value + '"\n'
   open(path, "w", encoding="utf-8").write(text); print("  tfvars: chann_sales_contact ตั้งแล้ว")
   PYEOF
     unset CHANN_SALES_CONTACT_VALUE
   else
     info "ไม่มี ~/.chann_sales_contact — ปุ่ม 'ติดต่อทีม Chann เพื่ออัปเกรด' จะไม่แสดง (ตั้งทีหลังได้: echo 'LINE @channcrm|https://line.me/R/ti/p/@channcrm' > ~/.chann_sales_contact แล้วรัน script ซ้ำ)"
   fi
   ```
10. the commit body (STAGE 4 heredoc): the owner's sentence, the five decisions, and what was built, in the 21C style;
11. the closing `TXT` block → checklist section **X** (X-1 … X-10), and `sed -i "s/__21D_SHA__/${SHORT}/" "$HOME/CHECKLIST-หลัง-deploy.md" 2>/dev/null || true`;
12. every `round21c-deploy.sh` mention → `round21d-deploy.sh`.

```bash
bash -n ~/round21d-deploy.sh && echo SYNTAX OK
grep -c "round21c-deploy\|5435" ~/round21d-deploy.sh   # 0
grep -n "0039_license_plan\|PG_PORT" ~/round21d-deploy.sh | head
```

- [ ] **Step 6: Deploy — the owner's standing authorisation: a green clone deploys**

```bash
cd ~/chann-crm-ai && git status --porcelain | head -3 && git log --oneline -1    # clean, and on 449819e
```
Launch as a **tracked background task** whose command is
`cd ~/chann-crm-ai && setsid nohup env ALLOW_APPLY=YES bash /home/thanawinmax2/round21d-deploy.sh > ~/deploy-21d.log 2>&1 < /dev/null`
— detached, because a session interrupt kills a foreground deploy mid-flight; tracked, so it is visible. Watch `~/deploy-21d.log` for `DEPLOY OK — <sha>` (≈ 35 min). On `HALT`: read the log, fix in `~/stage-fix/r21d`, regenerate the patch (Step 4), rerun — the script resumes at build once the commit is on origin. If `docker push` halts on "no active account", **stop**: only the owner can re-authorise gcloud.

- [ ] **Step 7: Runtime proof**

```bash
AU=$(gcloud run services describe chann-crm-ai-dev-application --project=chann1-1 --region=asia-southeast1 --format='value(status.url)')
DU=$(gcloud run services describe chann-crm-ai-dev-data --project=chann1-1 --region=asia-southeast1 --format='value(status.url)')
curl -fsS "$AU/health" | grep -o '"git_commit":"[0-9a-f]*"'
curl -fsS "$DU/health" | grep -o '"schema_state":"[^"]*"\|"expected_migration_head":"[^"]*"'
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="chann-crm-ai-dev-migrate" AND textPayload:"0039_license_plan"' \
  --project=chann1-1 --limit=20 --freshness=2h --format='value(textPayload)'
```
`git_commit` must be the pushed SHA, the head `0039_license_plan`, `schema_state` `up-to-date`, and the job log must show `licences per plan …` and **no** `moved to` line (a `moved to` line means a shop was over its limit after the backfill — report it to the owner with the licence id). **Nothing is "deployed" until `/health` says so** (CLAUDE.md). The acceptance evidence is the owner's X-1 … X-10 on DEV, not this run.

- [ ] **Step 8: The tester guide artifact**

Read `https://claude.ai/artifact/V16FfyaeNmXGdfBq2vcU6d` with the Artifact tool (`action: "read"`), update the DEV SHA and the round note, add rows `X-1…X-10` in the same shape as the `21C-…` rows, and republish to the same URL.

- [ ] **Step 9: Reset the worktree**

```bash
cd ~/stage-fix/r21d && git fetch -q origin && git log --oneline -1 origin/main    # the 21D commit
```
(then `git reset -q --hard origin/main` once the owner confirms DEV — the branch's commits exist in the patch and on origin.)

---

## Self-review

### 1. Spec coverage

| Spec | Requirement | Task |
|---|---|---|
| §1 | a licence has a plan; entitlement beside permission; nothing deleted | 1, 2, 5; round trip proven in 4 (`TestTheRoundTrip`) |
| §2, §2.1, §11.1 | the 41-row matrix, parsed from the owner's HTML, cell by cell | 1 |
| §3.1–3.2 | `plan_code` + CHECK; `PLANS` literal; `resolve()` with every principal; cache seam `k_license_plan` | 1, 2, 3 |
| §3.3 | trials are Pro by the server default | 2 (server default; `create_license` writes no plan) |
| §3.4 | immediate change, audit `update`, invalidate one key; owner told; per-feature downgrade table | 3 (audit, cache), 13 (owner notice); rows: service 5/7/8/11/12, warranty 5/12, customer link 4/8/11/12, live chat 4/5/12, custom documents 5/11/12, custom roles 4/5/12, multi-level 4/12, external API 5/6/12, quota 9, members 3 |
| §4, §4.1 | ten keys, labels, `PERMISSION_FEATURE`, `ALWAYS_ON_PERMISSIONS`, named checks | 1, 5 |
| §5.1 | principal carries the plan, subtracts locked keys, `require_feature`, fails open as Pro | 5 |
| §5.2 | one refusal shape | 1 (Data), 5 (App) — the same sentence, tested equal |
| §5.3 | chat: gate order, `suggest_what_you_can_do`, every bare refusal, decline-only prefix | 7 (115 sites, not ~40), 9 (prefix) |
| §5.4, §8.1–8.5 | nav lock + reason, hidden from non-upgraders, `<PlanLocked>`, plan card, disabled-with-reason lines, contact | 12 |
| §5.5 | ext API 403 after key checks; `docs/API.md` | 6 |
| §5.6 | credits from the plan; Starter locked; three basic reports; guide text | 9, 15 |
| §5.7 | seat check in the Data transaction (redeem, reactivate, move); early refusal; technician invite needs service; owner told | 3, 4, 8 |
| §5.8, §13 Q6 | no per-plan rich menu — proven safe tile by tile | 14 |
| §5.9 | sweeps and customer pushes; chat SLA; daily summary | 4 (chat SLA), 11 |
| §6.1 | "ร้านใช้แพ็กเกจอะไร" — ask-model before/after per sentence | 10 |
| §6.2 | refusal copy verbatim | 7, 8 |
| §7.1–7.3 | Sales / Technician / Customer OA behaviour | 7, 8 |
| §9 | admin: column + filter, select, confirm with counts, refusal (owner decision Q2), usage, top-up | 3 (Data), 13 |
| §10 | check-perms family + classification; check-parity plan section; check-routes `api_principal` | 15, 6 |
| §11.2 | limit−1/limit/limit+1, Plus 500, race, allowance−1/allowance, override 0 vs empty, Starter locked, 1 vs 2 steps, no plan payload | 3, 9, 4, 5 |
| §11.3 | migration a–e + downgrade + head; round trip; cache; audit; multi-step on Pro | 2, 3, 4 |
| §11.4 | Starter + Pro-unchanged scenarios | 15 (the 6th join: 3, on Postgres) |
| §11.5 | full gate, `--real`, ask-model, guide images looked at, tester section X | 15, 16 |
| §12 | migration 0039, backfill, assertion, head bump, migrate before data | 2, 16 |
| §13 | the owner's five decisions | Global Constraints; 2 (Q1), 3/13 (Q2), 1/9 (Q3), 9/12 (Q4), 5/7/12 (Q5) |

**Gaps not closed:**
- Guide pictures (§11.5): only `sales-members` gains the plan card. The locked page and the Starter AI box have no guide slot, and a new guide step would push the Sales help menu past 15 lines — they are checked by eye on DEV (X-3, X-4) instead of drawn.
- Enterprise → Pro → Enterprise for the external API (§11.3) is proven at unit level (`test_round21d_ext_api.py` over the resolver's payload) and by X-9 on DEV, not by an HTTP test through a live Data tier.

### 2. Rulings — where this plan departs from the spec's wording, and why

1. **The plan rides on the membership row, not on `authorization_context`** (spec §3.2 lists both). The membership is read on every request and not cached per person; `authorization_context` is cached per `(licence, person, channel)` — exactly the seam §3.2 warns about. Cost if wrong: one more field on one more payload.
2. **`/me/permissions` carries the plan but not usage** (§5.4). Usage is counted fresh by `GET …/plan`, which only the plan card and the members page load — one Data call fewer on every page open.
3. **API keys: reading and revoking stay open on every plan; only creating is the feature** (§5.5 says the chat verbs refuse; §3.4 says the page shows keys read-only). A downgraded owner must be able to see and cut off an outside system. Same on both surfaces; `ACCEPTED_PLAN` records it.
4. **Templates and API pages lock as a notice over a readable page** (`lockMode: "notice"`), the other locked pages are replaced by `<PlanLocked>` — §3.4's "read-only with the plan notice" for those two, §5.4's locked panel for the rest.
5. **"เชิญสมาชิก at the limit"** (§5.4): the dashboard mints no invites (chat does), so the reason line sits under the members page's invites heading.
6. **The admin's preview rides on the tenant GET for every other plan** instead of an Application `…/plan-preview?to=` route (§9): the confirm dialog needs the answer the moment the select changes, with no extra round trip. The Data route exists as specified.
7. **No new guide step** — the help menu is at 15 lines; the plan goes into the `members`, `api` and `ai-reports` steps.
8. **Daily Summary**: the 08:00 digest is follow-ups only; there is no job section to omit (§3.4, §5.9).
9. **Deleting a custom role, listing/previewing/archiving templates stay open** — cleanup is not customising.
10. **The English refusal message is `"<label>: included from the <plan> plan. This shop is on <plan>."`** — §5.2's example sentence does not agree in number for every label.
11. **Words the spec did not write:** the joiner's sentence when a shop is full, the owner's notices, the plan-read lines — each in `entitlements.py` / `chat.py`, both languages.
12. **Test fakes with no plan are Pro** (the fail-open value). A test exercising an Enterprise feature through such a fake is given `plan_payload("enterprise")`, never a weaker gate.
13. **The "ทีมช่าง" rich-menu tile** declined nothing on Starter; Task 14 adds a decline-only plan check inside `_maybe_handle_teams`' matched branches.
14. **Real-model coverage:** `scripts/agent-test/run.py` has no `--real` flag (the 21C plan's Step 2 asked for one). This round's real-model evidence is the per-sentence `ask-model.py` measurement (the only prompt change), `simulate-phrasings --real`, and `converse.py` on `smoke.json` pointed at this tree.

### 3. Placeholder scan and name consistency

- Searched for `TBD`, `TODO`, "implement later", "similar to Task", "fill in", "appropriate error handling": none. The two deliberate markers — `<วางผลจริง…>` in the handoff draft and `__21D_SHA__` in the handoff and the checklist — are filled by Task 16 (Step 3 greps the first; the deploy script's verify block refuses to ship it; the script's `sed` fills the second).
- Names used across tasks, checked against their defining task: `chann_data.plans.{PLANS, ROW_MAP, resolve, min_plan, smallest_plan_for, ai_allowance, PlanFeatureLocked, MemberLimitReached, PlanDowngradeRefused, UnknownPlan}` (1) · `PlanRepository.{payload, usage, require_feature, require_seat, check_change, preview, licences_without}` (3, 4) · `_plan_payload`, `_plan_refusal` in `internal.py` (3) · `k_license_plan` (3) · `entitlements.{PlanView, effective_keys, feature_of, feature_for_intent, plan_for, sales_contact, is_plan_refusal, SERVICE_BASIC_REPORTS, MEMBER_LIMIT_REACHED, TECH_*, CUSTOMER_OA_*, MEMBER_LIMIT_*, plan_change_text}` (5, 7, 8, 13) · `authorization.build_principal`, `TenantPrincipal.require_feature` (5) · `chat.{_PLAN, _plan_has, _plan_refusal, _plan_reply_from, _no_permission, _handle_plan_read}` (7, 10) · `FakeDataClient.{license_plan, _plan, _members_count}` (7) · `plan_fixtures.{plan_payload, plan_view}` (5) · `chart_quota.FAIL_OPEN_ALLOWANCE` (9) · `_plan.tsx: {PlanInfo, SalesContact, planHas, minPlanLabel, PlanLocked, PlanCard, UpgradeContact, PlanReason, LOCK_ICON, PLAN_LABELS}`, `_nav-model.lockedBy`, `SalesSession.{plan, salesContact}` (12) · the ten keys and four codes spelled identically in every task.
- Line numbers are as of `404cfe3` and each carries a `grep -n` anchor; later tasks move earlier lines — trust the anchor.
