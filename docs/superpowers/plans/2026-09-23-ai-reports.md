# AI reports that are right (round 21C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shop asks "ยอดมูลค่าดีลทั้งหมด" — in chat, on a button, or on a card — and gets its real number, from one definition of what a deal is worth; five everyday reports are answered by code that cannot be wrong; a paid invoice closes its deal and hands it the lines it was paid for; and a picture is designed by the model but computed by us.

**Architecture:** One SQL expression (`deal_value.py`) becomes the only definition of a deal's value and is used by the pipeline card, the Phase-17 report engine and the new basic reports alike. A new `BasicReportRepository` answers five fixed questions with no model in the number path; the model only classifies a sentence into one of five keys. `deals.closed_at` (migration `0038`) makes "won this month" a fact rather than a forecast. The invoice gains a line editor shaped like the quote's, and the payment that settles it writes its lines and total back onto the deal inside the same Data-tier transaction. Pictures move from hand-drawn Pillow to a validated "chart plan" → HTML/inline-SVG in the OA theme → SmartBrowz `preview_image` → the existing PNG/asset-link/LINE pipe, with Pillow kept as the fallback.

**Tech Stack:** FastAPI (Application + Data), SQLAlchemy 2 + Alembic, httpx `DataClient`, Next.js/React LIFF pages, Pillow (fallback charts), Zoho Catalyst SmartBrowz (screenshots), OpenRouter (the model), pytest (+ Postgres for `tests/integration`), the repo's `scripts/dev/check-*.py` gate.

**Spec:** `docs/superpowers/specs/2026-09-23-ai-reports-design.md`
(background and the proven root cause: `docs/superpowers/specs/2026-09-23-ai-reports-diagnosis.md`)

## Global Constraints

- **Arithmetic is never the model's** (`docs/MODEL_FIRST.md`, CLAUDE.md §-1 rule 6). The model reads and proposes; code validates, computes and acts. A model may choose a report or design a picture; it may never produce a number.
- **Model-first:** a sentence a person typed is read by the model before an action is chosen. A keyword may decline but may not act. Measure **per sentence, before and after**, with `scripts/dev/ask-model.py` — never on totals.
- **Deal value, one definition:** `COALESCE(deals.amount, SUM(deal_products.qty * deal_products.quoted_unit_price), 0)` — the typed amount wins; line items only when it is null.
- **The five basic reports never call the model for a number and never spend a credit.** Credits (`license_settings.ai_chart_quota`, default 30, `chart_quota.spend_one`) are spent for a model-designed picture and for an answered ad-hoc question.
- Tier boundary: Application code never imports `sqlalchemy`, `psycopg`, `redis`, `alembic` or `chann_data` (`tests/boundary/test_tier_boundaries.py`). Only `services/pdf/smartbrowz.py` may import `zcatalyst_sdk`.
- Every `routers_phase2.py` route whose path contains `{license_id}` must call `_require_same_tenant(principal, license_id)` and `principal.require(...)`/`require_any(...)`.
- Every `DataClient` call must map to an existing `/internal/v1/...` route with the right method (`scripts/dev/check-client.py`); every dashboard `/api/phase2/...` call must map to an Application `/api/v1/...` route (`check-routes.py`).
- Parity: anything reachable in chat is reachable on the dashboard and back. A deliberate one-sided capability goes in `ACCEPTED` in `scripts/dev/check-parity.py` **with a reason**. `ACCEPTED` is keyed `(entity, action)`; `ACTION_PERMISSIONS` (`chat.py:103`) is keyed `(action, entity)`.
- `audit_log.action` has a CHECK constraint. Allowed verbs, exactly: `create, update, delete, assign, transfer, cross_tenant_lookup, link_document, upsert, status, remove_product, claim, reject, check_in, check_out, pdpa_erasure, pdpa_export` (`data/chann_data/audit_actions.py`). **Never invent one** — the audit row shares the transaction with the change, so a rejected verb takes the change down with it.
- Migration ids ≤ 32 chars; the Data image boots only when `EXPECTED_MIGRATION_HEAD` in `data/chann_data/main.py` equals the alembic head. This round: `"0038_deal_closed_at"`, `down_revision = "0037_api_keys"`.
- OA theme colours: Sale `#178a50` · Tech `#1f6fd6` · CS `#e8731a` (`presentation/app/globals.css`, `[data-theme]`).
- Chart caps already enforced by `charts.py`: `MAX_BARS = 8`, `MAX_HBARS = 10`, `MAX_POINTS = 14`; kinds `bar | hbar | line | value`.
- Every dashboard change (buttons, placement, pages included) goes through the **ui-ux-pro-max** skill — owner's standing rule, 20 ก.ย. 2569.
- `python3 scripts/dev/render-guides.py` after any `guides.py` change; `python3 scripts/dev/render-guide-images.py` after any scene change, then **LOOK at the PNG**. Never edit a guide PNG by hand.
- Tests need `JWT_SECRET=test-jwt-secret`. Integration tests need `TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test` (container `pg20w`).
- **Never run the full unit suite without these two deselects** (they call the real Zoho endpoint):
  `--deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors"`
- Python for tests: `/tmp/dv/bin/python` (if `/tmp/dv` is gone after a Cloud Shell restart: `python3 -m venv /tmp/dv && /tmp/dv/bin/pip install -q -r data/requirements.txt -r application/requirements.txt -r requirements-test.txt pytest pytest-asyncio httpx`).
- Work in the worktree `~/stage-fix/r21c` (branch `r21c`, based on round 21B's tip `1c29bda`). Commit after each task. **21C ships after 21B is deployed.**
- `~/stage-fix/tools/gate.sh` and `~/stage-fix/tools/converse.py` default to `~/stage-fix/registry` — point them at `~/stage-fix/r21c` or they test the previous round.

---

## File map

| Area | File | Responsibility |
|---|---|---|
| Data | `data/chann_data/repositories/deal_value.py` | **new** — the one deal-value SQL expression + subquery helper |
| Data | `data/chann_data/repositories/phase9.py` | `pipeline_summary` uses it; `transition_stage` stamps/clears `closed_at`; `close_won_from_invoice` |
| Data | `data/chann_data/repositories/phase17.py` | deal-value expression, `IN` filters, `closed_at` date field |
| Data | `data/chann_data/repositories/basic_reports.py` | **new** — the five fixed reports |
| Data | `data/chann_data/repositories/invoices.py` | `_editable`, `update_lines`, `update_details`, settle → deal |
| Data | `data/chann_data/models.py` | `Deal.closed_at` |
| Data | `database/alembic/versions/0038_deal_closed_at.py` | column + index + backfill |
| Data | `data/chann_data/main.py` | `EXPECTED_MIGRATION_HEAD` |
| Data | `data/chann_data/schemas.py` | `ReportQueryIn.filter` widened, `BasicReport*`, `InvoiceLinesIn`, `InvoiceDetailsIn`, `InvoiceOut.needs_reissue` |
| Data | `data/chann_data/routers/internal.py` | basic-report route, invoice edit routes, settle audit rows |
| App | `application/chann_app/services/deal_value.py` | **new** — the same precedence for a deal dict |
| App | `application/chann_app/services/sales_charts.py` | `deal_value` delegates; `deal_closed_on` uses `closed_at` |
| App | `application/chann_app/services/reports_ai.py` | list filters, `closed_at`, prompt, `_filter_words` |
| App | `application/chann_app/services/basic_reports.py` | **new** — envelope → text/card, the model's `report_key` chooser |
| App | `application/chann_app/services/chart_plan.py` | **new** — chart-plan schema, validator, HTML/SVG renderer, publish |
| App | `application/chann_app/services/document_send.py` | **new** — hand a quote/invoice/receipt to the customer on LINE |
| App | `application/chann_app/services/pdf/__init__.py` | re-export (it is empty today, and that silently killed the report PDF) |
| App | `application/chann_app/services/invoices.py` | `edit_lines`, `edit_details`, re-issue flag |
| App | `application/chann_app/data_client.py` | `basic_report`, `update_invoice_lines`, `update_invoice_details` |
| App | `application/chann_app/routers_phase2.py` | basic-report route, invoice edit routes, `_charge_for_the_question` |
| App | `application/chann_app/services/chat.py` | the five reports, the picture button, invoice line editing, the settle sentence |
| App | `application/chann_app/services/guides.py` | the `ai-reports` step |
| Pres | `presentation/app/liff/sales/reports/ai/AiReports.tsx` | the five cards |
| Pres | `presentation/app/liff/sales/invoices/InvoiceList.tsx` | the line editor in the detail sheet |
| Pres | `presentation/lib/i18n/{th,en}.ts` | `dashboard.aiReports.basic.*`, `dashboard.invoices.edit*` |
| Tools | `scripts/dev/check-parity.py` | reason strings for `("report","create"/"read")` |
| Tools | `scripts/dev/render-guide-images.py` | `sales-ai-report` scene redrawn |
| Tools | `scripts/agent-test/scenarios/round21c-{reports,invoice-edit}.yaml` | the chat contract |
| Tools | `scripts/dev/probe-smartbrowz-screenshot.py` | **new** — does a screenshot wait for JavaScript? (Task 15) |
| Docs | `docs/SESSION_HANDOFF.md`, `~/CHECKLIST-หลัง-deploy.md` | handoff entry, tester checklist VI |
| Tests | `tests/unit/test_round21c_{deal_value,reports,invoice_edit,invoice_chat,invoice_ui,document_send,basic_reports,reports_chat,reports_ui,chart_plan}.py` | |
| Tests | `tests/integration/test_round21c_{deal_value,closed_at,invoice_edit,invoice_settle,basic_reports}.py` | |

---

### Task 1: One deal-value expression — the pipeline card, the AI report, and a test that pins them together

**Files:**
- Create: `data/chann_data/repositories/deal_value.py`
- Modify: `data/chann_data/repositories/phase9.py:890-906` (`pipeline_summary`)
- Modify: `data/chann_data/repositories/phase17.py:103-114, 212-241` (`NUMERIC_FIELDS`, `build_statement`)
- Test: `tests/integration/test_round21c_deal_value.py`

**Interfaces:**
- Produces: `chann_data.repositories.deal_value.DEAL_VALUE` (a SQLAlchemy expression) and `deal_value_subquery(*extra_columns)`; `ReportQueryRepository._apply_scope_and_filters(stmt, model, table, scope, spec, *, today=None)` (Task 3 widens its filter line to `IN`).

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21c_deal_value.py`:

```python
"""Round 21C — one definition of what a deal is worth, on a real database.

Owner, 23 ก.ย. 2569: the typed `amount` wins; the line items are the
fallback. The pipeline card and the AI report must return the SAME number,
which nothing has ever asserted (diagnosis §1f).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase17 import ReportQueryRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def shop(migrated_db):
    """Three deals: lines only (30,000) · amount only (250,000) · both,
    where the typed 99,000 must beat the 1,000 of lines."""
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CV{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(
            company_name=f"Deal value {tag}", created_by_chann_uid=uid)
        license_id = row.id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as s:
        customers, deals = CustomerRepository(s), DealRepository(s)
        # One open deal per customer (the Phase 9 duplicate guard), so each
        # case gets its own customer rather than closing the one before.
        a = customers.create(scope, first_name="ลูกค้า", last_name="ก", phone="0810000001")
        s.flush()
        lines_only = deals.create(scope, contact_id=a.id)
        s.flush()
        deals.add_product(scope, lines_only.id, product_id=None, product_name="แอร์ 12000 BTU",
                          quoted_unit_price=Decimal("15000.00"), qty=2)
        b = customers.create(scope, first_name="ลูกค้า", last_name="ข", phone="0810000002")
        s.flush()
        deals.create(scope, contact_id=b.id, amount=Decimal("250000.00"))
        s.flush()
        c = customers.create(scope, first_name="ลูกค้า", last_name="ค", phone="0810000003")
        s.flush()
        both = deals.create(scope, contact_id=c.id, amount=Decimal("99000.00"))
        s.flush()
        deals.add_product(scope, both.id, product_id=None, product_name="ค่าบริการติดตั้ง",
                          quoted_unit_price=Decimal("1000.00"), qty=1)
        s.commit()
    return migrated_db, scope


class TestOneDefinition:
    def test_the_typed_amount_wins_and_the_lines_are_the_fallback(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            summary = DealRepository(s).pipeline_summary(scope)
        # 30,000 + 250,000 + 99,000 — NOT 281,000, which is what summing the
        # line items first gives.
        assert Decimal(summary["by_stage"]["new"]["value"]) == Decimal("379000")
        assert Decimal(summary["open_value"]) == Decimal("379000")

    def test_the_ai_report_returns_the_same_number_as_the_pipeline_card(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            card = DealRepository(s).pipeline_summary(scope)
            report = ReportQueryRepository(s).run(
                scope, {"entity": "deals", "metric": "sum", "field": "amount"})
        assert Decimal(str(report["total"])) == Decimal(card["by_stage"]["new"]["value"])
        assert Decimal(str(report["total"])) == Decimal("379000")

    def test_grouping_by_stage_still_counts_deals_not_line_items(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            counted = ReportQueryRepository(s).run(
                scope, {"entity": "deals", "metric": "count", "group_by": "stage"})
        assert {row["key"]: row["value"] for row in counted["rows"]} == {"new": 3}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_deal_value.py -q
```
Expected: FAIL — the first test gets `281000` (lines first) and the second gets `0` (`sum(deals.amount)` over two NULLs and one 250,000 → actually `349000`; whatever it prints, it is not `379000`).

- [ ] **Step 3: Create the one expression**

`data/chann_data/repositories/deal_value.py`:

```python
"""The one definition of what a deal is worth (round 21C).

Owner, 23 ก.ย. 2569: the value a salesperson typed wins; the line items
are what a deal is worth when nobody typed one.

There were four implementations of this and two of them disagreed. The
pipeline card summed the line items first; the deal list and chat took the
typed amount first; and the AI report summed `deals.amount` alone, which
is NULL for every deal whose value lives in its lines — so it answered 0
(`docs/superpowers/specs/2026-09-23-ai-reports-diagnosis.md` §1).

The join to `deal_products` MULTIPLIES rows, so every user of this groups
by `Deal.id` and aggregates the subquery, never the joined rows: a count
taken over the join counts line items, not deals.
"""
from __future__ import annotations

from sqlalchemy import func, select

from ..models import Deal, DealProduct

#: The value of ONE deal, for a statement that has already outer-joined
#: `deal_products` and grouped by `Deal.id`. `amount` first, on purpose.
DEAL_VALUE = func.coalesce(
    Deal.amount, func.sum(DealProduct.quoted_unit_price * DealProduct.qty), 0,
)


def deal_value_subquery(*extra_columns):
    """One row per deal: `deal_id`, whatever else the caller asked for, and
    `value`. The caller adds its own WHERE and then aggregates."""
    return (
        select(Deal.id.label("deal_id"), *extra_columns, DEAL_VALUE.label("value"))
        .outerjoin(DealProduct, DealProduct.deal_id == Deal.id)
        .group_by(Deal.id, Deal.amount, *extra_columns)
    )
```

- [ ] **Step 4: Point `pipeline_summary` at it**

In `data/chann_data/repositories/phase9.py`, add to the imports at the top of the file:

```python
from .deal_value import deal_value_subquery
```

and replace the statement at `phase9.py:890-906` (keep everything after it — the loop, the buckets and the return — untouched):

```python
        rows = self._s.execute(
            # Round 21C: one definition of a deal's value, shared with the
            # AI report (`repositories/deal_value.py`). The typed amount
            # wins; the lines are the fallback.
            deal_value_subquery(Deal.stage, Deal.expected_close_date)
            .where(
                Deal.license_id == scope.license_id,
                Deal.archived_at.is_(None),
            )
        ).all()
```

- [ ] **Step 5: Point Phase 17's deal sum at it**

In `data/chann_data/repositories/phase17.py`, add `from .deal_value import deal_value_subquery` to the imports, and replace `NUMERIC_FIELDS["deals"]`'s entry so the whitelist still answers "is `amount` a numeric field of deals?" while the statement builder knows it is not a plain column:

```python
NUMERIC_FIELDS: dict[str, dict] = {
    # Round 21C: `amount` is the NAME the spec uses for "a deal's value",
    # not the column it sums. The column alone is NULL for every deal
    # whose value is in its line items, which is why this report answered
    # 0. `_deal_value_statement` below builds the real expression.
    "deals": {"amount": Deal.amount},
    ...
```

(unchanged — the dict stays as it is; the branch is in `build_statement`.)

Replace `build_statement` (`phase17.py:212-241`) with:

```python
    def build_statement(self, scope: TenantScope, spec: dict, *, today: date | None = None):
        """One SELECT from the whitelist, tenant-filtered. Public so a test
        can look at the SQL and see the license_id bind parameter."""
        spec = validate_spec(spec)
        table = ENTITIES[spec["entity"]]
        model = table["model"]
        group_col = table["fields"][spec["group_by"]] if spec["group_by"] else None
        if spec["entity"] == "deals" and spec["metric"] != "count":
            return self._deal_value_statement(scope, spec, table, group_col, today=today), spec
        if spec["metric"] == "count":
            measure = func.count()
        else:
            column = NUMERIC_FIELDS[spec["entity"]][spec["field"]]
            measure = getattr(func, spec["metric"])(column)
        stmt = select(measure.label("value"))
        if group_col is not None:
            stmt = select(group_col.label("key"), measure.label("value")).group_by(group_col).order_by(measure.desc())
        stmt = stmt.select_from(model)
        return self._apply_scope_and_filters(stmt, model, table, scope, spec, today=today), spec

    def _deal_value_statement(self, scope: TenantScope, spec: dict, table: dict, group_col, *, today=None):
        """sum/avg/min/max of a DEAL'S VALUE, which is not a column.

        One row per deal first (`deal_value_subquery`), then the aggregate.
        Aggregating the joined rows would multiply a deal by its number of
        line items; `count` never comes here for the same reason.
        """
        inner = deal_value_subquery(*([group_col] if group_col is not None else []))
        inner = self._apply_scope_and_filters(inner, Deal, table, scope, spec, today=today)
        sub = inner.subquery()
        measure = getattr(func, spec["metric"])(sub.c.value)
        if group_col is None:
            return select(measure.label("value")).select_from(sub)
        key = sub.c[group_col.key]
        return (
            select(key.label("key"), measure.label("value"))
            .select_from(sub).group_by(key).order_by(measure.desc())
        )

    def _apply_scope_and_filters(self, stmt, model, table: dict, scope: TenantScope, spec: dict, *, today=None):
        """The three rules every report obeys: this tenant only, no
        archived rows, and the spec's own filters and date window."""
        stmt = stmt.where(model.license_id == scope.license_id)
        if hasattr(model, "archived_at"):
            # Archived leads and deals are not in any list; they were still
            # counted in "ลูกค้าใหม่เดือนนี้" (review, 6 Sep 2026).
            stmt = stmt.where(model.archived_at.is_(None))
        for key, value in spec["filter"].items():
            stmt = stmt.where(table["fields"][key] == value)
        if spec["date_range"]:
            start, end = date_window(spec["date_range"], today=today)
            column = table["fields"][spec["date_field"]]
            if isinstance(column.type.python_type, type) and column.type.python_type is date:
                stmt = stmt.where(column >= start.astimezone(BANGKOK).date(), column < end.astimezone(BANGKOK).date())
            else:
                stmt = stmt.where(column >= start, column < end)
        return stmt
```

- [ ] **Step 6: Run the new test, then everything that touched the old order**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_deal_value.py -q
```
Expected: 3 passed. Then the existing pins:
```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_ai_reports_data.py tests/integration/test_round20o_counts_on_a_real_database.py -q
grep -rln "pipeline_summary" tests/ | tee /tmp/21c-pipeline-tests.txt
JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest $(cat /tmp/21c-pipeline-tests.txt | tr '\n' ' ') -q
```
**A test that fails here is information, not an obstacle** (round 20K): read what it pinned. A test that asserted lines-beat-amount is pinning the behaviour the owner just changed — update it and say so in its docstring. A test that fails for any other reason is a real regression.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "fix(round21c): one definition of what a deal is worth, and the AI report uses it" && git log --oneline -1
```

---

### Task 2: The same precedence in the Application tier and on the deal list

**Files:**
- Create: `application/chann_app/services/deal_value.py`
- Modify: `application/chann_app/services/sales_charts.py:126-138` (`deal_value`)
- Modify: `application/chann_app/services/chat.py:18618-18635` (`_deal_value`)
- Modify: `presentation/app/liff/sales/deals/DealList.tsx:39-45` (`dealValue`)
- Test: `tests/unit/test_round21c_deal_value.py`

**Interfaces:**
- Consumes: nothing from Task 1 (this is the dict-shaped twin of the same rule).
- Produces: `chann_app.services.deal_value.deal_value(deal: dict) -> Decimal`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_deal_value.py`:

```python
"""Round 21C — a deal dict is worth what the Data tier says it is worth."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.deal_value import deal_value  # noqa: E402


class TestPrecedence:
    def test_the_typed_amount_wins_over_the_lines(self):
        deal = {"amount": "99000.00",
                "products": [{"qty": 1, "quoted_unit_price": "1000.00"}]}
        assert deal_value(deal) == Decimal("99000.00")

    def test_lines_are_used_when_no_amount_was_typed(self):
        deal = {"amount": None,
                "products": [{"qty": 2, "quoted_unit_price": "15000.00"}]}
        assert deal_value(deal) == Decimal("30000.00")

    def test_a_typed_zero_is_a_typed_value_not_a_missing_one(self):
        deal = {"amount": "0",
                "products": [{"qty": 1, "quoted_unit_price": "500.00"}]}
        assert deal_value(deal) == Decimal("0")

    def test_neither_is_zero(self):
        assert deal_value({"amount": None, "products": []}) == Decimal("0")

    def test_rubbish_does_not_raise(self):
        assert deal_value({"amount": "หมื่นห้า", "products": []}) == Decimal("0")


class TestTheOtherTwoRoadsDelegate:
    def test_sales_charts_and_chat_use_the_same_helper(self):
        from chann_app.services import sales_charts
        from chann_app.services import chat

        deal = {"amount": "99000.00", "products": [{"qty": 1, "quoted_unit_price": "1000.00"}]}
        assert sales_charts.deal_value(deal) == Decimal("99000.00")
        assert chat._deal_value(deal) == Decimal("99000.00")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_deal_value.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'chann_app.services.deal_value'`.

- [ ] **Step 3: Write the helper**

`application/chann_app/services/deal_value.py`:

```python
"""What a deal is worth, given a deal dict (round 21C).

The Data tier's `repositories/deal_value.py` is the same rule in SQL. This
one exists because the Application tier holds deals as dicts from the
DataClient, and three places were each deciding for themselves what a deal
was worth — two of them differently from the pipeline card.

`amount` is nullable. NULL and "" mean nobody typed a value; 0 means
somebody typed zero, and a typed zero is an answer.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def deal_value(deal: dict) -> Decimal:
    """The typed amount when there is one, otherwise the line items."""
    typed = deal.get("amount")
    if typed is not None and str(typed).strip() != "":
        return _decimal(typed)
    total = Decimal("0")
    for row in deal.get("products") or []:
        total += _decimal(row.get("quoted_unit_price")) * int(row.get("qty") or 0)
    return total
```

- [ ] **Step 4: Make the other two delegate**

`application/chann_app/services/sales_charts.py` — replace the body of `deal_value` (:126-138) with:

```python
def deal_value(deal: dict) -> Decimal:
    """What one deal is worth. Round 21C: one definition, in
    `services/deal_value.py`, matching the Data tier's SQL — a chart that
    valued deals differently from the card on the dashboard would be a
    second, quieter source of truth, and for a year it was."""
    from .deal_value import deal_value as _shared

    return _shared(deal)
```

`application/chann_app/services/chat.py` — replace the body of `_deal_value` (:18618-18635) with:

```python
def _deal_value(deal: dict) -> Decimal:
    """What a deal is worth for a query ("ดีลเกิน 1 หมื่น", "ดีลใหญ่สุด").
    Round 21C: the one definition, shared with the pipeline card and the
    AI report (`services/deal_value.py`)."""
    from .deal_value import deal_value as _shared

    return _shared(deal)
```

- [ ] **Step 5: Fix the deal list's typed zero**

`presentation/app/liff/sales/deals/DealList.tsx:39-45`:

```tsx
/** The deal's own amount when the salesperson gave one; otherwise the line
 *  items. Round 21C: a typed 0 is a typed value — only a missing one falls
 *  through to the lines, which is what the server now does too. */
function dealValue(deal: Deal): number {
  if (deal.amount != null && deal.amount !== "") return Number(deal.amount) || 0;
  return (deal.products ?? []).reduce(
    (sum, p) => sum + Number(p.qty ?? 0) * Number(p.quoted_unit_price ?? 0), 0,
  );
}
```

- [ ] **Step 6: Run the tests and the chart/chat suites**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_deal_value.py tests/unit/test_sales_charts.py -q
cd ~/stage-fix/r21c/presentation && npm run typecheck
```
Expected: all passed; typecheck clean. If `tests/unit/test_sales_charts.py` does not exist under that name: `ls tests/unit | grep -i chart`.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "fix(round21c): chat, the sales charts and the deal list read a deal's value the same way" && git log --oneline -1
```

---

### Task 3: Filters that hold more than one value (`IN`)

**Files:**
- Modify: `data/chann_data/repositories/phase17.py:173-191` (`validate_spec` filter loop), `:~235` (the filter line added in Task 1)
- Modify: `data/chann_data/schemas.py:1483-1490` (`ReportQueryIn.filter`)
- Modify: `application/chann_app/services/reports_ai.py:138-143` (`_filter_words`), `:197-212` (`validate_query_spec` filter loop), `:263-295` (`_whitelist_text` / `build_system_prompt`)
- Test: `tests/unit/test_round21c_reports.py`, `tests/integration/test_round21c_basic_reports.py` (created here, extended in Task 11)

**Interfaces:**
- Consumes: `ReportQueryRepository._apply_scope_and_filters` (Task 1).
- Produces: a spec whose `filter` values are `str | list[str]`; both validators return lists unchanged and de-duplicated.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_round21c_reports.py`:

```python
"""Round 21C — a report filter may name more than one value.

The prompt has promised this since round 20V ("งานค้าง = status open or
assigned or in_progress", "ยอดค้าง = issued or partially_paid") while the
engine could only ever compare to one, so both answers silently
undercounted (diagnosis §1, "two more defects").
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))
sys.path.insert(0, str(ROOT / "data"))

from chann_app.services import reports_ai  # noqa: E402
from chann_data.repositories.phase17 import ReportSpecInvalid, validate_spec  # noqa: E402


class TestTheDataTierValidator:
    def test_a_list_of_statuses_is_kept_as_a_list(self):
        spec = validate_spec({"entity": "tickets", "metric": "count",
                              "filter": {"status": ["open", "assigned", "in_progress"]}})
        assert spec["filter"] == {"status": ["open", "assigned", "in_progress"]}

    def test_one_value_is_still_a_scalar(self):
        spec = validate_spec({"entity": "tickets", "metric": "count",
                              "filter": {"status": "open"}})
        assert spec["filter"] == {"status": "open"}

    def test_duplicates_collapse_and_order_is_kept(self):
        spec = validate_spec({"entity": "invoices", "metric": "sum", "field": "outstanding",
                              "filter": {"status": ["issued", "issued", "partially_paid"]}})
        assert spec["filter"] == {"status": ["issued", "partially_paid"]}

    def test_a_bad_value_inside_a_good_list_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="is not a valid status"):
            validate_spec({"entity": "tickets", "metric": "count",
                           "filter": {"status": ["open", "จบแล้ว"]}})

    def test_an_empty_list_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="at least one value"):
            validate_spec({"entity": "tickets", "metric": "count", "filter": {"status": []}})

    def test_more_than_ten_values_is_refused(self):
        with pytest.raises(ReportSpecInvalid, match="at most 10"):
            validate_spec({"entity": "tickets", "metric": "count",
                           "filter": {"status": ["open"] * 11}})


class TestTheApplicationValidatorAgrees:
    @pytest.mark.parametrize("spec", [
        {"entity": "tickets", "metric": "count", "filter": {"status": ["open", "assigned", "in_progress"]}},
        {"entity": "invoices", "metric": "sum", "field": "outstanding",
         "filter": {"status": ["issued", "partially_paid"]}},
    ])
    def test_both_validators_accept_the_same_spec(self, spec):
        assert reports_ai.validate_query_spec(dict(spec))["filter"] == validate_spec(dict(spec))["filter"]

    def test_the_prompt_tells_the_model_it_may_send_a_list(self):
        prompt = reports_ai.build_system_prompt()
        assert '["issued","partially_paid"]' in prompt.replace(" ", "")
        assert '["open","assigned","in_progress"]' in prompt.replace(" ", "")

    def test_the_header_names_every_value_that_was_counted(self):
        words = reports_ai._filter_words("status", ["issued", "partially_paid"], "th")
        assert "·" in words and "ออกแล้ว" in words
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_reports.py -q
```
Expected: FAIL — `ReportSpecInvalid: '['open', 'assigned', 'in_progress']' is not a valid status` (the list is stringified by `str(value).strip()`).

- [ ] **Step 3: Widen the Data-tier validator**

In `data/chann_data/repositories/phase17.py`, replace the filter loop inside `validate_spec` (:176-191) with:

```python
    filters: dict[str, str | list[str]] = {}
    for key, value in filters_in.items():
        if key not in table["fields"] or key in table["date_fields"]:
            raise ReportSpecInvalid(f"field '{key}' cannot be filtered on {entity}")
        # Round 21C: a filter may name several values ("งานค้าง" is open OR
        # assigned OR in_progress). A single value stays a single value, so
        # every statement built before this change is built the same way.
        many = isinstance(value, (list, tuple))
        values = list(value) if many else [value]
        if not values:
            raise ReportSpecInvalid(f"filter '{key}' needs at least one value")
        if len(values) > 10:
            raise ReportSpecInvalid(f"filter '{key}' may name at most 10 values")
        cleaned: list[str] = []
        for one in values:
            one = str(one).strip()
            if key in table["enums"]:
                if one not in table["enums"][key]:
                    raise ReportSpecInvalid(f"'{one}' is not a valid {key}")
            elif key in ID_FIELDS:
                try:
                    one = str(uuid.UUID(one))
                except ValueError:
                    raise ReportSpecInvalid(f"{key} must be an id")
            elif not _VALUE_RE.match(one):
                raise ReportSpecInvalid(f"'{one}' is not an acceptable value for {key}")
            if one not in cleaned:
                cleaned.append(one)
        filters[key] = cleaned if many else cleaned[0]
```

- [ ] **Step 4: Emit `IN` when there are several**

In `_apply_scope_and_filters` (added in Task 1), replace the one filter line:

```python
        for key, value in spec["filter"].items():
            column = table["fields"][key]
            # A list becomes IN; a single value stays `==`, so no statement
            # that worked before this round is rewritten as a one-element IN.
            stmt = stmt.where(column.in_(value) if isinstance(value, list) else column == value)
```

- [ ] **Step 5: Widen the schema and the Application validator**

`data/chann_data/schemas.py:1487`:

```python
    filter: dict[str, str | list[str]] = {}
```

`application/chann_app/services/reports_ai.py` — replace its filter loop (:197-212) with the **same** code as Step 3 (this module is the Application-side copy of the whitelist, and the two are kept in step by `TestTheApplicationValidatorAgrees`), and replace `_filter_words` (:138-143) — which today is

```python
def _filter_words(key: str, value: str, language: str) -> str:
    """"สถานะ ปิดสำเร็จ", never "stage=won"."""
    name = _t(GROUP_LABEL.get(key) or FIELD_LABEL.get(key) or {"th": key}, language)
    words = VALUE_LABEL.get(key, {}).get(value)
    return f"{name} {_t(words, language) if words else value}"
```

with

```python
def _filter_words(key: str, value, language: str) -> str:
    """"สถานะ ออกแล้ว รอชำระ · ชำระบางส่วน", never "status=issued".

    Round 21C: a filter may hold several values, and the header says which
    ones were counted — "ยอดค้างชำระ" that quietly means only `issued` is
    exactly the bug this round is fixing."""
    name = _t(GROUP_LABEL.get(key) or FIELD_LABEL.get(key) or {"th": key}, language)
    values = value if isinstance(value, list) else [value]
    said = []
    for one in values:
        words = VALUE_LABEL.get(key, {}).get(one)
        said.append(_t(words, language) if words else str(one))
    return f"{name} {' · '.join(said)}"
```

- [ ] **Step 6: Tell the model**

In `reports_ai.build_system_prompt()`, the Thai hints currently say *"งานค้าง = status open or assigned or in_progress (pick 'open' if they say ค้าง and no other clue, or omit the status filter and group by status)"* — a workaround for the engine's limit, which now goes. Replace that clause with:

```python
        "งานค้าง = filter status [\"open\",\"assigned\",\"in_progress\"] (ส่งเป็นลิสต์ ไม่ต้องเลือกค่าเดียว); "
```

and the invoice clause's *"ยอดค้าง = sum of outstanding with filter status issued or partially_paid"* with:

```python
        "ยอดค้าง = sum of outstanding with filter status [\"issued\",\"partially_paid\"]; "
```

and add one line to the JSON instructions, after the example object:

```python
        'filter รับได้ทั้งค่าเดียวและลิสต์: {"status": ["issued","partially_paid"]} · ค่าต้องอยู่ในรายการข้างบนเท่านั้น สูงสุด 10 ค่า\n'
```

- [ ] **Step 7: Run the unit tests and the whole report suite**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_reports.py tests/unit/test_ai_reports.py -q
TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_ai_reports_data.py -q
```
Expected: all passed.

- [ ] **Step 8: Ask the real model, and record what it said**

```bash
cd ~/stage-fix/r21c && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py \
  "งานค้างแยกตามช่าง" "ยอดค้างชำระตอนนี้เท่าไหร่" "ใบแจ้งหนี้ที่ยังไม่ได้เงินเดือนนี้"
```
Paste the three answers into the handoff entry (Task 17). If the model still sends one status, the prompt line is wrong — fix the prompt, not the validator (`docs/MODEL_FIRST.md` step 2).

- [ ] **Step 9: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "fix(round21c): a report filter may name several statuses, so งานค้าง and ยอดค้างชำระ stop undercounting" && git log --oneline -1
```

---

### Task 4: `deals.closed_at` — migration 0038, stamped on close, cleared on reopen, backfilled

**Files:**
- Create: `database/alembic/versions/0038_deal_closed_at.py`
- Modify: `data/chann_data/models.py:1034` (after `archived_at` on `Deal`)
- Modify: `data/chann_data/main.py:25` (`EXPECTED_MIGRATION_HEAD`)
- Modify: `data/chann_data/repositories/phase9.py:985-1018` (`transition_stage`)
- Modify: `data/chann_data/schemas.py:1009-1026` (`DealOut`), `data/chann_data/routers/internal.py:2662-2680` (`_deal_out`)
- Modify: `data/chann_data/repositories/phase17.py:32-39` (deals entity), `application/chann_app/services/reports_ai.py:39-40` (deals entity)
- Modify: `application/chann_app/services/sales_charts.py:166-172` (`deal_closed_on`)
- Test: `tests/integration/test_round21c_closed_at.py`

**Interfaces:**
- Produces: `Deal.closed_at: Mapped[datetime | None]`; `DealOut.closed_at`; `phase17.ENTITIES["deals"]["fields"]["closed_at"]` and `date_fields` containing `"closed_at"`; the alembic head `"0038_deal_closed_at"`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21c_closed_at.py`:

```python
"""Round 21C — a deal records WHEN it closed.

Until now "ปิดสำเร็จเดือนนี้" could only be approximated from
`expected_close_date ?? created_at` — a forecast date, not a close date
(diagnosis §1, "two more defects").
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Deal
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope


@pytest.fixture
def shop(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CC{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        row = RegistrationRepository(s).create_license(
            company_name=f"Closed at {tag}", created_by_chann_uid=uid)
        license_id = row.id
        s.commit()
    return migrated_db, TenantScope(license_id=license_id)


class TestTheColumn:
    def test_migration_adds_closed_at_and_its_index(self, migrated_db):
        columns = {c["name"] for c in inspect(migrated_db).get_columns("deals")}
        assert "closed_at" in columns
        indexes = {i["name"] for i in inspect(migrated_db).get_indexes("deals")}
        assert "ix_deals_closed_at" in indexes

    def test_the_head_the_data_image_expects_is_this_one(self, migrated_db):
        from chann_data.main import EXPECTED_MIGRATION_HEAD

        with migrated_db.begin() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == EXPECTED_MIGRATION_HEAD == "0038_deal_closed_at"


class TestWhenItIsStamped:
    def test_won_stamps_it_and_reopening_clears_it(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="ปิด", phone="0820000001")
            s.flush()
            deals = DealRepository(s)
            deal = deals.create(scope, contact_id=customer.id)
            s.flush()
            assert deal.closed_at is None
            deals.transition_stage(scope, deal.id, to_stage="proposed", allow_reopen=False)
            assert deal.closed_at is None
            deals.transition_stage(scope, deal.id, to_stage="won", allow_reopen=False)
            assert deal.closed_at is not None
            deals.transition_stage(scope, deal.id, to_stage="new", allow_reopen=True)
            assert deal.closed_at is None

    def test_lost_stamps_it_too(self, shop):
        engine, scope = shop
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="แพ้", phone="0820000002")
            s.flush()
            deals = DealRepository(s)
            deal = deals.create(scope, contact_id=customer.id)
            s.flush()
            deals.transition_stage(scope, deal.id, to_stage="lost", allow_reopen=False,
                                   lost_reason="ราคาสูงกว่าคู่แข่ง")
            assert deal.closed_at is not None


class TestTheBackfill:
    def test_a_deal_closed_before_the_column_existed_gets_its_updated_at(self, shop):
        """The migration's UPDATE, re-run against a row that looks like an
        old one: closed, with no closed_at. It is an approximation, and the
        report says so in words (spec §4)."""
        engine, scope = shop
        stamp = datetime.now(timezone.utc) - timedelta(days=40)
        with Session(engine) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="เก่า", phone="0820000003")
            s.flush()
            deal = DealRepository(s).create(scope, contact_id=customer.id)
            s.flush()
            deal.stage = "won"
            deal.closed_at = None
            deal.updated_at = stamp
            s.commit()
            deal_id = deal.id
        with Session(engine) as s:
            s.execute(text(
                "UPDATE deals SET closed_at = updated_at "
                "WHERE stage IN ('won','lost') AND closed_at IS NULL"))
            s.commit()
        with Session(engine) as s:
            assert s.get(Deal, deal_id).closed_at is not None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_closed_at.py -q
```
Expected: FAIL — `AssertionError: 'closed_at' not in {...}`.

- [ ] **Step 3: The column on the model**

`data/chann_data/models.py`, immediately after `archived_at` on `Deal` (:1034):

```python
    # Round 21C — WHEN the deal closed. Until this column existed, "ปิดสำเร็จ
    # เดือนนี้" was answered from expected_close_date, which is a forecast:
    # a deal closed in September with a July forecast counted as July.
    # Stamped by transition_stage (won/lost) and by the invoice that
    # settles the deal; cleared when the deal is reopened, because a deal
    # sitting in "new" that still says when it closed is a lie the reports
    # would believe.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: The migration**

`database/alembic/versions/0038_deal_closed_at.py`:

```python
"""A deal records when it closed.

Owner, 23 ก.ย. 2569: "ยอดปิดสำเร็จเดือนนี้ เทียบเดือนที่แล้ว" — which the
database could not answer, because the only dates a deal had were the day
it was created and the day someone GUESSED it would close. Reports built
on the forecast put a September win in July.

Rows closed before this column existed are backfilled from `updated_at`:
the closest honest approximation there is, and the report says so in words
rather than pretending it is a real close date.

Revision ID: 0038_deal_closed_at
Revises: 0037_api_keys
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_deal_closed_at"
down_revision = "0037_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    # (license_id, closed_at): every report that reads it is one shop's
    # month, never every shop's.
    op.create_index("ix_deals_closed_at", "deals", ["license_id", "closed_at"])
    op.execute(
        "UPDATE deals SET closed_at = updated_at "
        "WHERE stage IN ('won', 'lost') AND closed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_deals_closed_at", table_name="deals")
    op.drop_column("deals", "closed_at")
```

`data/chann_data/main.py:25`:

```python
EXPECTED_MIGRATION_HEAD = "0038_deal_closed_at"
```

- [ ] **Step 5: Stamp it and clear it in the one place stages change**

`data/chann_data/repositories/phase9.py`, inside `transition_stage`, replacing the final three lines (`deal.stage = to_stage` / `self._s.flush()` / `return deal`):

```python
        deal.stage = to_stage
        # Round 21C: the close date, kept where the stage changes so the two
        # can never disagree. A reopen clears it for the same reason the
        # lost_reason above is cleared — it describes something that no
        # longer happened.
        deal.closed_at = datetime.now(timezone.utc) if to_stage in ("won", "lost") else None
        self._s.flush()
        return deal
```

(`datetime` and `timezone` are already imported at the top of `phase9.py` — `archive` uses them. Confirm with `grep -n "^from datetime" data/chann_data/repositories/phase9.py`.)

- [ ] **Step 6: Carry it across the tier seam**

`data/chann_data/schemas.py`, in `DealOut` after `lost_reason`:

```python
    closed_at: datetime | None = None
```

`data/chann_data/routers/internal.py`, in `_deal_out` after `lost_reason=deal.lost_reason,`:

```python
        closed_at=deal.closed_at,
```

(Both, or the column exists and the dashboard never sees it — the `Warranty.purchase_date` lesson; `scripts/dev/check-fields.py` is what catches it.)

- [ ] **Step 7: Let a report ask for it**

`data/chann_data/repositories/phase17.py`, the `deals` entity:

```python
    "deals": {
        "model": Deal,
        "fields": {
            "stage": Deal.stage, "owner_member_id": Deal.owner_member_id,
            "created_at": Deal.created_at, "expected_close_date": Deal.expected_close_date,
            # Round 21C: the real close date. A date field, never a filter
            # value — validate_spec refuses a filter on anything in
            # date_fields.
            "closed_at": Deal.closed_at,
        },
        "enums": {"stage": DEAL_STAGES},
        "date_fields": ("created_at", "expected_close_date", "closed_at"),
    },
```

`application/chann_app/services/reports_ai.py`, the matching entry (the Application-side copy of the same whitelist):

```python
    "deals": {"fields": ("stage", "owner_member_id", "created_at", "expected_close_date", "closed_at"),
              "enums": {"stage": ("new", "proposed", "won", "lost")},
              "date_fields": ("created_at", "expected_close_date", "closed_at")},
```

and in `build_system_prompt`'s Thai hints, after "ปิดสำเร็จ/ชนะ = stage won":

```python
        "ปิดเมื่อไหร่/ปิดได้เดือนนี้ = date_field closed_at (วันที่ปิดจริง ไม่ใช่วันคาดว่าจะปิด); "
```

- [ ] **Step 8: The charts stop guessing**

`application/chann_app/services/sales_charts.py:166-172`:

```python
def deal_closed_on(deal: dict) -> date | None:
    """When a deal closed. Round 21C: the real date when the row has one;
    the forecast only for deals closed before `closed_at` existed, which
    the migration backfilled from `updated_at` anyway — so this falls
    through only for a deal that is not closed at all."""
    return (
        _as_date(deal.get("closed_at"))
        or _as_date(deal.get("expected_close_date"))
        or _as_date(deal.get("created_at"))
    )
```

- [ ] **Step 9: Run the tests**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_closed_at.py -q
JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit tests/boundary -q -p no:cacheprovider \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors" | tail -3
/tmp/dv/bin/python scripts/dev/check-fields.py | tail -2
```
Expected: integration passed; unit/boundary passed; `check-fields` clean.

- [ ] **Step 10: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): a deal records when it closed, and reports read that instead of a forecast" && git log --oneline -1
```

---

### Task 5: An invoice can be edited — Data tier

**Files:**
- Modify: `data/chann_data/repositories/invoices.py` (add `_editable`, `update_lines`, `update_details`)
- Modify: `data/chann_data/schemas.py` (`InvoiceLinesIn`, `InvoiceDetailsIn`, `InvoiceOut.needs_reissue`)
- Modify: `data/chann_data/routers/internal.py` (two routes + `_invoice_out`)
- Test: `tests/integration/test_round21c_invoice_edit.py`

**Interfaces:**
- Produces:
  - `InvoiceRepository.update_lines(scope, invoice_id, *, data_snapshot: dict, subtotal, discount_amount, vat_rate, vat_amount, total) -> Invoice`
  - `InvoiceRepository.update_details(scope, invoice_id, *, note=None, due_date=None) -> Invoice`
  - `InvoiceOut.needs_reissue: bool` (read from `data_snapshot["needs_reissue_at"]`)
  - internal routes `PATCH /licenses/{license_id}/invoices/{invoice_id}/lines` and `PATCH /licenses/{license_id}/invoices/{invoice_id}`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21c_invoice_edit.py`:

```python
"""Round 21C — an invoice's lines and details can be corrected.

Owner, 23 ก.ย. 2569: "invoice ต้องแก้ไขรายการสินค้าหรือข้อมูลต่างๆได้เหมือน
quote ด้วย". A quote is editable while it is `draft`; an invoice is
editable until money has touched it (spec §3.3).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity
from chann_data.repositories.invoices import InvoiceConflict, InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

SNAPSHOT = {
    "line_items": [
        {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
         "unit_price": "15000.00", "line_total": "30000.00", "notes": None},
    ],
    "totals": {"subtotal": "30000.00", "discount_amount": "0", "vat_rate": "0.07",
               "vat_amount": "2100.00", "grand_total": "32100.00"},
}


@pytest.fixture
def invoice(migrated_db):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CE{tag}"
    with Session(migrated_db) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(migrated_db) as s:
        license_id = RegistrationRepository(s).create_license(
            company_name=f"Invoice edit {tag}", created_by_chann_uid=uid).id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(migrated_db) as s:
        customer = CustomerRepository(s).create(
            scope, first_name="ลูกค้า", last_name="แก้", phone="0830000001")
        s.flush()
        deal = DealRepository(s).create(scope, contact_id=customer.id)
        s.flush()
        row = InvoiceRepository(s).create(
            scope, deal_id=deal.id, contact_id=customer.id,
            subtotal=Decimal("30000.00"), vat_rate=Decimal("0.07"),
            vat_amount=Decimal("2100.00"), total=Decimal("32100.00"),
            data_snapshot=SNAPSHOT, created_by=uid)
        s.commit()
        invoice_id = row.id
    return migrated_db, scope, invoice_id


class TestEditingADraft:
    def test_lines_and_totals_are_replaced_together(self, invoice):
        engine, scope, invoice_id = invoice
        changed = {**SNAPSHOT, "line_items": [
            {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 3,
             "unit_price": "15000.00", "line_total": "45000.00", "notes": None}]}
        with Session(engine) as s:
            row = InvoiceRepository(s).update_lines(
                scope, invoice_id, data_snapshot=changed,
                subtotal=Decimal("45000.00"), discount_amount=Decimal("0"),
                vat_rate=Decimal("0.07"), vat_amount=Decimal("3150.00"),
                total=Decimal("48150.00"))
            s.commit()
            assert row.total == Decimal("48150.00")
            assert row.data_snapshot["line_items"][0]["qty"] == 3

    def test_details_are_editable_too(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            row = InvoiceRepository(s).update_details(
                scope, invoice_id, note="ชำระภายใน 15 วัน", due_date=date(2026, 10, 8))
            s.commit()
            assert row.note == "ชำระภายใน 15 วัน"
            assert row.due_date == date(2026, 10, 8)


class TestTheLock:
    def test_an_edit_after_issue_marks_the_invoice_as_needing_reissue(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.issue(scope, invoice_id, issue_date=date(2026, 9, 23))
            row = repo.update_details(scope, invoice_id, note="แก้หลังออกแล้ว")
            s.commit()
            assert row.data_snapshot.get("needs_reissue_at")

    def test_money_received_locks_it(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.issue(scope, invoice_id, issue_date=date(2026, 9, 23))
            repo.add_payment(scope, invoice_id, amount=Decimal("1000.00"), method="cash")
            s.commit()
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="has a payment"):
                InvoiceRepository(s).update_details(scope, invoice_id, note="สายไป")

    def test_a_void_invoice_cannot_be_edited(self, invoice):
        engine, scope, invoice_id = invoice
        with Session(engine) as s:
            repo = InvoiceRepository(s)
            repo.void(scope, invoice_id)
            s.commit()
        with Session(engine) as s:
            with pytest.raises(InvoiceConflict, match="void"):
                InvoiceRepository(s).update_details(scope, invoice_id, note="สายไป")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_invoice_edit.py -q
```
Expected: FAIL — `AttributeError: 'InvoiceRepository' object has no attribute 'update_lines'`.

- [ ] **Step 3: The guard and the two writers**

In `data/chann_data/repositories/invoices.py`, after `get_by_code` (:186-192), add:

```python
    def _editable(self, scope: TenantScope, invoice_id: uuid.UUID) -> Invoice:
        """An invoice is editable until money has touched it.

        A quote uses the same shape with a stricter threshold —
        `phase10._editable` refuses anything that is not `draft` — because
        a sent quote can be superseded for nothing, while an invoice
        carries a number the shop's books depend on. Here the line is
        drawn at the first payment: after that the road is void + re-raise
        (owner, 23 ก.ย. 2569, and spec §3.3).
        """
        row = self.get(scope, invoice_id)
        if row is None:
            raise InvoiceNotFound("invoice not found in this tenant")
        if row.status == "void":
            raise InvoiceConflict(f"invoice {row.invoice_id} is void and cannot be edited")
        if Decimal(str(row.paid_amount)) > 0:
            raise InvoiceConflict(
                f"invoice {row.invoice_id} has a payment recorded and can no longer be edited"
            )
        return row

    def _mark_needing_reissue(self, row: Invoice) -> None:
        """An edit after the PDF was made. The document in the customer's
        hand no longer matches the row, so the invoice says so until it is
        re-issued under the same number. Kept inside the snapshot — the
        one document of record — rather than in a column of its own."""
        if row.generated_document_id is None:
            return
        snapshot = dict(row.data_snapshot or {})
        snapshot["needs_reissue_at"] = datetime.now(timezone.utc).isoformat()
        row.data_snapshot = snapshot

    def update_lines(
        self, scope: TenantScope, invoice_id: uuid.UUID, *, data_snapshot: dict,
        subtotal, discount_amount=0, vat_rate=None, vat_amount=0, total,
    ) -> Invoice:
        """New lines and the totals computed from them, in one write.

        The Application tier recomputes with the same `compute_totals` the
        quote and this invoice were built with; this method never derives
        money from the snapshot itself, so there is exactly one piece of
        arithmetic and it is not here (round 20K).
        """
        row = self._editable(scope, invoice_id)
        if not (data_snapshot or {}).get("line_items"):
            raise InvoiceConflict("an invoice needs at least one line")
        row.data_snapshot = dict(data_snapshot)
        row.subtotal = _money(subtotal)
        row.discount_amount = _money(discount_amount)
        row.vat_rate = None if vat_rate is None else Decimal(str(vat_rate))
        row.vat_amount = _money(vat_amount)
        row.total = _money(total)
        self._mark_needing_reissue(row)
        self._s.flush()
        return row

    def update_details(
        self, scope: TenantScope, invoice_id: uuid.UUID, *,
        note: str | None = None, due_date: date | None = None,
    ) -> Invoice:
        """The details that are not money: the note and when it is due."""
        row = self._editable(scope, invoice_id)
        if note is not None:
            row.note = note.strip() or None
        if due_date is not None:
            row.due_date = due_date
        self._mark_needing_reissue(row)
        self._s.flush()
        return row
```

Imports at the top of the module: `datetime`, `timezone` and `date` from `datetime` (check with `sed -n '1,30p' data/chann_data/repositories/invoices.py` and add only what is missing).

`issue()` (:319) clears the flag, because a re-issue is what the flag asks for — add before `self._s.flush()`:

```python
        if (row.data_snapshot or {}).get("needs_reissue_at"):
            snapshot = dict(row.data_snapshot)
            snapshot.pop("needs_reissue_at", None)
            row.data_snapshot = snapshot
```

and the same three lines in `link_document` (:342), which is the path a re-issue of an already-issued invoice takes.

- [ ] **Step 4: Carry `needs_reissue` across the seam**

`data/chann_data/schemas.py`, in `InvoiceOut` (:1177) after `is_overdue`:

```python
    needs_reissue: bool = False
```

`data/chann_data/routers/internal.py`, in `_invoice_out` (:3504):

```python
        needs_reissue=bool((row.data_snapshot or {}).get("needs_reissue_at")),
```

- [ ] **Step 5: The two internal routes**

In `data/chann_data/routers/internal.py`, beside the other invoice routes, add:

The two request schemas go in `data/chann_data/schemas.py` beside `InvoiceIn` (:1131):

```python
class InvoiceLinesIn(BaseModel):
    """Round 21C — the whole line set, with the totals the Application tier
    computed from it. Sent together because they must never disagree."""
    data_snapshot: dict
    subtotal: Decimal
    discount_amount: Decimal = Decimal("0")
    vat_rate: Decimal | None = None
    vat_amount: Decimal = Decimal("0")
    total: Decimal


class InvoiceDetailsIn(BaseModel):
    note: str | None = None
    due_date: date | None = None
```

and the routes go in `data/chann_data/routers/internal.py`, below the existing invoice block:

```python
@router.patch("/licenses/{license_id}/invoices/{invoice_id}/lines", response_model=InvoiceOut)
def update_invoice_lines(
    license_id: uuid.UUID, invoice_id: uuid.UUID, payload: InvoiceLinesIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    """Replace an invoice's lines and the totals computed from them."""
    scope = TenantScope(license_id=license_id)
    try:
        repo = InvoiceRepository(session)
        before = repo.get(scope, invoice_id)
        before_total = before.total if before else None
        row = repo.update_lines(
            scope, invoice_id, data_snapshot=payload.data_snapshot,
            subtotal=payload.subtotal, discount_amount=payload.discount_amount,
            vat_rate=payload.vat_rate, vat_amount=payload.vat_amount, total=payload.total,
        )
        AuditRepository(session).write(
            license_id=license_id, entity_type="invoice", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields({"total": before_total}, {"total": row.total}),
        )
        session.commit()
        return _invoice_out(row, repo.list_payments(scope, row.id))
    except Exception as exc:
        session.rollback()
        raise _invoice_http_error(exc)


@router.patch("/licenses/{license_id}/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice_details(
    license_id: uuid.UUID, invoice_id: uuid.UUID, payload: InvoiceDetailsIn,
    session: Session = Depends(get_session), x_actor_id: str = Header(default=""),
):
    scope = TenantScope(license_id=license_id)
    try:
        repo = InvoiceRepository(session)
        before = repo.get(scope, invoice_id)
        before_fields = {"note": before.note, "due_date": before.due_date} if before else {}
        row = repo.update_details(scope, invoice_id, note=payload.note, due_date=payload.due_date)
        AuditRepository(session).write(
            license_id=license_id, entity_type="invoice", entity_id=row.id,
            actor_type="user", actor_id=x_actor_id or None, action="update",
            field_changes=diff_fields(before_fields, {"note": row.note, "due_date": row.due_date}),
        )
        session.commit()
        return _invoice_out(row, repo.list_payments(scope, row.id))
    except Exception as exc:
        session.rollback()
        raise _invoice_http_error(exc)
```

Note the route order: `PATCH /invoices/{invoice_id}/lines` must be declared **before** `PATCH /invoices/{invoice_id}` is not an issue (different path depth), but `GET /invoices/summary` already sits before `GET /invoices/{invoice_id}` for exactly that reason — keep the new routes below the existing invoice block.

- [ ] **Step 6: Run the tests**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/integration/test_round21c_invoice_edit.py tests/integration/test_round20v_invoices.py -q
```
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): an invoice's lines and details can be corrected until money touches it" && git log --oneline -1
```

---

### Task 6: An invoice can be edited — Application tier, client and routes

**Files:**
- Modify: `application/chann_app/services/invoices.py` (add `edit_lines`, `edit_details`)
- Modify: `application/chann_app/data_client.py` (add `update_invoice_lines`, `update_invoice_details`)
- Modify: `application/chann_app/routers_phase2.py` (two routes beside the invoice block at :2683-2950)
- Test: `tests/unit/test_round21c_invoice_edit.py`

**Interfaces:**
- Consumes: `InvoiceRepository.update_lines/update_details` and the two internal routes (Task 5).
- Produces:
  - `invoices.edit_lines(client, *, license_id, invoice, lines, company, actor_id=None) -> dict`
  - `invoices.edit_details(client, *, license_id, invoice, note=None, due_date=None, actor_id=None) -> dict`
  - `DataClient.update_invoice_lines(license_id, invoice_id, payload: dict, actor_id=None) -> dict`
  - `DataClient.update_invoice_details(license_id, invoice_id, payload: dict, actor_id=None) -> dict`
  - `PATCH /api/v1/licenses/{license_id}/invoices/{invoice_id}/lines` and `PATCH …/invoices/{invoice_id}`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_invoice_edit.py`:

```python
"""Round 21C — editing an invoice recomputes its money in one place."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import invoices  # noqa: E402


class FakeClient:
    def __init__(self, invoice):
        self.invoice = invoice
        self.lines_payload = None
        self.details_payload = None

    async def update_invoice_lines(self, license_id, invoice_id, payload, actor_id=None):
        self.lines_payload = payload
        return {**self.invoice, **{k: payload[k] for k in ("subtotal", "total")}}

    async def update_invoice_details(self, license_id, invoice_id, payload, actor_id=None):
        self.details_payload = payload
        return {**self.invoice, "note": payload.get("note")}

    async def get_customer(self, license_id, customer_id):
        return {"id": customer_id, "first_name": "สมชาย", "last_name": "ใจดี"}

    async def get_company_profile(self, license_id):
        return {"company_name": "ร้านทดสอบ", "vat_rate": "0.07"}


INVOICE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "invoice_id": "INV-2026-0001", "status": "draft", "paid_amount": "0",
    "contact_id": "22222222-2222-2222-2222-222222222222", "deal_id": None,
    "total": "32100.00", "vat_rate": "0.07",
    "data_snapshot": {"line_items": [
        {"line_no": 1, "product_name": "แอร์ 12000 BTU", "qty": 2,
         "unit_price": "15000.00", "line_total": "30000.00"}]},
}


class TestEditLines:
    @pytest.mark.asyncio
    async def test_the_totals_sent_are_computed_from_the_new_lines(self):
        client = FakeClient(INVOICE)
        await invoices.edit_lines(
            client, license_id="L1", invoice=INVOICE,
            lines=[{"product_name": "แอร์ 12000 BTU", "qty": 3, "unit_price": "15000.00"}],
            company={"company_name": "ร้านทดสอบ", "vat_rate": "0.07"},
        )
        sent = client.lines_payload
        assert Decimal(sent["subtotal"]) == Decimal("45000.00")
        assert Decimal(sent["vat_amount"]) == Decimal("3150.00")
        assert Decimal(sent["total"]) == Decimal("48150.00")
        assert sent["data_snapshot"]["line_items"][0]["qty"] == 3

    @pytest.mark.asyncio
    async def test_an_empty_line_set_is_refused_before_the_data_tier(self):
        client = FakeClient(INVOICE)
        with pytest.raises(invoices.InvoiceLinesEmpty):
            await invoices.edit_lines(client, license_id="L1", invoice=INVOICE, lines=[],
                                      company={"vat_rate": "0.07"})
        assert client.lines_payload is None

    @pytest.mark.asyncio
    async def test_a_paid_invoice_is_refused_before_the_data_tier(self):
        paid = {**INVOICE, "status": "partially_paid", "paid_amount": "1000.00"}
        client = FakeClient(paid)
        with pytest.raises(invoices.InvoiceLocked):
            await invoices.edit_details(client, license_id="L1", invoice=paid, note="สายไป")
        assert client.details_payload is None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_invoice_edit.py -q
```
Expected: FAIL — `AttributeError: module 'chann_app.services.invoices' has no attribute 'edit_lines'`.

- [ ] **Step 3: The service functions**

In `application/chann_app/services/invoices.py`, beside the other exceptions (:87-104):

```python
class InvoiceLocked(RuntimeError):
    """Money has been received, so the bill can no longer be corrected —
    the road from here is void + a fresh invoice."""


class InvoiceLinesEmpty(ValueError):
    """An invoice with no lines is not an invoice."""
```

and after `record_payment` (:456):

```python
async def edit_lines(
    client: DataClient, *, license_id: str, invoice: dict, lines: list[dict],
    company: dict | None = None, actor_id: str | None = None,
) -> dict:
    """Replace an invoice's lines and recompute every money column.

    The same two functions the bill was built with do the arithmetic —
    `build_line_items` and `compute_totals` — so a corrected invoice and a
    fresh one cannot disagree about VAT or rounding. Nothing here adds up
    a number that has been through JSON (round 20K).
    """
    from .documents.snapshot import build_line_items, compute_totals

    if money(invoice.get("paid_amount") or 0) > 0:
        raise InvoiceLocked(f"invoice {invoice.get('invoice_id')} already has a payment")
    if not lines:
        raise InvoiceLinesEmpty("an invoice needs at least one line")
    line_items = build_line_items(lines)
    vat_rate = (company or {}).get("vat_rate", invoice.get("vat_rate"))
    totals = compute_totals(line_items, vat_rate, _snapshot_discount(invoice))
    snapshot = dict(invoice.get("data_snapshot") or {})
    snapshot["line_items"] = line_items
    snapshot["totals"] = totals
    return await client.update_invoice_lines(
        str(license_id), str(invoice["id"]),
        {
            "data_snapshot": snapshot,
            "subtotal": totals["subtotal"],
            "discount_amount": totals.get("discount_amount") or "0",
            "vat_rate": totals.get("vat_rate"),
            "vat_amount": totals.get("vat_amount") or "0",
            "total": totals["grand_total"],
        },
        actor_id=actor_id,
    )


def _snapshot_discount(invoice: dict) -> Decimal | None:
    """The discount already on the bill, kept across an edit. None, not 0,
    when there was none — `compute_totals` treats the two differently."""
    current = ((invoice.get("data_snapshot") or {}).get("totals") or {}).get("discount_amount")
    if current in (None, "", "0", "0.00"):
        return None
    return money(current)


async def edit_details(
    client: DataClient, *, license_id: str, invoice: dict,
    note: str | None = None, due_date: date | None = None, actor_id: str | None = None,
) -> dict:
    """The note and the due date — the details a quote's `set_terms` covers."""
    if money(invoice.get("paid_amount") or 0) > 0:
        raise InvoiceLocked(f"invoice {invoice.get('invoice_id')} already has a payment")
    return await client.update_invoice_details(
        str(license_id), str(invoice["id"]),
        {"note": note, "due_date": due_date.isoformat() if due_date else None},
        actor_id=actor_id,
    )
```

- [ ] **Step 4: The client methods**

In `application/chann_app/data_client.py`, beside `add_invoice_payment` (:2628):

```python
    async def update_invoice_lines(
        self, license_id: str, invoice_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/invoices/{invoice_id}/lines",
            json=payload, headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)

    async def update_invoice_details(
        self, license_id: str, invoice_id: str, payload: dict, actor_id: str | None = None,
    ) -> dict:
        resp = await self._client.patch(
            f"{self._base}/internal/v1/licenses/{license_id}/invoices/{invoice_id}",
            json=payload, headers=self._headers_for(actor_id),
        )
        return self._unwrap(resp)
```

- [ ] **Step 5: The two dashboard routes**

In `application/chann_app/routers_phase2.py`, after `void_invoice` (:2927):

```python
class InvoiceLineIn(BaseModel):
    product_name: str
    qty: int = 1
    unit_price: str | float
    notes: str | None = None


class InvoiceLinesBody(BaseModel):
    lines: list[InvoiceLineIn]


class InvoiceDetailsBody(BaseModel):
    note: str | None = None
    due_date: date | None = None


@router.patch("/licenses/{license_id}/invoices/{invoice_id}/lines")
async def update_invoice_lines(
    license_id: str, invoice_id: str, payload: InvoiceLinesBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Correct what is on the bill. Editable until money has touched it —
    the invoice's answer to the quote's "draft only" (round 21C)."""
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        company = await client.get_company_profile(license_id)
        return await invoice_service.edit_lines(
            client, license_id=license_id, invoice=invoice,
            lines=[line.model_dump() for line in payload.lines],
            company=company, actor_id=principal.chann_uid,
        )
    except invoice_service.InvoiceLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except invoice_service.InvoiceLinesEmpty as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)


@router.patch("/licenses/{license_id}/invoices/{invoice_id}")
async def update_invoice_details(
    license_id: str, invoice_id: str, payload: InvoiceDetailsBody,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    from .services import invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    try:
        return await invoice_service.edit_details(
            client, license_id=license_id, invoice=invoice,
            note=payload.note, due_date=payload.due_date, actor_id=principal.chann_uid,
        )
    except invoice_service.InvoiceLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)
```

(`DataClient.get_company_profile(license_id)` is at `data_client.py:163` — the same call `_create_invoice` (:2751) makes; it carries `vat_rate`, which is what `compute_totals` needs.)

- [ ] **Step 6: Run the tests and the checkers**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_invoice_edit.py tests/unit/test_round20v_invoices.py -q
/tmp/dv/bin/python scripts/dev/check-client.py | tail -2
/tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
/tmp/dv/bin/python scripts/dev/check-methods.py | tail -2
```
Expected: tests passed; all three checkers clean.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the invoice edit road — one recomputation, one lock, both tiers" && git log --oneline -1
```

---

### Task 7: The payment that settles the bill closes the deal

**Files:**
- Modify: `data/chann_data/repositories/phase9.py` (add `DealRepository.close_won_from_invoice`)
- Modify: `data/chann_data/repositories/invoices.py:352-394` (`add_payment` returns what it settled)
- Modify: `data/chann_data/routers/internal.py:3660-3702` (`add_invoice_payment` writes the deal's audit rows)
- Test: `tests/integration/test_round21c_invoice_settle.py`

**Interfaces:**
- Consumes: `Deal.closed_at` (Task 4).
- Produces: `DealRepository.close_won_from_invoice(scope, deal_id, *, lines: list[dict], total, closed_at) -> Deal | None` (None when the deal must not be touched) and `InvoiceRepository.add_payment(...) -> tuple[Invoice, InvoicePayment, Deal | None]`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21c_invoice_settle.py`:

```python
"""Round 21C — when the bill is paid, the deal closes.

Owner, 23 ก.ย. 2569, verbatim: "ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอนชำระเงินแล้ว".
Not when it is issued — when the money arrives, which is the same moment
the receipt becomes possible.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, Deal
from chann_data.repositories.invoices import InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

SNAPSHOT = {
    "line_items": [
        {"line_no": 1, "product_name": "แอร์ 18000 BTU", "qty": 1,
         "unit_price": "22000.00", "line_total": "22000.00", "notes": None},
        {"line_no": 2, "product_name": "ค่าติดตั้ง", "qty": 1,
         "unit_price": "3000.00", "line_total": "3000.00", "notes": None},
    ],
    "totals": {"subtotal": "25000.00", "grand_total": "26750.00"},
}


def _world(engine, *, stage: str = "proposed", amount=None):
    tag = uuid.uuid4().hex[:6]
    uid = f"CHN-21CS{tag}"
    with Session(engine) as s:
        s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                            primary_role="sales", display_name="เจ้าของ"))
        s.commit()
    with Session(engine) as s:
        license_id = RegistrationRepository(s).create_license(
            company_name=f"Settle {tag}", created_by_chann_uid=uid).id
        s.commit()
    scope = TenantScope(license_id=license_id)
    with Session(engine) as s:
        customer = CustomerRepository(s).create(
            scope, first_name="ลูกค้า", last_name="จ่าย", phone=f"08{tag[:8]}")
        s.flush()
        deals = DealRepository(s)
        deal = deals.create(scope, contact_id=customer.id, amount=amount)
        s.flush()
        deals.add_product(scope, deal.id, product_id=None, product_name="ของเดิม",
                          quoted_unit_price=Decimal("111.00"), qty=1)
        if stage != "new":
            deal.stage = stage
        s.flush()
        invoice = InvoiceRepository(s).create(
            scope, deal_id=deal.id, contact_id=customer.id,
            subtotal=Decimal("25000.00"), vat_rate=Decimal("0.07"),
            vat_amount=Decimal("1750.00"), total=Decimal("26750.00"),
            data_snapshot=SNAPSHOT, created_by=uid)
        s.flush()
        InvoiceRepository(s).issue(scope, invoice.id, issue_date=date(2026, 9, 23))
        s.commit()
        return scope, deal.id, invoice.id


class TestTheSettle:
    def test_paying_in_full_closes_the_deal_and_writes_the_invoice_onto_it(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            lines = DealRepository(s).products_of(deal_id)
            assert deal.stage == "won"
            assert deal.closed_at is not None
            assert deal.amount == Decimal("26750.00")
            assert [(l.product_name, l.qty, l.quoted_unit_price) for l in lines] == [
                ("แอร์ 18000 BTU", 1, Decimal("22000.00")),
                ("ค่าติดตั้ง", 1, Decimal("3000.00")),
            ]

    def test_a_partial_payment_changes_nothing_on_the_deal(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("1000.00"), method="cash")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.stage == "proposed"
            assert deal.closed_at is None

    def test_a_deal_still_at_new_is_closed_too_because_the_money_arrived(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db, stage="new")
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            assert s.get(Deal, deal_id).stage == "won"

    def test_a_lost_deal_is_not_resurrected(self, migrated_db):
        scope, deal_id, invoice_id = _world(migrated_db, stage="lost")
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, invoice_id, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.stage == "lost"
            assert deal.amount is None

    def test_a_second_invoice_on_a_closed_deal_leaves_it_alone(self, migrated_db):
        scope, deal_id, first = _world(migrated_db)
        with Session(migrated_db) as s:
            InvoiceRepository(s).add_payment(
                scope, first, amount=Decimal("26750.00"), method="transfer")
            s.commit()
        with Session(migrated_db) as s:
            repo = InvoiceRepository(s)
            second = repo.create(
                scope, deal_id=deal_id, subtotal=Decimal("500.00"),
                vat_rate=None, vat_amount=Decimal("0"), total=Decimal("500.00"),
                data_snapshot={"line_items": [
                    {"line_no": 1, "product_name": "ค่าล้างแอร์", "qty": 1,
                     "unit_price": "500.00", "line_total": "500.00"}]})
            s.flush()
            repo.issue(scope, second.id, issue_date=date(2026, 9, 24))
            repo.add_payment(scope, second.id, amount=Decimal("500.00"), method="cash")
            s.commit()
        with Session(migrated_db) as s:
            deal = s.get(Deal, deal_id)
            assert deal.amount == Decimal("26750.00")      # not 500
            assert len(DealRepository(s).products_of(deal_id)) == 2
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_invoice_settle.py -q
```
Expected: FAIL — the first test's `deal.stage` is still `proposed`.

- [ ] **Step 3: The deal's side of it**

In `data/chann_data/repositories/phase9.py`, after `transition_stage`:

```python
    def close_won_from_invoice(
        self, scope: TenantScope, deal_id: uuid.UUID, *,
        lines: list[dict], total, closed_at: datetime,
    ) -> "Deal | None":
        """The bill was paid in full, so the deal is won and holds what it
        was paid for. Returns the deal, or None when it must not be touched.

        Deliberately NOT `transition_stage`: that state machine has no
        `new → won` because a PERSON may not click a deal from "new"
        straight to "won" without a quote. Money arriving is not a person
        clicking — it is the proof the machine exists to demand — so this
        path sets the stage itself and says why, and the human table at the
        top of this module is left exactly as it is.

        Three refusals, each one paid for by something that has gone wrong
        before: a lost deal is not resurrected by a payment; an archived
        deal is not edited at all; and a deal that is ALREADY closed keeps
        the contents of the bill that closed it, because the second invoice
        on a deal is a follow-up, not a correction.
        """
        deal = self.get(scope, deal_id)
        if deal is None or deal.archived_at is not None:
            return None
        if deal.stage == "lost":
            return None
        if deal.stage == "won" and deal.closed_at is not None:
            return None
        for row in self.products_of(deal_id):
            self._s.delete(row)
        self._s.flush()
        for position, line in enumerate(lines):
            self._s.add(DealProduct(
                id=uuid.uuid4(), deal_id=deal_id,
                product_id=line.get("product_id"),
                product_name=str(line.get("product_name") or "-")[:255],
                quoted_unit_price=_decimal(line.get("unit_price") or line.get("quoted_unit_price")),
                qty=int(line.get("qty") or 1), notes=line.get("notes"),
                position=position,
            ))
        deal.amount = _decimal(total)
        deal.lost_reason = None
        deal.stage = "won"
        deal.closed_at = closed_at
        self._s.flush()
        return deal
```

(`_decimal` is the module's existing helper — `grep -n "def _decimal" data/chann_data/repositories/phase9.py`. `DealProduct` and `uuid` are already imported.)

- [ ] **Step 4: The invoice calls it, in its own transaction**

In `data/chann_data/repositories/invoices.py`, replace the tail of `add_payment` (from `row.status = "paid" if …` to the `return`):

```python
        settled_before = row.status == "paid"
        row.status = "paid" if row.paid_amount >= row.total else "partially_paid"
        closed: "Deal | None" = None
        if row.status == "paid" and not settled_before and row.deal_id:
            # Round 21C, the owner's words: "ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอน
            # ชำระเงินแล้ว". Inside this transaction, so a payment that is
            # recorded and a deal that is not closed cannot both be true.
            # `add_payment` refuses a paid invoice, so this runs once per
            # invoice by construction.
            from .phase9 import DealRepository

            closed = DealRepository(self._s).close_won_from_invoice(
                scope, row.deal_id,
                lines=list((row.data_snapshot or {}).get("line_items") or []),
                total=row.total, closed_at=payment.paid_at,
            )
        self._s.flush()
        return row, payment, closed
```

(The local import keeps `invoices.py` and `phase9.py` free of an import cycle; `phase12`/`phase10` already do this — `grep -n "from .phase9 import" data/chann_data/repositories/*.py`.)

- [ ] **Step 5: Two more audit rows when it happens**

In `data/chann_data/routers/internal.py::add_invoice_payment` (:3660), change the unpack and add the rows after the existing two:

```python
        row, payment, closed_deal = repo.add_payment(
            scope, invoice_id, amount=payload.amount, method=payload.method,
            paid_at=payload.paid_at, reference=payload.reference, note=payload.note,
            recorded_by=payload.recorded_by or (x_actor_id or None),
        )
```

then, after the `action="status"` write for the invoice:

```python
        if closed_deal is not None:
            # Two rows, because two different things happened to the deal:
            # it closed, and its contents were replaced. Both verbs are on
            # the CHECK constraint's list (audit_actions.AUDIT_ACTIONS) —
            # inventing one here would roll the payment back with it.
            audit.write(
                license_id=license_id, entity_type="deal", entity_id=closed_deal.id,
                actor_type="user", actor_id=x_actor_id or None, action="status",
                field_changes=diff_fields(
                    {"stage": "open"}, {"stage": closed_deal.stage, "closed_by_invoice": row.invoice_id}),
            )
            audit.write(
                license_id=license_id, entity_type="deal", entity_id=closed_deal.id,
                actor_type="user", actor_id=x_actor_id or None, action="update",
                field_changes=diff_fields({}, {"amount": closed_deal.amount, "from_invoice": row.invoice_id}),
            )
```

Every other caller of `add_payment` must unpack three values now: `grep -rn "\.add_payment(" data/ tests/ | grep -v "def add_payment"` and fix each one (the round-20V integration test is one of them).

- [ ] **Step 6: Run the tests**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/integration/test_round21c_invoice_settle.py tests/integration/test_round20v_invoices.py \
  tests/integration/test_audit_actions.py -q
```
Expected: all passed. `test_audit_actions.py` is the one that proves the verbs match the CHECK constraint.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): paying a bill in full closes its deal and hands it the lines it was paid for" && git log --oneline -1
```

---

### Task 8: Send a quote, invoice or receipt to the customer on LINE

**Files:**
- Create: `application/chann_app/services/document_send.py`
- Modify: `application/chann_app/services/invoices.py:537-571` (`notify_customer_receipt` becomes a call into it)
- Modify: `application/chann_app/routers_phase2.py` (two routes)
- Modify: `application/chann_app/services/chat.py:103` (`ACTION_PERMISSIONS`)
- Test: `tests/unit/test_round21c_document_send.py`

**Interfaces:**
- Produces:
  - `document_send.CustomerNotLinked` (exception carrying `customer_name`)
  - `document_send.DocumentNotIssued`
  - `document_send.send_document_to_customer(client, *, license_id, kind, record, document_id, customer, company, language="th", actor_id=None) -> dict` → `{"sent": bool, "resent": bool, "url": str | None, "customer_name": str}`
  - `POST /api/v1/licenses/{license_id}/quotes/{quote_id}/send` and `POST …/invoices/{invoice_id}/send` (body `{"kind": "invoice"|"receipt"}` for the invoice route)
  - `ACTION_PERMISSIONS[("send","quote")] = "quote.update"`, `[("send","invoice")] = "invoice.update"`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_document_send.py`:

```python
"""Round 21C — sending a document to the customer on LINE.

Owner, 23 ก.ย. 2569: "ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้ว … สามารถออกคำสั่ง
หรือกดปุ่มส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มีผูกก็แจ้งว่าไม่ได้ หรือทำปุ่มเป็นไม่พร้อมใช้งาน".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import document_send  # noqa: E402

LINKED = {"id": "c1", "first_name": "สมชาย", "last_name": "ใจดี", "customer_chann_uid": "CHN-CUST-1"}
WALK_IN = {"id": "c2", "first_name": "สมหญิง", "last_name": "ร่ำรวย", "customer_chann_uid": None}
COMPANY = {"company_name": "ร้านทดสอบ"}
INVOICE = {"id": "i1", "invoice_id": "INV-2026-0003", "total": "32100.00",
           "generated_document_id": "d1"}


class FakeClient:
    def __init__(self):
        self.pushed = []
        self.notifications = []

    async def line_target_of(self, chann_uid):
        return f"U-{chann_uid}"

    async def list_notifications(self, license_id, **kwargs):
        return self.notifications


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send_notification(client, **kwargs):
        calls.append(kwargs)
        return {"id": "n1", **kwargs}

    monkeypatch.setattr("chann_app.services.notify.send_notification", fake_send_notification)
    monkeypatch.setattr("chann_app.services.chat.document_download_url",
                        lambda license_id, document_id: f"https://x/d/{document_id}")
    return calls


class TestSending:
    @pytest.mark.asyncio
    async def test_a_linked_customer_gets_the_document(self, sent):
        out = await document_send.send_document_to_customer(
            FakeClient(), license_id="L1", kind="invoice", record=INVOICE,
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True and out["resent"] is False
        assert out["customer_name"] == "สมชาย ใจดี"
        assert sent and sent[0]["oa"] == "customer"
        assert "INV-2026-0003" in sent[0]["message"]
        assert "https://x/d/d1" in sent[0]["message"]

    @pytest.mark.asyncio
    async def test_a_customer_without_line_is_refused_by_name(self, sent):
        with pytest.raises(document_send.CustomerNotLinked) as exc:
            await document_send.send_document_to_customer(
                FakeClient(), license_id="L1", kind="invoice", record=INVOICE,
                document_id="d1", customer=WALK_IN, company=COMPANY)
        assert exc.value.customer_name == "สมหญิง ร่ำรวย"
        assert sent == []

    @pytest.mark.asyncio
    async def test_a_document_that_was_never_issued_cannot_be_sent(self, sent):
        with pytest.raises(document_send.DocumentNotIssued):
            await document_send.send_document_to_customer(
                FakeClient(), license_id="L1", kind="invoice",
                record={**INVOICE, "generated_document_id": None},
                document_id=None, customer=LINKED, company=COMPANY)
        assert sent == []

    @pytest.mark.asyncio
    async def test_sending_the_same_version_twice_says_so(self, sent):
        client = FakeClient()
        client.notifications = [{"type": "document_sent", "entity_type": "invoice",
                                 "entity_id": "i1", "message": "…d1…",
                                 "created_at": "2026-09-23T03:00:00+00:00"}]
        out = await document_send.send_document_to_customer(
            client, license_id="L1", kind="invoice", record=INVOICE,
            document_id="d1", customer=LINKED, company=COMPANY)
        assert out["sent"] is True and out["resent"] is True
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_document_send.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'chann_app.services.document_send'`.

- [ ] **Step 3: The one road**

`application/chann_app/services/document_send.py`:

```python
"""Hand a document to the customer on LINE (round 21C).

Owner, 23 ก.ย. 2569: "ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้วสำหรับใบเสนอราคา
หรือ invoice ต่างๆสามารถออกคำสั่งหรือกดปุ่มส่งไปให้ลูกค้าผ่านไลน์ได้เลย ถ้าไม่มี
ผูกก็แจ้งว่าไม่ได้หรือทำปุ่มเป็นไม่พร้อมใช้งาน".

A receipt has been pushed this way since round 20V
(`invoices.notify_customer_receipt`). This is that one function, widened
to the three documents a shop hands over, so there is one road and not
three: the same signed link, the same notification row (which is what the
platform counts), the same "never raises, says what happened".
"""
from __future__ import annotations

import logging

from ..data_client import DataClient

log = logging.getLogger(__name__)

KIND_WORDS = {
    "quote": {"th": "ใบเสนอราคา", "en": "quotation"},
    "invoice": {"th": "ใบแจ้งหนี้", "en": "invoice"},
    "receipt": {"th": "ใบเสร็จรับเงิน", "en": "receipt"},
}
SEND_MESSAGE = {
    "th": "{what} {code} จาก {company}\nเปิดเอกสาร (ลิงก์ใช้ได้ 7 วัน):\n{url}",
    "en": "{what} {code} from {company}\nOpen it (link valid 7 days):\n{url}",
}
SEND_MESSAGE_NO_LINK = {
    "th": "{what} {code} จาก {company} — เปิดดูได้จากหน้าลูกค้า",
    "en": "{what} {code} from {company} — it is on your customer page.",
}


class CustomerNotLinked(RuntimeError):
    """The shop's record for this person has no LINE identity, so there is
    nowhere to push to. Carries the name, because "ส่งไม่ได้" without one
    tells the salesperson nothing about who to go and add."""

    def __init__(self, customer_name: str):
        super().__init__(f"{customer_name} is not linked on LINE")
        self.customer_name = customer_name


class DocumentNotIssued(RuntimeError):
    """There is no PDF yet. Issue it first; sending a link to nothing is
    worse than refusing."""


def customer_name(customer: dict) -> str:
    parts = [str(customer.get("first_name") or "").strip(), str(customer.get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or str(customer.get("customer_id") or "ลูกค้า")


def record_code(kind: str, record: dict) -> str:
    return str(record.get("invoice_id") or record.get("quote_id") or record.get("id") or "")


async def already_sent(client: DataClient, *, license_id: str, kind: str,
                       record: dict, document_id: str) -> bool:
    """Has THIS version of the document already gone out? Asked of the
    notification rows rather than kept as a flag, so re-issuing resets it
    for free: a new document id is a new question."""
    entity = "invoice" if kind in ("invoice", "receipt") else "quote"
    try:
        rows = await client.list_notifications(
            str(license_id), entity_type=entity, entity_id=str(record.get("id") or ""), limit=20)
    except Exception:  # noqa: BLE001
        return False
    return any(str(document_id) in str(row.get("message") or "")
               for row in rows or [] if row.get("type") == "document_sent")


async def send_document_to_customer(
    client: DataClient, *, license_id: str, kind: str, record: dict,
    document_id: str | None, customer: dict, company: dict,
    language: str = "th", actor_id: str | None = None,
) -> dict:
    """Push the document to the customer's LINE. Raises `DocumentNotIssued`
    or `CustomerNotLinked`; otherwise returns what happened."""
    from .chat import document_download_url
    from .notify import send_notification

    if kind not in KIND_WORDS:
        raise ValueError(f"unknown document kind: {kind!r}")
    if not document_id:
        raise DocumentNotIssued(f"{kind} {record_code(kind, record)} has no document yet")
    name = customer_name(customer)
    uid = str(customer.get("customer_chann_uid") or "")
    if not uid:
        raise CustomerNotLinked(name)
    resent = await already_sent(client, license_id=license_id, kind=kind,
                                record=record, document_id=document_id)
    url = document_download_url(str(license_id), str(document_id))
    table = SEND_MESSAGE if url else SEND_MESSAGE_NO_LINK
    values = {
        "what": KIND_WORDS[kind]["th"], "code": record_code(kind, record),
        "company": company.get("company_name") or company.get("legal_name") or "",
        "url": url or "",
    }
    values_en = {**values, "what": KIND_WORDS[kind]["en"]}
    line_uid = await client.line_target_of(uid)
    await send_notification(
        client, license_id=str(license_id), target_chann_uid=uid, target_line_user_id=line_uid,
        type="document_sent", message=table["th"].format(**values),
        message_en=table["en"].format(**values_en),
        entity_type="invoice" if kind in ("invoice", "receipt") else "quote",
        entity_id=str(record.get("id") or ""),
        # The customer reads it in LINE and on their own page; there is no
        # dashboard bell for them (the same choice notify_customer_receipt
        # made).
        delivery_dashboard=False, oa="customer", language=language,
    )
    return {"sent": True, "resent": resent, "url": url, "customer_name": name}
```

`notify_customer_receipt` (`services/invoices.py:537`) becomes:

```python
async def notify_customer_receipt(
    client: DataClient, *, license_id: str, invoice: dict, document: dict,
) -> bool:
    """One LINE line to the customer who paid, with the receipt link.
    Round 21C: the same road every other document now takes
    (`services/document_send.py`) — it must never raise, so the refusals
    are caught here and reported as False."""
    from .document_send import CustomerNotLinked, DocumentNotIssued, send_document_to_customer

    customer, company = await invoice_parties(client, license_id, invoice)
    try:
        out = await send_document_to_customer(
            client, license_id=str(license_id), kind="receipt", record=invoice,
            document_id=str(document.get("id") or ""), customer=customer, company=company,
        )
        return bool(out["sent"])
    except (CustomerNotLinked, DocumentNotIssued):
        return False
    except Exception:  # noqa: BLE001
        log.exception("could not tell the customer about the receipt of %s", invoice.get("invoice_id"))
        return False
```

Confirm `list_notifications`'s real name and parameters before writing `already_sent`:
`grep -n "async def list_notifications" -A 10 application/chann_app/data_client.py`. If it does not accept `entity_type`/`entity_id`, pass what it does accept and filter in Python — the function must not become a reason to add a Data-tier route in this task.

- [ ] **Step 4: The two routes**

In `application/chann_app/routers_phase2.py`, beside the invoice block:

```python
class DocumentSendBody(BaseModel):
    kind: str = "invoice"   # "invoice" | "receipt"; the quote route ignores it


def _document_send_error(exc: Exception) -> HTTPException:
    from .services.document_send import CustomerNotLinked, DocumentNotIssued

    if isinstance(exc, CustomerNotLinked):
        return HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail={"error": "customer_not_linked", "customer": exc.customer_name})
    if isinstance(exc, DocumentNotIssued):
        return HTTPException(status_code=422, detail={"error": "not_issued"})
    raise exc


@router.post("/licenses/{license_id}/invoices/{invoice_id}/send")
async def send_invoice_to_customer(
    license_id: str, invoice_id: str, payload: DocumentSendBody | None = None,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """Hand the bill (or its receipt) to the customer on LINE."""
    from .services import document_send, invoices as invoice_service

    _require_same_tenant(principal, license_id)
    principal.require("invoice.update")
    invoice = await _invoice_or_404(client, principal, license_id, invoice_id)
    kind = (payload.kind if payload else "invoice") or "invoice"
    document_id = invoice.get("receipt_document_id") if kind == "receipt" else invoice.get("generated_document_id")
    customer, company = await invoice_service.invoice_parties(client, license_id, invoice)
    try:
        return await document_send.send_document_to_customer(
            client, license_id=license_id, kind=kind, record=invoice,
            document_id=str(document_id) if document_id else None,
            customer=customer, company=company, actor_id=principal.chann_uid)
    except (document_send.CustomerNotLinked, document_send.DocumentNotIssued) as exc:
        raise _document_send_error(exc)
    except DataTierError as exc:
        raise _propagate(exc)


@router.post("/licenses/{license_id}/quotes/{quote_id}/send")
async def send_quote_to_customer(
    license_id: str, quote_id: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    from .services import document_send

    _require_same_tenant(principal, license_id)
    principal.require("quote.update")
    quote = await client.get_quote(license_id, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="quote not found")
    deal = await client.get_deal(license_id, str(quote.get("deal_id")))
    customer = await client.get_customer(license_id, str((deal or {}).get("contact_id") or "")) or {}
    company = await client.get_company_profile(license_id)
    try:
        return await document_send.send_document_to_customer(
            client, license_id=license_id, kind="quote", record=quote,
            document_id=str(quote.get("generated_document_id") or "") or None,
            customer=customer, company=company, actor_id=principal.chann_uid)
    except (document_send.CustomerNotLinked, document_send.DocumentNotIssued) as exc:
        raise _document_send_error(exc)
    except DataTierError as exc:
        raise _propagate(exc)
```

- [ ] **Step 5: Register the capability on both surfaces**

`application/chann_app/services/chat.py:103`, in `ACTION_PERMISSIONS`, beside the quote and invoice entries:

```python
    # Round 21C — handing the document to the customer on LINE. The same
    # keys that govern issuing it: whoever may put a number on a document
    # may give it to the person it is addressed to.
    ("send", "quote"): "quote.update",
    ("send", "invoice"): "invoice.update",
```

`scripts/dev/check-parity.py` will now demand both surfaces for `("quote","send")` and `("invoice","send")`; the dashboard buttons in Task 10 are what satisfies it. **Do not** put them in `ACCEPTED` — they are not one-sided.

- [ ] **Step 6: Run the tests and the checkers**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_document_send.py tests/unit/test_round20v_invoices.py -q
/tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
/tmp/dv/bin/python scripts/dev/check-perms.py | tail -2
```
Expected: tests passed; `check-routes`/`check-perms` clean. `check-parity` will report `quote.send`/`invoice.send` as chat-only until Task 10 — that is expected, and Task 10's gate is where it must be clean.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): a document can be handed to the customer on LINE, and says who is not linked" && git log --oneline -1
```

---

### Task 9: Chat — correcting an invoice, the sentence when a bill settles, and "ส่งให้ลูกค้า"

**Files:**
- Modify: `application/chann_app/services/ai/intent.py:234-270` (the `invoice` block; add `action="send"`, move one example)
- Modify: `application/chann_app/services/chat.py` — `_handle_invoice_intent` (:23967), `_handle_invoice_payment` (:23753), new `_handle_invoice_edit` and `_handle_document_send`
- Create: `scripts/agent-test/scenarios/round21c-invoice-edit.yaml`
- Test: `tests/unit/test_round21c_invoice_chat.py`

**Interfaces:**
- Consumes: `invoices.edit_lines/edit_details`, `InvoiceLocked`, `InvoiceLinesEmpty` (Task 6); `document_send.send_document_to_customer`, `CustomerNotLinked`, `DocumentNotIssued` (Task 8); `add_payment`'s third return value surfacing as the invoice payload's `closed_deal` (Task 7).
- Produces: chat handlers `_handle_invoice_edit(...) -> ChatReply` and `_handle_document_send(...) -> ChatReply`.

- [ ] **Step 0: Ask the deployed model FIRST — this is the conversion, not a formality**

```bash
cd ~/stage-fix/r21c && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py \
  "ส่งใบแจ้งหนี้ INV-2026-0003 ให้ลูกค้า" \
  "ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้าทางไลน์" \
  "ส่งใบเสร็จให้ลูกค้า INV-2026-0003" \
  "แก้รายการในใบแจ้งหนี้ INV-2026-0003" \
  "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0003 เป็น 3 ตัว" \
  "ใบแจ้งหนี้ INV-2026-0003 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน"
```
Record all six answers verbatim in the handoff (Task 17). Expect `ส่งใบเสร็จให้ลูกค้า` to come back as `action="receipt"` — it is listed as an example of that action in the prompt today (`intent.py:262-263`). That is the collision this task resolves; **measure it before and after**, per sentence.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_invoice_chat.py`:

```python
"""Round 21C — the chat side of editing, settling and sending a bill."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import chat  # noqa: E402


class TestThePromptTeachesSending:
    def test_send_is_an_action_of_its_own(self):
        from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT

        assert 'action="send"' in INTENT_SYSTEM_PROMPT
        assert "ส่งใบแจ้งหนี้" in INTENT_SYSTEM_PROMPT

    def test_both_send_capabilities_are_registered(self):
        assert chat.ACTION_PERMISSIONS[("send", "quote")] == "quote.update"
        assert chat.ACTION_PERMISSIONS[("send", "invoice")] == "invoice.update"


class TestTheRefusalsAreSaidInWords:
    def test_a_locked_invoice_is_explained_not_just_refused(self):
        said = chat._t(chat.LINE_INVOICE_LOCKED, "th").format(code="INV-2026-0001")
        assert "INV-2026-0001" in said
        assert "ยกเลิก" in said          # the road out is named

    def test_an_unlinked_customer_is_named_and_the_way_to_fix_it_is_said(self):
        said = chat._t(chat.DOCUMENT_SEND_NO_LINE, "th").format(name="สมหญิง ร่ำรวย")
        assert "สมหญิง ร่ำรวย" in said
        assert "ไลน์" in said

    def test_the_settle_sentence_names_the_deal_and_the_value(self):
        said = chat._t(chat.INVOICE_SETTLED_DEAL, "th").format(
            deal="D-2026-0007", amount="26,750.00")
        assert "D-2026-0007" in said and "26,750.00" in said
```

(`chat._t` is the module's existing table-to-language helper; `grep -n "^def _t(" application/chann_app/services/chat.py`.)

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_invoice_chat.py -q
```
Expected: FAIL — `AssertionError: 'action="send"' in …` and `AttributeError: module … has no attribute 'LINE_INVOICE_LOCKED'`.

- [ ] **Step 3: Teach the model the new action**

In `application/chann_app/services/ai/intent.py`, inside the `entity="invoice"` block, **replace** the receipt example line
`"ส่งใบเสร็จให้ลูกค้า INV-2026-0001".` with `"ออกใบเสร็จให้ INV-2026-0001".` and add, after `action="void"`:

```
  action="send": hand an EXISTING document to the customer on LINE — the
    shop is not making anything, it is giving it to them.
    fields={{"code": "INV-2026-0001", "kind": "invoice"}}, kind "receipt"
    when the sentence says ใบเสร็จ. Examples: "ส่งใบแจ้งหนี้ INV-2026-0001
    ให้ลูกค้า", "ส่งบิลให้ลูกค้าทางไลน์", "ส่งใบเสร็จให้ลูกค้า
    INV-2026-0001". "ออกใบเสร็จ" is action="receipt" (make it); "ส่ง
    ใบเสร็จ" is this (give it).
```

and in the `entity="quote"` block, after its existing actions:

```
  action="send": give an ISSUED quotation to the customer on LINE.
    fields={{"code": "Q-2026-0001"}}. Examples: "ส่งใบเสนอราคา Q-2026-0001
    ให้ลูกค้า", "ส่งใบเสนอราคาให้ลูกค้าทางไลน์".
```

- [ ] **Step 4: The copy**

In `application/chann_app/services/chat.py`, beside `LINE_QUOTE_LOCKED` (:14703):

```python
#: Round 21C — an invoice is editable until money touches it (spec §3.3).
LINE_INVOICE_LOCKED = {
    "th": "ใบแจ้งหนี้ {code} มีการรับชำระแล้ว จึงแก้รายการไม่ได้ — ถ้าต้องแก้จริง ให้ยกเลิกใบนี้แล้วออกใบใหม่",
    "en": "Invoice {code} already has a payment recorded, so it can no longer be edited — void it and raise a new one.",
}
INVOICE_NEEDS_REISSUE = {
    "th": "\n\nใบนี้ออกเอกสารไปแล้ว เอกสารเดิมจึงไม่ตรงกับที่แก้ — พิมพ์ \"ออกเอกสาร {code}\" เพื่อออกใหม่ด้วยเลขเดิม",
    "en": "\n\nThis invoice already had a PDF, so it no longer matches — say \"issue {code}\" to re-issue it under the same number.",
}
#: The write-back, said out loud. A deal that changes by itself is a
#: number nobody can check (spec §3.4).
INVOICE_SETTLED_DEAL = {
    "th": "\n\nชำระครบแล้ว · ปิดดีล {deal} เป็น \"ปิดสำเร็จ\" และอัปเดตรายการ/มูลค่าดีลเป็น {amount} บาท ตามใบแจ้งหนี้นี้",
    "en": "\n\nPaid in full — deal {deal} is now won, and its lines and value ({amount}) come from this invoice.",
}
DOCUMENT_SENT = {
    "th": "ส่ง{what} {code} ให้ {name} ทางไลน์แล้ว (ลิงก์ใช้ได้ 7 วัน)",
    "en": "Sent {what} {code} to {name} on LINE (the link is valid 7 days).",
}
DOCUMENT_RESENT = {"th": " · ส่งซ้ำ", "en": " · sent again"}
DOCUMENT_SEND_NO_LINE = {
    "th": "ส่งให้ไม่ได้ — {name} ยังไม่ได้ผูกไลน์กับร้าน ให้ลูกค้าเพิ่มเพื่อน OA ลูกค้าแล้วลงทะเบียน หรือคัดลอกลิงก์ส่งเอง",
    "en": "Cannot send — {name} is not linked on LINE. Ask them to add the customer OA and register, or copy the link and send it yourself.",
}
DOCUMENT_SEND_NOT_ISSUED = {
    "th": "ยังไม่ได้ออกเอกสารของ {code} จึงยังไม่มีอะไรให้ส่ง — พิมพ์ \"ออกเอกสาร {code}\" ก่อน",
    "en": "{code} has no document yet — issue it first.",
}
```

- [ ] **Step 5: The two handlers and the dispatcher**

Add to `application/chann_app/services/chat.py`, beside the other invoice handlers:

```python
async def _handle_invoice_edit(
    client: DataClient, *, ctx: ResolvedContext, license_id, fields: dict, message: str,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """Correct a bill: its lines, its note, or when it is due.

    The lines themselves are edited the way a deal's and a quote's are —
    one line at a time, through `_handle_line_item_command` — so this
    handler resolves the invoice, checks the lock, and hands over. What it
    does NOT do is compute money: `invoices.edit_lines` does that once, in
    one place (round 20K)."""
    from . import invoices as invoice_service

    if "invoice.update" not in set(permission_keys):
        return ChatReply(text=_t(NO_PERMISSION_FOR, language).format(what="แก้ใบแจ้งหนี้"))
    invoice = await _invoice_by_code(client, license_id, str(fields.get("code") or ""))
    if invoice is None:
        return ChatReply(text=_t(INVOICE_NOT_FOUND, language).format(code=fields.get("code") or ""))
    try:
        lines = _invoice_lines_after(invoice, fields, message)
        if lines is not None:
            company = await client.get_company_profile(str(license_id))
            updated = await invoice_service.edit_lines(
                client, license_id=str(license_id), invoice=invoice, lines=lines,
                company=company, actor_id=ctx.chann_uid)
        else:
            updated = await invoice_service.edit_details(
                client, license_id=str(license_id), invoice=invoice,
                note=fields.get("note"), due_date=_date_field(fields.get("due_date")),
                actor_id=ctx.chann_uid)
    except invoice_service.InvoiceLocked:
        return ChatReply(text=_t(LINE_INVOICE_LOCKED, language).format(code=invoice["invoice_id"]))
    except invoice_service.InvoiceLinesEmpty:
        return ChatReply(text=_t(INVOICE_NEEDS_A_LINE, language))
    except DataTierError as exc:
        return ChatReply(text=_data_error_text(exc, language))
    text = _invoice_card(updated, language)
    if (updated.get("data_snapshot") or {}).get("needs_reissue_at"):
        text += _t(INVOICE_NEEDS_REISSUE, language).format(code=updated["invoice_id"])
    return ChatReply(text=text, entity_type="invoice", entity_id=str(updated["id"]),
                     intent={"action": "update", "entity": "invoice"})


async def _handle_document_send(
    client: DataClient, *, ctx: ResolvedContext, license_id, entity: str, fields: dict,
    permission_keys: list[str], language: str,
) -> ChatReply:
    """Give an issued document to the customer on LINE (owner, 23 ก.ย.)."""
    from . import document_send, invoices as invoice_service

    needed = "quote.update" if entity == "quote" else "invoice.update"
    if needed not in set(permission_keys):
        return ChatReply(text=_t(NO_PERMISSION_FOR, language).format(what="ส่งเอกสารให้ลูกค้า"))
    kind = "receipt" if str(fields.get("kind") or "") == "receipt" else entity
    code = str(fields.get("code") or "")
    if entity == "quote":
        record = await _quote_by_code(client, license_id, code)
        document_id = str((record or {}).get("generated_document_id") or "") or None
        customer, company = await _quote_parties(client, license_id, record) if record else ({}, {})
    else:
        record = await _invoice_by_code(client, license_id, code)
        document_id = str((record or {}).get(
            "receipt_document_id" if kind == "receipt" else "generated_document_id") or "") or None
        customer, company = await invoice_service.invoice_parties(
            client, str(license_id), record) if record else ({}, {})
    if record is None:
        return ChatReply(text=_t(RECORD_NOT_FOUND, language).format(code=code))
    try:
        out = await document_send.send_document_to_customer(
            client, license_id=str(license_id), kind=kind, record=record,
            document_id=document_id, customer=customer, company=company,
            language=language, actor_id=ctx.chann_uid)
    except document_send.CustomerNotLinked as exc:
        url = document_download_url(str(license_id), document_id or "")
        return ChatReply(
            text=_t(DOCUMENT_SEND_NO_LINE, language).format(name=exc.customer_name)
                 + (f"\n{url}" if url else ""),
            entity_type=entity, entity_id=str(record.get("id") or ""))
    except document_send.DocumentNotIssued:
        return ChatReply(text=_t(DOCUMENT_SEND_NOT_ISSUED, language).format(code=code))
    except DataTierError as exc:
        return ChatReply(text=_data_error_text(exc, language))
    said = _t(DOCUMENT_SENT, language).format(
        what=document_send.KIND_WORDS[kind]["th" if language != "en" else "en"],
        code=document_send.record_code(kind, record), name=out["customer_name"])
    if out["resent"]:
        said += _t(DOCUMENT_RESENT, language)
    return ChatReply(text=said, entity_type=entity, entity_id=str(record.get("id") or ""),
                     intent={"action": "send", "entity": entity})
```

In `_handle_invoice_intent` (:23967), add two branches beside the existing ones:

```python
    if action == "update":
        return await _handle_invoice_edit(
            client, ctx=ctx, license_id=license_id, fields=fields, message=message,
            permission_keys=permission_keys or [], language=language)
    if action == "send":
        return await _handle_document_send(
            client, ctx=ctx, license_id=license_id, entity="invoice", fields=fields,
            permission_keys=permission_keys or [], language=language)
```

and route `("send", "quote")` from the quote intent handler (`_handle_quote_intent`; find it with
`grep -n "async def _handle_quote_intent" application/chann_app/services/chat.py`) to
`_handle_document_send(..., entity="quote", ...)`.

The helpers used above that may not exist yet — check each and write the missing ones beside their neighbours, all of them small:
`grep -n "_invoice_by_code\|_quote_by_code\|_quote_parties\|_invoice_card\|_date_field\|NO_PERMISSION_FOR\|INVOICE_NOT_FOUND\|RECORD_NOT_FOUND\|INVOICE_NEEDS_A_LINE\|_invoice_lines_after\|_data_error_text" application/chann_app/services/chat.py | head -40`.
`_invoice_lines_after(invoice, fields, message)` is new: it returns the full line list to send, or `None` when the sentence changed only a detail. Build it from the snapshot's `line_items` with the one change the intent carries (`{"line_no": 1, "qty": 3}` / `{"product_name": …, "unit_price": …}`), reusing `_product_clauses` where the sentence names a product by name.

- [ ] **Step 6: Say it when the bill settles**

In `_handle_invoice_payment` (:23753), after the payment comes back, append the sentence when the deal closed. The Application tier learns this from the invoice payload: `POST …/payments` returns the invoice, and `closed_deal` travels with it — add it to the Application route's response in `routers_phase2.py::record_invoice_payment` (:2856) as `{"invoice": …, "closed_deal": {"deal_id": …, "amount": …} | None}` if it is not there already, reading it from the Data tier's reply. Then:

```python
    closed = (result or {}).get("closed_deal") or {}
    if closed:
        text += _t(INVOICE_SETTLED_DEAL, language).format(
            deal=closed.get("deal_id") or "", amount=invoice_service.baht(closed.get("amount")))
```

For this to work, the Data tier must return it: in `internal.py::add_invoice_payment`, the
response model is `InvoiceOut`, so add `closed_deal: dict | None = None` to `InvoiceOut` in
`schemas.py` and fill it in `_invoice_out(row, payments, closed_deal=None)` — one optional
parameter, defaulting to None for every other caller.

- [ ] **Step 7: The agent-test scenario**

Create `scripts/agent-test/scenarios/round21c-invoice-edit.yaml` (mirror
`scripts/agent-test/scenarios/round20v-invoices.yaml`'s shape — `name`, `description`,
`backend: fake`, `actor`, then `seed`/`send` steps):

```yaml
name: round21c-invoice-edit
description: a bill can be corrected until money touches it, paying it in full closes the deal
  with the lines it was paid for, and an issued document can be handed to the customer on LINE -
  or refused by name when they have no LINE. Every reading is the deployed model's own
  (scripts/dev/ask-model.py, 23 ก.ย. 2569).
backend: fake
actor:
  oa: sales
  role: sales
  language: th
  permissions: sales
steps:
- seed:
    raw:
      company_profile:
        legal_name: บริษัท ทดสอบ จำกัด
        company_name: ร้านทดสอบ
        tax_id: '0105558123456'
        company_address: 99/1
        company_phone: '021234567'
        company_email: a@b.com
        open_hours: null
        vat_rate: '0.07'
    customers:
    - ref: somchai
      first_name: สมชาย
      last_name: ใจดี
      phone: '0812345678'
      customer_chann_uid: CHN-CUST-1
    - ref: somying
      first_name: สมหญิง
      last_name: ร่ำรวย
      phone: '0899999999'
    deals:
    - ref: deal1
      contact_id: $somchai
    call:
    - method: add_deal_product
      args: {deal_id: $deal1.id, payload: {product_name: แอร์ 12000 BTU, qty: 2, quoted_unit_price: '15000.00'}}
- send:
    message: ออกใบแจ้งหนี้ให้ดีล D-2026-0001
    expect_text: ['INV-2026-0001']
- send:
    message: เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว
    expect_text: ['48,150.00', 'ออกเอกสาร']
- send:
    message: ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า
    expect_text: ['สมชาย', 'ไลน์']
- send:
    message: รับชำระ INV-2026-0001 ครบ
    expect_text: ['ปิดสำเร็จ', 'D-2026-0001']
- send:
    message: แก้ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน
    expect_text: ['แก้รายการไม่ได้', 'ยกเลิก']
```

Read `scripts/agent-test/agent_test_runner/` for the exact step keys before writing it
(`grep -rn "expect_text\|expect_rows" scripts/agent-test/agent_test_runner/ | head`), and make
the seed's `customer_chann_uid` match whatever the fake backend calls that field.

- [ ] **Step 8: Run everything chat touches**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_invoice_chat.py tests/unit/test_round20v_invoices.py \
  tests/unit/test_round20x_invoice_form.py -q
/tmp/dv/bin/python scripts/agent-test/run.py --only round21c-invoice-edit --only round20v-invoices | tail -3
/tmp/dv/bin/python scripts/dev/simulate-phrasings.py | grep -E "^=== [0-9]+ cases"
/tmp/dv/bin/python scripts/dev/measure-road-share.py | tail -4
```
Expected: tests passed; `scenarios · N passed · 0 failed`; phrasings no worse than before (`0 long`); **road share must not shrink** — `measure-road-share.py` exits non-zero if the rule road grew.

- [ ] **Step 9: Ask the model again, and diff sentence by sentence**

Re-run Step 0's six sentences. Every one must now read as the action the handler expects, and
the four sentences that were already correct must be **unchanged** — "measure per sentence,
never on totals" (`docs/MODEL_FIRST.md`). Paste the before/after pairs into the handoff.

- [ ] **Step 10: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): chat corrects a bill, says when one closes its deal, and hands documents to the customer" && git log --oneline -1
```

---

### Task 10: Dashboard — the invoice line editor and the two send buttons

**Files:**
- Modify: `presentation/app/liff/sales/invoices/InvoiceList.tsx` (the detail `<Sheet>` at :563-789)
- Modify: `presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx` (a send button beside the document actions)
- Modify: `presentation/lib/i18n/th.ts`, `presentation/lib/i18n/en.ts`
- Test: `tests/unit/test_round21c_invoice_ui.py` (source assertions, the way `test_round20x_invoice_form.py` does it)

**Interfaces:**
- Consumes: `PATCH /api/phase2/licenses/{id}/invoices/{invoiceId}/lines`, `PATCH …/invoices/{invoiceId}`, `POST …/invoices/{invoiceId}/send`, `POST …/quotes/{quoteId}/send` (Tasks 6 and 8); `invoice.needs_reissue`, `invoice.paid_amount`.
- Produces: nothing other tasks consume.

- [ ] **Step 0: The design pass (owner's standing rule, 20 ก.ย. 2569)**

Run the **ui-ux-pro-max** skill for this screen before writing TSX. The three rules that
decide this layout, and what they mean here:
- `primary-action` — the sheet already has one primary action (issue / record payment).
  The line editor is **not** a second one: it is an "แก้ไขรายการ" button that turns the
  read-only line list into an editable one, in place.
- `destructive-nav-separation` — "ลบรายการ" stays a per-line danger action under the list,
  never beside "ส่งให้ลูกค้า".
- `disabled-needs-a-reason` — a disabled "ส่งให้ลูกค้า" shows its reason as visible helper
  text under the button, not only in `title`: a phone has no tooltip.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21c_invoice_ui.py`:

```python
"""Round 21C — the invoice sheet can correct a bill and hand it over.

Source assertions, like tests/unit/test_round20x_invoice_form.py: these
pin that the screen exists and calls the right routes. What they cannot
see is whether it looks right — that is the ui-ux pass and a human
looking at it.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVOICES = (ROOT / "presentation/app/liff/sales/invoices/InvoiceList.tsx").read_text(encoding="utf-8")
QUOTE = (ROOT / "presentation/app/liff/sales/quotes/[id]/QuoteDetail.tsx").read_text(encoding="utf-8")
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")


class TestTheLineEditor:
    def test_the_sheet_patches_lines_and_details(self):
        assert "/lines" in INVOICES and '"PATCH"' in INVOICES
        assert "ProductLineForm" in INVOICES

    def test_it_is_closed_once_money_has_been_received(self):
        assert "Number(open.paid_amount" in INVOICES

    def test_a_reissue_is_offered_when_the_document_no_longer_matches(self):
        assert "needs_reissue" in INVOICES


class TestSending:
    def test_both_screens_can_send_to_the_customer(self):
        assert "/send" in INVOICES
        assert "/send" in QUOTE

    def test_a_disabled_send_says_why_in_visible_text(self):
        # not only title=: the reason is rendered
        assert "sendNoLine" in INVOICES and "sendNoLine" in QUOTE
        assert "sendNoLine" in TH
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_invoice_ui.py -q
```
Expected: FAIL — `assert '/lines' in INVOICES`.

- [ ] **Step 3: The strings**

`presentation/lib/i18n/th.ts`, under `dashboard.invoices` (find it: `grep -n "invoices: {" presentation/lib/i18n/th.ts`):

```ts
      editLines: "แก้ไขรายการ",
      editLinesDone: "เสร็จแล้ว",
      editLocked: "ใบนี้รับชำระแล้ว จึงแก้รายการไม่ได้ — ถ้าต้องแก้ ให้ยกเลิกใบนี้แล้วออกใบใหม่",
      needsReissue: "แก้หลังออกเอกสารแล้ว เอกสารเดิมไม่ตรงกับใบนี้ — กด \"ออกเอกสารอีกครั้ง\" เพื่อออกใหม่ด้วยเลขเดิม",
      send: "ส่งให้ลูกค้าทางไลน์",
      sendReceipt: "ส่งใบเสร็จให้ลูกค้า",
      sendDone: "ส่งให้ {name} ทางไลน์แล้ว",
      sendAgain: "ส่งซ้ำให้ {name} แล้ว",
      sendNoLine: "ลูกค้ายังไม่ได้ผูกไลน์กับร้าน จึงส่งให้ทางไลน์ไม่ได้ — ให้ลูกค้าเพิ่มเพื่อน OA ลูกค้าแล้วลงทะเบียน",
      sendNotIssued: "ยังไม่ได้ออกเอกสาร จึงยังไม่มีอะไรให้ส่ง",
```

and the English twin of each in `en.ts` (`check-i18n-usage.py` fails on a key present in one
file and missing in the other).

- [ ] **Step 4: The editor inside the sheet**

In `InvoiceList.tsx`, inside the detail `<Sheet>`, replace the read-only line list with a
section that flips into edit mode. State beside the existing `form` state (:108):

```tsx
  const [editingLines, setEditingLines] = useState(false);
  const [lines, setLines] = useState<InvoiceLine[]>([]);
```

and the two writers beside `savePayment` (:316):

```tsx
  const editable =
    can("invoice.update") && Number(open?.paid_amount ?? 0) === 0 && open?.status !== "void";

  async function saveLines(next: InvoiceLine[]) {
    if (!open) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/invoices/${open.id}/lines`,
        {
          method: "PATCH",
          headers: proxyHeaders(token, licenseId),
          body: JSON.stringify({
            lines: next.map((line) => ({
              product_name: line.product_name,
              qty: Number(line.qty ?? 1),
              unit_price: String(line.unit_price ?? "0"),
            })),
          }),
        },
      );
      if (!response.ok) {
        say(response.status === 409 ? copy.editLocked : listError(response), "error");
        return;
      }
      // The server's totals, re-read — never recomputed here. A price the
      // browser adds up is a second source of truth (round 20K).
      const saved = (await response.json()) as Invoice;
      setOpen(saved);
      setLines((saved.data_snapshot?.line_items ?? []) as InvoiceLine[]);
      say(copy.saved, "ok");
    } finally {
      setBusy(false);
    }
  }

  async function sendToCustomer(kind: "invoice" | "receipt") {
    if (!open) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/phase2/licenses/${licenseId}/invoices/${open.id}/send`, {
        method: "POST",
        headers: proxyHeaders(token, licenseId),
        body: JSON.stringify({ kind }),
      });
      const data = (await response.json()) as
        { sent?: boolean; resent?: boolean; customer_name?: string; detail?: { error?: string; customer?: string } };
      if (!response.ok) {
        say(data.detail?.error === "customer_not_linked" ? copy.sendNoLine : copy.sendNotIssued, "error");
        return;
      }
      say((data.resent ? copy.sendAgain : copy.sendDone).replace("{name}", data.customer_name ?? ""), "ok");
    } finally {
      setBusy(false);
    }
  }
```

Render, inside the sheet, after the line list:

```tsx
        {editable ? (
          editingLines ? (
            <ProductLineForm
              lines={lines}
              onChange={setLines}
              onSave={() => { void saveLines(lines); setEditingLines(false); }}
              onCancel={() => setEditingLines(false)}
            />
          ) : (
            <button type="button" className="linklike" onClick={() => {
              setLines((open?.data_snapshot?.line_items ?? []) as InvoiceLine[]);
              setEditingLines(true);
            }}>{copy.editLines}</button>
          )
        ) : (
          <p className="hint">{copy.editLocked}</p>
        )}
        {open?.needs_reissue ? <p className="hint warn">{copy.needsReissue}</p> : null}
```

and, in the sheet's non-status action row (`RecordActions`-style, round 21A):

```tsx
          <button
            type="button"
            onClick={() => void sendToCustomer(open?.receipt_document_id ? "receipt" : "invoice")}
            disabled={busy || !open?.customer_has_line || !open?.generated_document_id}
          >
            {open?.receipt_document_id ? copy.sendReceipt : copy.send}
          </button>
          {!open?.customer_has_line ? <p className="hint">{copy.sendNoLine}</p> : null}
          {open?.customer_has_line && !open?.generated_document_id
            ? <p className="hint">{copy.sendNotIssued}</p> : null}
```

`customer_has_line` does not exist on the invoice payload yet. Add it in
`routers_phase2.py::get_invoice` (:2736) — read the customer once and attach
`{"customer_has_line": bool(customer.get("customer_chann_uid"))}` — rather than making the
browser fetch the customer to decide whether a button is enabled. `ProductLineForm`'s real
props are at `presentation/app/liff/_product-line-form.tsx:31`: read them and match, do not
guess (`QuoteDetail.tsx:732` is a working call site).

- [ ] **Step 5: The quote's send button**

In `QuoteDetail.tsx`, beside the document buttons (near `issuable`, :466), add the same
button calling `POST /api/phase2/licenses/${licenseId}/quotes/${quoteId}/send`, disabled when
the quote has no `generated_document_id` or the customer has no LINE, with the reason
rendered underneath. The quote detail already loads the customer
(`grep -n "customer" presentation/app/liff/sales/quotes/\[id\]/QuoteDetail.tsx | head`), so
read `customer_chann_uid` from what it has.

- [ ] **Step 6: Run the tests, the checkers and the build**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_invoice_ui.py -q
/tmp/dv/bin/python scripts/dev/check-parity.py | tail -3
/tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
/tmp/dv/bin/python scripts/dev/check-i18n-usage.py | tail -2
cd presentation && npm run typecheck && NEXT_TELEMETRY_DISABLED=1 npm run build > /tmp/21c-build.log 2>&1 || tail -20 /tmp/21c-build.log
```
Expected: tests passed; **`check-parity` prints "every capability is reachable from both
surfaces"** (this is the task where `quote.send`/`invoice.send` stop being chat-only);
typecheck and build clean.

- [ ] **Step 7: Look at it**

Open the DEV dashboard (or `npm run dev`) and open an invoice: the line editor appears only
when nothing is paid; the disabled send button says why underneath; a corrected invoice shows
the re-issue notice. **Look at it on a phone-width window** — the sheet is used on LINE.

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the invoice sheet corrects a bill and hands it to the customer, and so does the quote page" && git log --oneline -1
```

---

### Task 11: The five basic reports — Data tier

**Files:**
- Create: `data/chann_data/repositories/basic_reports.py`
- Modify: `data/chann_data/schemas.py` (`BasicReportOut`, `BasicReportRowOut`, `BasicReportHeadlineOut`)
- Modify: `data/chann_data/routers/internal.py` (one route beside the Phase 17 block at :6495)
- Test: `tests/integration/test_round21c_basic_reports.py`

**Interfaces:**
- Consumes: `deal_value_subquery` (Task 1), `Deal.closed_at` (Task 4), `phase17.date_window`.
- Produces: `BasicReportRepository(session).run(scope, key, *, today=None) -> dict`; `basic_reports.REPORT_KEYS`; `GET /internal/v1/licenses/{license_id}/reports/basic/{key}`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_round21c_basic_reports.py`:

```python
"""Round 21C — the five reports a shop asks for every day, computed by code.

No spec, no whitelist walk, no model in the number path (spec §5). These
are the numbers the owner said must always be right, so they are pinned
against a real database and against the pipeline card itself.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from chann_data.models import ChannIdentity, SatisfactionSurvey, ServiceTicket
from chann_data.repositories.basic_reports import REPORT_KEYS, BasicReportRepository
from chann_data.repositories.invoices import InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

TODAY = date(2026, 9, 23)


@pytest.fixture
def world(migrated_db):
    """One shop with something to report on, and a second shop whose rows
    must never appear in the first one's answers."""
    tag = uuid.uuid4().hex[:6]
    uids = {"a": f"CHN-21CB{tag}A", "b": f"CHN-21CB{tag}B"}
    with Session(migrated_db) as s:
        for key, uid in uids.items():
            s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                                primary_role="sales", display_name="เอ" if key == "a" else "บี"))
        s.commit()
    with Session(migrated_db) as s:
        reg = RegistrationRepository(s)
        a = reg.create_license(company_name=f"Basic A {tag}", created_by_chann_uid=uids["a"])
        b = reg.create_license(company_name=f"Basic B {tag}", created_by_chann_uid=uids["b"])
        ids = (a.id, b.id)
        s.commit()
    scope_a, scope_b = TenantScope(license_id=ids[0]), TenantScope(license_id=ids[1])
    with Session(migrated_db) as s:
        customers, deals = CustomerRepository(s), DealRepository(s)
        # 1 & 2 — deals: one open (30,000 of lines), one won this month
        # (99,000 typed), one won last month (10,000 typed).
        c1 = customers.create(scope_a, first_name="ลูกค้า", last_name="หนึ่ง", phone=f"081{tag[:7]}")
        s.flush()
        open_deal = deals.create(scope_a, contact_id=c1.id)
        s.flush()
        deals.add_product(scope_a, open_deal.id, product_id=None, product_name="แอร์",
                          quoted_unit_price=Decimal("15000.00"), qty=2)
        c2 = customers.create(scope_a, first_name="ลูกค้า", last_name="สอง", phone=f"082{tag[:7]}")
        s.flush()
        won_now = deals.create(scope_a, contact_id=c2.id, amount=Decimal("99000.00"))
        s.flush()
        won_now.stage = "won"
        won_now.closed_at = datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc)
        c3 = customers.create(scope_a, first_name="ลูกค้า", last_name="สาม", phone=f"083{tag[:7]}")
        s.flush()
        won_before = deals.create(scope_a, contact_id=c3.id, amount=Decimal("10000.00"))
        s.flush()
        won_before.stage = "won"
        won_before.closed_at = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
        # 3 — tickets: two open for one technician, one unassigned, one done.
        member = uuid.uuid4()
        for status_, assignee in (("open", member), ("in_progress", member),
                                  ("assigned", None), ("completed", member)):
            s.add(ServiceTicket(
                id=uuid.uuid4(), license_id=ids[0], ticket_number=f"T-{uuid.uuid4().hex[:6]}",
                issue_description="แอร์ไม่เย็น", status=status_, assigned_to_ref=assignee,
                contact_id=c1.id))
        # 4 — invoices: one overdue, one not yet due, one paid, one draft.
        invoices = InvoiceRepository(s)
        for due, total, paid, issue in ((date(2026, 9, 1), "10000.00", "0", True),
                                        (date(2026, 10, 30), "5000.00", "0", True),
                                        (date(2026, 9, 1), "7000.00", "7000.00", True),
                                        (None, "999.00", "0", False)):
            row = invoices.create(scope_a, contact_id=c1.id, subtotal=Decimal(total),
                                  total=Decimal(total), data_snapshot={"line_items": [
                                      {"line_no": 1, "product_name": "x", "qty": 1,
                                       "unit_price": total, "line_total": total}]})
            s.flush()
            if issue:
                invoices.issue(scope_a, row.id, issue_date=date(2026, 9, 1), due_date=due)
            if Decimal(paid) > 0:
                invoices.add_payment(scope_a, row.id, amount=Decimal(paid), method="cash")
        # 5 — surveys: 3 and 1 answered this month, one never answered.
        ticket = s.execute(
            ServiceTicket.__table__.select().where(ServiceTicket.license_id == ids[0]).limit(1)
        ).first()
        for score, submitted in ((3, datetime(2026, 9, 5, tzinfo=timezone.utc)),
                                 (1, datetime(2026, 9, 6, tzinfo=timezone.utc)),
                                 (None, None)):
            s.add(SatisfactionSurvey(
                id=uuid.uuid4(), license_id=ids[0], ticket_id=ticket.id,
                scale_config_json={"scale": 3}, score=score, submitted_at=submitted))
        # the neighbouring shop
        cb = customers.create(scope_b, first_name="อีกร้าน", last_name="ลูกค้า", phone=f"089{tag[:7]}")
        s.flush()
        other = deals.create(scope_b, contact_id=cb.id, amount=Decimal("500000.00"))
        s.flush()
        other.stage = "won"
        other.closed_at = datetime(2026, 9, 11, tzinfo=timezone.utc)
        s.commit()
    return migrated_db, scope_a, scope_b


class TestTheFive:
    def test_every_key_answers_with_the_same_envelope(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            for key in REPORT_KEYS:
                out = BasicReportRepository(s).run(scope, key, today=TODAY)
                assert out["key"] == key
                assert set(out) >= {"key", "title_th", "title_en", "unit", "headline",
                                    "rows", "notes_th", "notes_en", "generated_at"}
                assert set(out["headline"]) >= {"label_th", "label_en", "value"}

    def test_pipeline_value_is_the_pipeline_card(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "pipeline_value", today=TODAY)
            card = DealRepository(s).pipeline_summary(scope)
        assert Decimal(str(out["headline"]["value"])) == sum(
            Decimal(b["value"]) for b in card["by_stage"].values())
        assert {row["key"] for row in out["rows"]} == {"new", "proposed", "won", "lost"}

    def test_won_this_month_counts_by_closed_at_and_compares(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "won_this_month", today=TODAY)
        rows = {row["key"]: row for row in out["rows"]}
        assert Decimal(str(rows["this_month"]["value"])) == Decimal("99000")
        assert Decimal(str(rows["last_month"]["value"])) == Decimal("10000")
        assert rows["this_month"]["count"] == 1
        assert out["notes_th"], "the backfill caveat must be said in words"

    def test_open_jobs_count_three_statuses_and_keep_the_unassigned(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "open_jobs_by_tech", today=TODAY)
        assert out["headline"]["value"] == 3
        assert any(row["label_th"] == "ยังไม่มอบหมาย" and row["value"] == 1 for row in out["rows"])

    def test_outstanding_splits_overdue_from_not_yet_due(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "outstanding_invoices", today=TODAY)
        rows = {row["key"]: row for row in out["rows"]}
        assert Decimal(str(rows["overdue"]["value"])) == Decimal("10000")
        assert Decimal(str(rows["not_due"]["value"])) == Decimal("5000")
        assert Decimal(str(out["headline"]["value"])) == Decimal("15000")

    def test_satisfaction_averages_only_answered_forms(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "satisfaction_avg", today=TODAY)
        assert out["headline"]["value"] == 2.0          # (3 + 1) / 2, not / 3
        assert out["rows"][0]["count"] == 2

    def test_an_unknown_key_is_refused(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            with pytest.raises(ValueError, match="unknown report"):
                BasicReportRepository(s).run(scope, "ยอดขายของคู่แข่ง", today=TODAY)


class TestTenantIsolation:
    def test_the_other_shop_is_never_in_the_answer(self, world):
        engine, scope_a, scope_b = world
        with Session(engine) as s:
            mine = BasicReportRepository(s).run(scope_a, "won_this_month", today=TODAY)
            theirs = BasicReportRepository(s).run(scope_b, "won_this_month", today=TODAY)
        assert Decimal(str(mine["headline"]["value"])) == Decimal("99000")
        assert Decimal(str(theirs["headline"]["value"])) == Decimal("500000")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_basic_reports.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'chann_data.repositories.basic_reports'`.
(If the `ServiceTicket(...)` constructor rejects a column, read the model at
`data/chann_data/models.py:632-668` and pass what it requires — the test is the first
consumer and must match the real columns.)

- [ ] **Step 3: Write the repository**

`data/chann_data/repositories/basic_reports.py`:

```python
"""The five questions a shop asks every day (round 21C).

Owner, 23 ก.ย. 2569: these five must be right, always. So there is no
spec, no whitelist walk and no model anywhere in the number path — the
model only reads a sentence and says WHICH of the five it is, which is
model-first without handing arithmetic to anyone (docs/MODEL_FIRST.md).

Every one returns the same envelope, so the chat reply and the dashboard
card render from one payload and cannot disagree about a total. Nothing
downstream recomputes: the differences and the percentages are worked out
here, in Decimal, not in TypeScript and not in a reply builder (the round
20K lesson).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ChannIdentity, Deal, Invoice, LicenseMember, SatisfactionSurvey, ServiceTicket,
    TechnicianTeam,
)
from .deal_value import deal_value_subquery
from .phase17 import BANGKOK, date_window
from .phase9 import DEAL_STAGES, DealRepository
from .tenant_scope import TenantScope

REPORT_KEYS = (
    "pipeline_value", "won_this_month", "open_jobs_by_tech",
    "outstanding_invoices", "satisfaction_avg",
)
OPEN_TICKET_STATUSES = ("open", "assigned", "in_progress")
OPEN_INVOICE_STATUSES = ("issued", "partially_paid")
STAGE_WORDS = {
    "new": ("ใหม่", "New"), "proposed": ("เสนอราคาแล้ว", "Proposed"),
    "won": ("ปิดสำเร็จ", "Won"), "lost": ("ไม่สำเร็จ", "Lost"),
}
UNASSIGNED = ("ยังไม่มอบหมาย", "Unassigned")


def _row(key: str, label_th: str, label_en: str, value, count: int | None = None) -> dict:
    row = {"key": key, "label_th": label_th, "label_en": label_en, "value": float(value or 0)}
    if count is not None:
        row["count"] = int(count)
    return row


def _envelope(key: str, title_th: str, title_en: str, unit: str, headline: dict,
              rows: list[dict], notes_th: list[str] | None = None,
              notes_en: list[str] | None = None) -> dict:
    return {
        "key": key, "title_th": title_th, "title_en": title_en, "unit": unit,
        "headline": headline, "rows": rows,
        "notes_th": notes_th or [], "notes_en": notes_en or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _headline(label_th: str, label_en: str, value) -> dict:
    return {"label_th": label_th, "label_en": label_en, "value": float(value or 0)}


class BasicReportRepository:
    def __init__(self, session: Session):
        self._s = session

    def run(self, scope: TenantScope, key: str, *, today: date | None = None) -> dict:
        if key not in REPORT_KEYS:
            raise ValueError(f"unknown report: {key!r}")
        return getattr(self, f"_{key}")(scope, today=today)

    # ---------------------------------------------------------------- 1
    def _pipeline_value(self, scope: TenantScope, *, today: date | None = None) -> dict:
        """Straight from the pipeline card, reshaped — not a second query.
        Two queries that agree today are two queries that will disagree
        one day (diagnosis §1d)."""
        card = DealRepository(self._s).pipeline_summary(scope)
        rows = [
            _row(stage, *STAGE_WORDS[stage],
                 value=Decimal(card["by_stage"].get(stage, {}).get("value") or 0),
                 count=int(card["by_stage"].get(stage, {}).get("count") or 0))
            for stage in ("new", "proposed", "won", "lost") if stage in DEAL_STAGES
        ]
        total = sum((Decimal(b["value"]) for b in card["by_stage"].values()), Decimal("0"))
        return _envelope(
            "pipeline_value", "มูลค่าดีลทั้งหมด", "Pipeline value", "money",
            _headline("มูลค่ารวมทุกดีล", "All deals", total), rows,
            notes_th=[f"ยังเปิดอยู่ {card['open_value']} บาท · คาดว่าจะปิดเดือนนี้ {card['closing_this_month']} บาท"],
            notes_en=[f"Open {card['open_value']} · forecast to close this month {card['closing_this_month']}"],
        )

    # ---------------------------------------------------------------- 2
    def _won_this_month(self, scope: TenantScope, *, today: date | None = None) -> dict:
        rows = []
        for range_key, th, en in (("this_month", "เดือนนี้", "This month"),
                                  ("last_month", "เดือนที่แล้ว", "Last month")):
            start, end = date_window(range_key, today=today)
            inner = (
                deal_value_subquery()
                .where(
                    Deal.license_id == scope.license_id,
                    Deal.archived_at.is_(None),
                    Deal.stage == "won",
                    Deal.closed_at >= start,
                    Deal.closed_at < end,
                )
                .subquery()
            )
            value = self._s.execute(
                select(func.coalesce(func.sum(inner.c.value), 0))).scalar_one()
            count = self._s.execute(select(func.count()).select_from(inner)).scalar_one()
            rows.append(_row(range_key, th, en, value, count))
        now, before = Decimal(str(rows[0]["value"])), Decimal(str(rows[1]["value"]))
        difference = now - before
        percent = (difference / before * 100) if before > 0 else None
        moved_th = f"{'มากกว่า' if difference >= 0 else 'น้อยกว่า'}เดือนที่แล้ว {abs(difference):,.2f} บาท"
        if percent is not None:
            moved_th += f" ({abs(percent):,.0f}%)"
        return _envelope(
            "won_this_month", "ยอดปิดสำเร็จเดือนนี้", "Won this month", "money",
            _headline("ปิดได้เดือนนี้", "Won this month", now), rows,
            notes_th=[moved_th,
                      "นับตามวันที่ปิดจริง · ดีลที่ปิดก่อน 23 ก.ย. 2569 ใช้วันที่แก้ไขล่าสุดเป็นวันปิด"],
            notes_en=["By the real close date; deals closed before 23 Sep 2026 use their last-updated date."],
        )

    # ---------------------------------------------------------------- 3
    def _open_jobs_by_tech(self, scope: TenantScope, *, today: date | None = None) -> dict:
        found = self._s.execute(
            select(ServiceTicket.assigned_to_ref, func.count().label("n"))
            .where(
                ServiceTicket.license_id == scope.license_id,
                ServiceTicket.status.in_(OPEN_TICKET_STATUSES),
            )
            .group_by(ServiceTicket.assigned_to_ref)
            .order_by(func.count().desc())
        ).all()
        rows = [
            _row(str(ref) if ref else "unassigned", *self._who(scope, ref), value=n, count=int(n))
            for ref, n in found
        ]
        return _envelope(
            "open_jobs_by_tech", "งานซ่อมค้างแยกตามช่าง", "Open jobs by technician", "count",
            _headline("งานค้างทั้งหมด", "Open jobs", sum(int(n) for _, n in found)), rows,
            notes_th=["นับสถานะ เปิดอยู่ · มอบหมายแล้ว · กำลังทำ"],
            notes_en=["Counting open, assigned and in progress."],
        )

    def _who(self, scope: TenantScope, ref) -> tuple[str, str]:
        """A job is assigned to a person or to a team, and both are just a
        uuid in `assigned_to_ref` — so both are looked up, and an id that
        matches neither is shown as an id rather than dropped."""
        if ref is None:
            return UNASSIGNED
        member = self._s.get(LicenseMember, ref) if isinstance(ref, uuid.UUID) else None
        if member is not None and member.license_id == scope.license_id:
            identity = self._s.get(ChannIdentity, member.chann_uid)
            name = (identity.display_name if identity else None) or member.chann_uid
            return (name, name)
        team = self._s.get(TechnicianTeam, ref) if isinstance(ref, uuid.UUID) else None
        if team is not None and team.license_id == scope.license_id:
            return (f"ทีม {team.team_name}", f"Team {team.team_name}")
        return (str(ref)[:8], str(ref)[:8])

    # ---------------------------------------------------------------- 4
    def _outstanding_invoices(self, scope: TenantScope, *, today: date | None = None) -> dict:
        day = today or datetime.now(BANGKOK).date()
        outstanding = Invoice.total - Invoice.paid_amount
        base = (
            select(func.coalesce(func.sum(outstanding), 0), func.count())
            .where(
                Invoice.license_id == scope.license_id,
                Invoice.archived_at.is_(None),
                Invoice.status.in_(OPEN_INVOICE_STATUSES),
            )
        )
        overdue_value, overdue_count = self._s.execute(
            base.where(Invoice.due_date.is_not(None), Invoice.due_date < day)).one()
        not_due_value, not_due_count = self._s.execute(
            base.where((Invoice.due_date.is_(None)) | (Invoice.due_date >= day))).one()
        rows = [
            _row("overdue", "เลยกำหนด", "Overdue", overdue_value, overdue_count),
            _row("not_due", "ยังไม่ถึงกำหนด", "Not yet due", not_due_value, not_due_count),
        ]
        total = Decimal(str(overdue_value or 0)) + Decimal(str(not_due_value or 0))
        return _envelope(
            "outstanding_invoices", "ยอดค้างชำระ", "Outstanding invoices", "money",
            _headline("ค้างชำระรวม", "Outstanding", total), rows,
            notes_th=["นับใบที่ออกแล้วและชำระบางส่วน · เลยกำหนดคิดจากวันครบกำหนดตามเวลาไทย"],
            notes_en=["Issued and partially paid bills; overdue is measured against Bangkok's today."],
        )

    # ---------------------------------------------------------------- 5
    def _satisfaction_avg(self, scope: TenantScope, *, today: date | None = None) -> dict:
        rows = []
        for range_key, th, en in (("this_month", "เดือนนี้", "This month"),
                                  ("last_month", "เดือนที่แล้ว", "Last month")):
            start, end = date_window(range_key, today=today)
            average, answered = self._s.execute(
                select(func.avg(SatisfactionSurvey.score), func.count())
                .where(
                    SatisfactionSurvey.license_id == scope.license_id,
                    SatisfactionSurvey.submitted_at.is_not(None),
                    SatisfactionSurvey.score.is_not(None),
                    SatisfactionSurvey.submitted_at >= start,
                    SatisfactionSurvey.submitted_at < end,
                )
            ).one()
            rows.append(_row(range_key, th, en,
                             round(float(average), 2) if average is not None else 0, answered))
        return _envelope(
            "satisfaction_avg", "คะแนนความพึงพอใจเฉลี่ย", "Average satisfaction", "score",
            _headline("เฉลี่ยเดือนนี้", "This month", rows[0]["value"]), rows,
            # A mean of two answers is not a verdict, and the reader should
            # be able to see that without opening the survey list.
            notes_th=[f"จากใบที่ตอบแล้ว {rows[0].get('count', 0)} ใบเดือนนี้ (เต็ม 3)"],
            notes_en=[f"From {rows[0].get('count', 0)} answered forms this month (out of 3)."],
        )
```

- [ ] **Step 4: The schemas and the route**

`data/chann_data/schemas.py`, beside `ReportResultOut` (:1499):

```python
class BasicReportHeadlineOut(BaseModel):
    label_th: str
    label_en: str
    value: float


class BasicReportRowOut(BaseModel):
    key: str
    label_th: str
    label_en: str
    value: float
    count: int | None = None


class BasicReportOut(BaseModel):
    """Round 21C — one envelope for all five, so the chat reply and the
    dashboard card render the same numbers from the same payload."""
    key: str
    title_th: str
    title_en: str
    unit: str
    headline: BasicReportHeadlineOut
    rows: list[BasicReportRowOut]
    notes_th: list[str] = []
    notes_en: list[str] = []
    generated_at: str
```

`data/chann_data/routers/internal.py`, beside `run_report_query` (:6498):

```python
@router.get("/licenses/{license_id}/reports/basic/{key}", response_model=BasicReportOut)
def run_basic_report(
    license_id: uuid.UUID, key: str, session: Session = Depends(get_session),
):
    """One of the five fixed reports. No spec, no model — the whole point
    (round 21C)."""
    scope = TenantScope(license_id=license_id)
    try:
        return BasicReportOut(**BasicReportRepository(session).run(scope, key))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "unknown_report", "message": str(exc)})
```

- [ ] **Step 5: Run the tests**

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration/test_round21c_basic_reports.py -q
/tmp/dv/bin/python scripts/dev/check-fields.py | tail -2
```
Expected: all passed; `check-fields` clean.

- [ ] **Step 6: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the five everyday reports, computed by code on the Data tier" && git log --oneline -1
```

---

### Task 12: The five basic reports — Application tier, and the model that only chooses

**Files:**
- Create: `application/chann_app/services/basic_reports.py`
- Modify: `application/chann_app/data_client.py` (add `basic_report`)
- Modify: `application/chann_app/routers_phase2.py` (one route beside the Phase 17 block at :5084)
- Test: `tests/unit/test_round21c_basic_reports.py`

**Interfaces:**
- Consumes: `GET /internal/v1/licenses/{id}/reports/basic/{key}` (Task 11).
- Produces:
  - `basic_reports.REPORT_KEYS`, `basic_reports.TITLES`
  - `basic_reports.choose_report(message: str, *, client=None, language="th") -> str | None` (the model's only job)
  - `basic_reports.fetch(client, *, license_id, key) -> dict`
  - `basic_reports.as_text(report: dict, language: str) -> str`
  - `GET /api/v1/licenses/{license_id}/reports/basic/{key}`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_basic_reports.py`:

```python
"""Round 21C — the model picks one of five; the numbers are already made."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import basic_reports  # noqa: E402

REPORT = {
    "key": "outstanding_invoices", "title_th": "ยอดค้างชำระ",
    "title_en": "Outstanding invoices", "unit": "money",
    "headline": {"label_th": "ค้างชำระรวม", "label_en": "Outstanding", "value": 15000.0},
    "rows": [
        {"key": "overdue", "label_th": "เลยกำหนด", "label_en": "Overdue", "value": 10000.0, "count": 1},
        {"key": "not_due", "label_th": "ยังไม่ถึงกำหนด", "label_en": "Not yet due", "value": 5000.0, "count": 1},
    ],
    "notes_th": ["นับใบที่ออกแล้วและชำระบางส่วน"], "notes_en": ["Issued and partially paid."],
    "generated_at": "2026-09-23T04:00:00+00:00",
}


class FakeAi:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        return self.answer


class TestChoosing:
    @pytest.mark.asyncio
    async def test_the_model_returns_a_key_and_only_a_key(self):
        ai = FakeAi('{"report": "outstanding_invoices"}')
        assert await basic_reports.choose_report("ใครยังไม่จ่ายบ้าง", client=ai) == "outstanding_invoices"

    @pytest.mark.asyncio
    async def test_a_key_outside_the_five_is_refused_not_guessed(self):
        ai = FakeAi('{"report": "ยอดขายของคู่แข่ง"}')
        assert await basic_reports.choose_report("อะไรก็ไม่รู้", client=ai) is None

    @pytest.mark.asyncio
    async def test_a_number_in_the_answer_is_ignored_entirely(self):
        """The model may not compute. Even if it volunteers a total, the
        chooser keeps the key and nothing else."""
        ai = FakeAi('{"report": "pipeline_value", "total": 999999}')
        assert await basic_reports.choose_report("ยอดในท่อ", client=ai) == "pipeline_value"


class TestRendering:
    def test_the_text_says_the_headline_the_rows_and_the_note(self):
        said = basic_reports.as_text(REPORT, "th")
        assert "ยอดค้างชำระ" in said
        assert "15,000" in said and "10,000" in said
        assert "เลยกำหนด" in said
        assert "นับใบที่ออกแล้ว" in said

    def test_it_stays_inside_the_reply_ceiling(self):
        assert len(basic_reports.as_text(REPORT, "th").splitlines()) <= 8

    def test_a_count_report_is_not_printed_as_money(self):
        counted = {**REPORT, "unit": "count",
                   "headline": {"label_th": "งานค้าง", "label_en": "Open", "value": 3.0},
                   "rows": [{"key": "a", "label_th": "สมชาย", "label_en": "Somchai", "value": 3.0, "count": 3}]}
        said = basic_reports.as_text(counted, "th")
        assert "บาท" not in said
        assert "3 งาน" in said
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_basic_reports.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'chann_app.services.basic_reports'`.

- [ ] **Step 3: Write the service**

`application/chann_app/services/basic_reports.py`:

```python
"""The five fixed reports, on the Application side (round 21C).

The model's ONLY job here is to read a sentence and name one of five
keys. It never sees a number, never produces a spec, and anything it says
beyond the key is thrown away. That is model-first (the model reads, the
code acts) with the arithmetic kept where it belongs (docs/MODEL_FIRST.md,
and the round 20K "dumped arithmetic" lesson).

These five never spend a credit: no picture is drawn and no report spec is
generated, so `chart_quota.spend_one` is not called from this module at
all (spec §5).
"""
from __future__ import annotations

import json
import logging

from ..data_client import DataClient
from .ai.client import AINotConfigured, AIUnavailable

log = logging.getLogger(__name__)

REPORT_KEYS = (
    "pipeline_value", "won_this_month", "open_jobs_by_tech",
    "outstanding_invoices", "satisfaction_avg",
)
TITLES = {
    "pipeline_value": {"th": "มูลค่าดีลทั้งหมด", "en": "Pipeline value"},
    "won_this_month": {"th": "ยอดปิดสำเร็จเดือนนี้", "en": "Won this month"},
    "open_jobs_by_tech": {"th": "งานซ่อมค้างแยกตามช่าง", "en": "Open jobs by technician"},
    "outstanding_invoices": {"th": "ยอดค้างชำระ", "en": "Outstanding invoices"},
    "satisfaction_avg": {"th": "คะแนนความพึงพอใจเฉลี่ย", "en": "Average satisfaction"},
}
BLURB = {
    "pipeline_value": {"th": "ทุกดีลที่ยังไม่ถูกลบ แยกตามขั้น", "en": "Every live deal, by stage"},
    "won_this_month": {"th": "เทียบกับเดือนที่แล้ว ตามวันที่ปิดจริง", "en": "Against last month, by close date"},
    "open_jobs_by_tech": {"th": "เปิดอยู่ · มอบหมายแล้ว · กำลังทำ", "en": "Open, assigned, in progress"},
    "outstanding_invoices": {"th": "ออกแล้ว/ชำระบางส่วน แยกเลยกำหนด", "en": "Issued and part-paid, overdue split out"},
    "satisfaction_avg": {"th": "เฉพาะใบที่ลูกค้าตอบแล้ว", "en": "Answered forms only"},
}

CHOOSE_PROMPT = (
    "The shop staff asked something. Decide which ONE of these five fixed reports answers it, "
    "or none.\n"
    "- pipeline_value: มูลค่าดีลทั้งหมด, ยอดในท่อ, ดีลรวมเท่าไหร่, pipeline\n"
    "- won_this_month: ยอดปิดสำเร็จเดือนนี้, ปิดได้เท่าไหร่, เทียบเดือนที่แล้ว, won this month\n"
    "- open_jobs_by_tech: งานซ่อมค้าง, งานค้างของช่าง, ช่างแต่ละคนมีงานกี่งาน\n"
    "- outstanding_invoices: ยอดค้างชำระ, ใครยังไม่จ่าย, บิลค้าง, overdue\n"
    "- satisfaction_avg: คะแนนความพึงพอใจ, ลูกค้าให้คะแนนเท่าไหร่\n\n"
    'Reply with JSON only: {"report": "<one key>"} or {"report": null}. '
    "Never include a number: you are not being asked for the answer, only for which report it is."
)


async def choose_report(message: str, *, client=None, language: str = "th") -> str | None:
    """Which of the five, or None. The model reads; the code decides."""
    from .ai.client import complete_json

    try:
        raw = await complete_json(CHOOSE_PROMPT, message, client=client)
    except (AINotConfigured, AIUnavailable):
        return None
    except Exception:  # noqa: BLE001
        log.exception("could not read which basic report was asked for")
        return None
    try:
        data = raw if isinstance(raw, dict) else json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    key = str((data or {}).get("report") or "").strip()
    # Anything else the model said — a total, an explanation, a sixth
    # report it invented — is discarded here, deliberately.
    return key if key in REPORT_KEYS else None


async def fetch(client: DataClient, *, license_id: str, key: str) -> dict:
    if key not in REPORT_KEYS:
        raise ValueError(f"unknown report: {key!r}")
    return await client.basic_report(str(license_id), key)


def _amount(value: float, unit: str, language: str) -> str:
    if unit == "money":
        return f"{value:,.2f} บาท" if language != "en" else f"{value:,.2f}"
    if unit == "score":
        return f"{value:,.2f}"
    whole = int(round(value))
    return f"{whole:,} งาน" if language != "en" else f"{whole:,}"


def as_text(report: dict, language: str) -> str:
    """At most eight lines: the title, the headline, the rows, one note.

    The 15-line ceiling `simulate-phrasings --real` measures is the hard
    limit; a report a person has to scroll is one they stop asking for.
    """
    th = language != "en"
    unit = str(report.get("unit") or "count")
    lines = [f"{report['title_th'] if th else report['title_en']}"]
    head = report.get("headline") or {}
    lines.append(
        f"{head.get('label_th') if th else head.get('label_en')}: "
        f"{_amount(float(head.get('value') or 0), unit, language)}"
    )
    for row in (report.get("rows") or [])[:4]:
        label = row.get("label_th") if th else row.get("label_en")
        said = _amount(float(row.get("value") or 0), unit, language)
        if unit != "count" and row.get("count") is not None:
            said += f" ({int(row['count'])} ใบ)" if th else f" ({int(row['count'])})"
        lines.append(f"· {label}: {said}")
    notes = report.get("notes_th" if th else "notes_en") or []
    if notes:
        lines.append(notes[0])
    return "\n".join(lines)
```

Two names to confirm before writing, because this module is the first caller outside `ai/`:
`grep -n "async def complete_json\|async def complete" application/chann_app/services/ai/client.py`.
If `complete_json` does not exist, use whatever `reports_ai.generate_query_spec` (:316) calls
and pass its output through `reports_ai.extract_json` (:297) — **do not invent a new AI entry
point for this**.

- [ ] **Step 4: The client method and the route**

`application/chann_app/data_client.py`, beside `run_report_query` (:912):

```python
    async def basic_report(self, license_id: str, key: str) -> dict:
        resp = await self._client.get(
            f"{self._base}/internal/v1/licenses/{license_id}/reports/basic/{key}",
            headers=self._headers,
        )
        return self._unwrap(resp)
```

`application/chann_app/routers_phase2.py`, beside `ai_report_options` (:5149):

```python
@router.get("/licenses/{license_id}/reports/basic/{key}")
async def basic_report(
    license_id: str, key: str,
    principal: TenantPrincipal = Depends(get_tenant_principal),
    client: DataClient = Depends(get_data_client),
):
    """One of the five fixed reports. No model call, and — deliberately —
    no `_charge_for_the_picture`: these never cost a credit (spec §5)."""
    from .services import basic_reports

    _require_same_tenant(principal, license_id)
    principal.require("view_reports")
    try:
        return await basic_reports.fetch(client, license_id=license_id, key=key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DataTierError as exc:
        raise _propagate(exc)
```

- [ ] **Step 5: Run the tests and the checkers**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_basic_reports.py -q
/tmp/dv/bin/python scripts/dev/check-client.py | tail -2
/tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
```
Expected: tests passed; both checkers clean.

- [ ] **Step 6: Ask the real model which report each sentence is**

```bash
cd ~/stage-fix/r21c && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/ask-model.py \
  "ยอดมูลค่าดีลทั้งหมด" "ยอดในท่อตอนนี้" "เดือนนี้ปิดได้เท่าไหร่ เทียบเดือนที่แล้ว" \
  "งานซ่อมค้างแยกตามช่าง" "ช่างแต่ละคนมีงานค้างกี่งาน" "ใครยังไม่จ่ายบ้าง" \
  "ยอดค้างชำระ" "คะแนนความพึงพอใจเฉลี่ย" "ลูกค้าให้คะแนนเท่าไหร่"
```
Nine sentences, three phrasings for the ones with several. **Every one must come back as the
right key.** If one does not, fix `CHOOSE_PROMPT` — not a keyword table
(`docs/MODEL_FIRST.md` step 2). Record the before/after in the handoff.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the model picks which of the five reports, and never touches the number" && git log --oneline -1
```

---

### Task 13: Chat — the five reports, reached before Phase 17 ever runs

**Files:**
- Modify: `application/chann_app/services/chat.py` — `_handle_ai_report` (:29810), new `_handle_basic_report`, the buttons
- Create: `scripts/agent-test/scenarios/round21c-reports.yaml`
- Test: `tests/unit/test_round21c_reports_chat.py`

**Interfaces:**
- Consumes: `basic_reports.choose_report/fetch/as_text/TITLES/BLURB` (Task 12).
- Produces: `chat._handle_basic_report(client, *, ctx, license_id, key, permission_keys, language, with_chart=False) -> ChatReply`; postback sentences `"รายงาน: <key>"`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_round21c_reports_chat.py`:

```python
"""Round 21C — the five reports in chat: free, fixed, and reachable by button."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services import chat  # noqa: E402

REPORT = {
    "key": "pipeline_value", "title_th": "มูลค่าดีลทั้งหมด", "title_en": "Pipeline value",
    "unit": "money",
    "headline": {"label_th": "มูลค่ารวมทุกดีล", "label_en": "All deals", "value": 379000.0},
    "rows": [{"key": "new", "label_th": "ใหม่", "label_en": "New", "value": 379000.0, "count": 3}],
    "notes_th": ["ยังเปิดอยู่ 379000 บาท"], "notes_en": ["Open 379000"],
    "generated_at": "2026-09-23T04:00:00+00:00",
}


class FakeClient:
    def __init__(self):
        self.asked = []
        self.quota_spent = 0

    async def basic_report(self, license_id, key):
        self.asked.append(key)
        return REPORT

    async def consume_ai_chart_quota(self, license_id, month):
        self.quota_spent += 1
        return {"allowed": True, "used": self.quota_spent, "allowance": 30}


class TestTheFiveAreFree:
    @pytest.mark.asyncio
    async def test_a_basic_report_never_spends_a_credit(self, monkeypatch):
        client = FakeClient()
        reply = await chat._handle_basic_report(
            client, ctx=chat_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        assert "379,000.00" in reply.text
        assert client.quota_spent == 0

    @pytest.mark.asyncio
    async def test_without_view_reports_it_says_so_and_asks_nothing(self):
        client = FakeClient()
        reply = await chat._handle_basic_report(
            client, ctx=chat_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=[], language="th")
        assert client.asked == []
        assert "สิทธิ์" in reply.text

    @pytest.mark.asyncio
    async def test_the_other_four_are_offered_as_buttons(self):
        reply = await chat._handle_basic_report(
            FakeClient(), ctx=chat_ctx(), license_id="L1", key="pipeline_value",
            permission_keys=["view_reports"], language="th")
        said = [label for label, _ in reply.quick_replies]
        assert "ยอดค้างชำระ" in said
        assert any("รูป" in label for label in said)


def chat_ctx():
    """The smallest ResolvedContext the handler reads: oa, chann_uid,
    memberships. Build it the way tests/unit/test_round20v_invoices.py
    does — read that file and reuse its helper rather than a second one."""
    raise NotImplementedError
```

Replace `chat_ctx()` with the helper the existing chat tests already use:
`grep -rn "def _ctx\|ResolvedContext(" tests/unit/test_round20v_invoices.py | head`.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_reports_chat.py -q
```
Expected: FAIL — `AttributeError: module 'chann_app.services.chat' has no attribute '_handle_basic_report'`.

- [ ] **Step 3: The handler**

In `application/chann_app/services/chat.py`, beside `_handle_ai_report`:

```python
#: The postback a button sends. A button names the action and the record,
#: so it keeps the direct path (docs/MODEL_FIRST.md) — but it is still a
#: sentence, so a person can type it too.
BASIC_REPORT_PREFIX = "รายงาน:"
BASIC_REPORT_PICTURE = {"th": "ดูเป็นรูป", "en": "As a picture"}
BASIC_REPORT_ASK_MORE = {"th": "ถามรายงานอื่น", "en": "Ask something else"}


def _basic_report_key(message: str) -> str | None:
    from . import basic_reports

    text = (message or "").strip()
    if not text.startswith(BASIC_REPORT_PREFIX):
        return None
    key = text[len(BASIC_REPORT_PREFIX):].strip()
    return key if key in basic_reports.REPORT_KEYS else None


async def _handle_basic_report(
    client: DataClient, *, ctx: ResolvedContext, license_id, key: str,
    permission_keys: list[str], language: str, with_chart: bool = False,
) -> ChatReply:
    """One of the five. Code computes it; the model has already done its
    only job, which was choosing which one. Never metered (spec §5)."""
    from . import basic_reports

    if "view_reports" not in set(permission_keys):
        return ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))
    try:
        report = await basic_reports.fetch(client, license_id=str(license_id), key=key)
    except DataTierError:
        log.exception("basic report %s failed for %s", key, ctx.chann_uid)
        return ChatReply(text=_t(AI_REPORT_UNAVAILABLE, language))
    text = basic_reports.as_text(report, language)
    buttons: list[tuple[str, str]] = [
        (_t(BASIC_REPORT_PICTURE, language), f"{BASIC_REPORT_PREFIX} {key} เป็นรูป"),
    ]
    for other in basic_reports.REPORT_KEYS:
        if other != key and len(buttons) < 4:
            buttons.append((basic_reports.TITLES[other]["th" if language != "en" else "en"],
                            f"{BASIC_REPORT_PREFIX} {other}"))
    images: list[str] = []
    if with_chart:
        from . import chart_plan

        url, _plottable = await chart_plan.publish_for_basic_report(
            client, report=report, license_id=str(license_id), language=language)
        if url:
            images = [url]
            text += _t(CHART_ALSO_AS_A_LINK, language).format(url=url)
        else:
            text += _t(reports_ai.CHART_UNAVAILABLE, language)
    return ChatReply(text=text, images=images, quick_replies=buttons,
                     intent={"action": "report", "entity": key})
```

(`chart_plan.publish_for_basic_report` arrives in Task 16. Until then, guard the import with
the `with_chart` branch as written — the five in words work without it, which is the point.)

- [ ] **Step 4: Reach it before Phase 17**

In `_handle_ai_report` (:29810), immediately after the `view_reports` check and before the
`reports_ai.handle_report_request` call:

```python
    # Round 21C: five questions are answered by code, not by a spec. The
    # model still READS the sentence — it just answers with a key instead
    # of arithmetic — so this is model-first, and it is free.
    from . import basic_reports

    wants_picture = with_chart
    fixed = _basic_report_key(message)
    if fixed is None:
        fixed = await basic_reports.choose_report(message, client=ai_client, language=language)
    if fixed is not None:
        return await _handle_basic_report(
            client, ctx=ctx, license_id=license_id, key=fixed,
            permission_keys=permission_keys, language=language, with_chart=wants_picture)
```

and, at the top of `_route_chat_message`'s postback handling, let `รายงาน: <key>` through
without a model call (it is a button):
`grep -n "_is_ai_report_request(message)" application/chann_app/services/chat.py` — at each of
those four call sites, `_basic_report_key(message) is not None` must also route to
`_handle_ai_report`. The sentence `"รายงาน: pipeline_value เป็นรูป"` sets `with_chart=True`
through the existing "เป็นกราฟ/เป็นรูป" suffix handling.

- [ ] **Step 5: The scenario**

Create `scripts/agent-test/scenarios/round21c-reports.yaml` (same shape as
`round21c-invoice-edit.yaml` in Task 9): seed a shop with two deals (one with lines, one
won this month), two open tickets and one overdue invoice; then

```yaml
- send:
    message: ยอดมูลค่าดีลทั้งหมด
    expect_text: ['มูลค่าดีลทั้งหมด']
- send:
    message: งานซ่อมค้างแยกตามช่าง
    expect_text: ['งานค้างทั้งหมด']
- send:
    message: ยอดค้างชำระ
    expect_text: ['เลยกำหนด']
- send:
    message: 'รายงาน: won_this_month'
    expect_text: ['เดือนที่แล้ว']
```

- [ ] **Step 6: Run everything**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_reports_chat.py tests/unit/test_ai_reports.py -q
/tmp/dv/bin/python scripts/agent-test/run.py --only round21c-reports | tail -3
/tmp/dv/bin/python scripts/dev/simulate-phrasings.py | grep -E "^=== [0-9]+ cases"
/tmp/dv/bin/python scripts/dev/measure-road-share.py | tail -4
/tmp/dv/bin/python scripts/dev/check-parity.py | tail -3
```
Expected: tests passed; scenario passed; phrasings `0 long` and not worse; road share not
shrunk; parity clean.

- [ ] **Step 7: A real conversation, not just sentences**

```bash
cd ~/stage-fix/r21c && OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python ~/stage-fix/tools/converse.py \
  --repo ~/stage-fix/r21c --area reports 2>&1 | tail -40
```
Read the transcript. A report that answers a question the person did not ask, or a button
that leads nowhere, shows up here and nowhere else.

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the five reports answer in chat, free, with the other four a tap away" && git log --oneline -1
```

---

### Task 14: Dashboard — five cards above the question box

**Files:**
- Modify: `presentation/app/liff/sales/reports/ai/AiReports.tsx`
- Modify: `presentation/lib/i18n/th.ts`, `presentation/lib/i18n/en.ts` (`dashboard.aiReports.basic`)
- Modify: `presentation/app/globals.css` (the card grid, if no existing class fits)
- Test: `tests/unit/test_round21c_reports_ui.py`

**Interfaces:**
- Consumes: `GET /api/phase2/licenses/{id}/reports/basic/{key}` (Task 12).

- [ ] **Step 0: The design pass**

Run **ui-ux-pro-max** for this screen first (owner's standing rule). What it decides here:
five cards in a responsive grid (one column on a phone, two from 480px, three from 768px);
the number is the card's largest element, right-aligned and `font-variant-numeric:
tabular-nums`; the blurb under the title says what the number counts, because "ยอดค้างชำระ
15,000" without "ออกแล้ว/ชำระบางส่วน" is a number people argue about; a skeleton while it
loads, and the card keeps its height so the grid does not jump; **the question box below is
unchanged and is still the page's single primary action.**

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_round21c_reports_ui.py`:

```python
"""Round 21C — the five reports are one tap from the reports page."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "presentation/app/liff/sales/reports/ai/AiReports.tsx").read_text(encoding="utf-8")
TH = (ROOT / "presentation/lib/i18n/th.ts").read_text(encoding="utf-8")
EN = (ROOT / "presentation/lib/i18n/en.ts").read_text(encoding="utf-8")

KEYS = ["pipeline_value", "won_this_month", "open_jobs_by_tech",
        "outstanding_invoices", "satisfaction_avg"]


class TestTheCards:
    def test_all_five_are_on_the_page(self):
        for key in KEYS:
            assert key in PAGE, key

    def test_they_call_the_free_route_not_the_ai_one(self):
        assert "/reports/basic/" in PAGE

    def test_the_numbers_come_from_the_server(self):
        # No arithmetic in the browser: no total, no percentage, no
        # difference is computed here (round 20K).
        assert "headline" in PAGE and "notes_th" in PAGE

    def test_both_languages_have_the_strings(self):
        for key in KEYS:
            assert key in TH and key in EN
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_reports_ui.py -q
```
Expected: FAIL — `assert 'pipeline_value' in PAGE`.

- [ ] **Step 3: The strings**

`presentation/lib/i18n/th.ts`, inside `dashboard.aiReports`:

```ts
      basic: {
        heading: "รายงานที่ใช้บ่อย",
        intro: "ห้ารายงานนี้ระบบคำนวณให้เอง ไม่ใช้เครดิต AI และตอบเหมือนกันทุกครั้ง",
        picture: "ดูเป็นรูป",
        pipeline_value: { title: "มูลค่าดีลทั้งหมด", blurb: "ทุกดีลที่ยังไม่ถูกลบ แยกตามขั้น" },
        won_this_month: { title: "ยอดปิดสำเร็จเดือนนี้", blurb: "เทียบเดือนที่แล้ว ตามวันที่ปิดจริง" },
        open_jobs_by_tech: { title: "งานซ่อมค้างแยกตามช่าง", blurb: "เปิดอยู่ · มอบหมายแล้ว · กำลังทำ" },
        outstanding_invoices: { title: "ยอดค้างชำระ", blurb: "ออกแล้ว/ชำระบางส่วน แยกเลยกำหนด" },
        satisfaction_avg: { title: "คะแนนความพึงพอใจเฉลี่ย", blurb: "เฉพาะใบที่ลูกค้าตอบแล้ว" },
      },
```

and the English twin in `en.ts` (same keys, or `check-i18n-usage.py` fails).

- [ ] **Step 4: The cards**

In `AiReports.tsx`, add the type and the loader beside the existing `Options` effect:

```tsx
type BasicRow = { key: string; label_th: string; label_en: string; value: number; count?: number };
type BasicReport = {
  key: string;
  title_th: string;
  title_en: string;
  unit: "money" | "count" | "score";
  headline: { label_th: string; label_en: string; value: number };
  rows: BasicRow[];
  notes_th: string[];
  notes_en: string[];
};

const BASIC_KEYS = [
  "pipeline_value", "won_this_month", "open_jobs_by_tech",
  "outstanding_invoices", "satisfaction_avg",
] as const;

  const [basic, setBasic] = useState<Record<string, BasicReport | null>>({});

  useEffect(() => {
    if (!token || !licenseId || allowed === false) return;
    let live = true;
    void (async () => {
      for (const key of BASIC_KEYS) {
        try {
          const response = await fetch(
            `/api/phase2/licenses/${licenseId}/reports/basic/${key}`,
            { headers: proxyHeaders(token, licenseId) },
          );
          if (!response.ok) continue;
          const data = (await response.json()) as BasicReport;
          if (live) setBasic((current) => ({ ...current, [key]: data }));
        } catch {
          // One card that cannot load leaves the other four alone.
        }
      }
    })();
    return () => { live = false; };
  }, [token, licenseId, allowed]);
```

and render, above the question box:

```tsx
        <section className="basic-reports">
          <h2>{copy.basic.heading}</h2>
          <p className="hint">{copy.basic.intro}</p>
          <div className="basic-grid">
            {BASIC_KEYS.map((key) => {
              const report = basic[key];
              const words = copy.basic[key];
              return (
                <article key={key} className="basic-card">
                  <h3>{words.title}</h3>
                  <p className="basic-blurb">{words.blurb}</p>
                  {report ? (
                    <>
                      <p className="basic-value">
                        {formatBasic(report.headline.value, report.unit, locale)}
                      </p>
                      <ul className="basic-rows">
                        {report.rows.slice(0, 3).map((row) => (
                          <li key={row.key}>
                            <span>{locale === "en" ? row.label_en : row.label_th}</span>
                            <b>{formatBasic(row.value, report.unit, locale)}</b>
                          </li>
                        ))}
                      </ul>
                      {(locale === "en" ? report.notes_en : report.notes_th)[0] ? (
                        <p className="basic-note">
                          {(locale === "en" ? report.notes_en : report.notes_th)[0]}
                        </p>
                      ) : null}
                    </>
                  ) : (
                    <p className="basic-value skeleton" aria-hidden="true">&nbsp;</p>
                  )}
                </article>
              );
            })}
          </div>
        </section>
```

with the one formatter, which only formats — it never computes:

```tsx
/** Formatting only. Every number here was computed by the server; adding
 *  two of them up in the browser is how the VAT bug of round 20K
 *  happened. */
function formatBasic(value: number, unit: BasicReport["unit"], locale: string): string {
  if (unit === "count") return new Intl.NumberFormat(locale).format(Math.round(value));
  return new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .format(value);
}
```

CSS in `presentation/app/globals.css` (reuse existing tokens; do not introduce new colours):

```css
.basic-grid { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 480px) { .basic-grid { grid-template-columns: repeat(2, 1fr); } }
@media (min-width: 768px) { .basic-grid { grid-template-columns: repeat(3, 1fr); } }
.basic-card { border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: var(--paper); }
.basic-card h3 { font-size: 14px; margin: 0; }
.basic-blurb { font-size: 12px; color: var(--soft); margin: 2px 0 10px; }
.basic-value { font-size: 26px; font-weight: 600; margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.basic-rows { list-style: none; padding: 0; margin: 8px 0 0; font-size: 13px; }
.basic-rows li { display: flex; justify-content: space-between; gap: 8px; padding: 2px 0; }
.basic-rows b { font-variant-numeric: tabular-nums; }
.basic-note { font-size: 11px; color: var(--soft); margin: 8px 0 0; }
```

(Confirm the variable names actually in the file: `grep -n "\-\-line\|\-\-soft\|\-\-paper" presentation/app/globals.css | head`. Use the ones that exist.)

- [ ] **Step 5: Run the test, the checkers and the build**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_reports_ui.py -q
/tmp/dv/bin/python scripts/dev/check-i18n-usage.py | tail -2
/tmp/dv/bin/python scripts/dev/check-routes.py | tail -2
cd presentation && npm run typecheck && NEXT_TELEMETRY_DISABLED=1 npm run build > /tmp/21c-build.log 2>&1 || tail -20 /tmp/21c-build.log
```

- [ ] **Step 6: Look at it**

Open `/liff/sales/reports/ai` at phone width and at desktop width. Five cards, no jumping
while they load, no number clipped, the question box still obviously the main thing on the
page. **Look at it** — no test in this task can see any of that.

- [ ] **Step 7: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the five reports are cards on the reports page, and they cost nothing" && git log --oneline -1
```

---

### Task 15: Prove what SmartBrowz's screenshot actually waits for — and fix the empty `pdf/__init__.py`

**Files:**
- Create: `scripts/dev/probe-smartbrowz-screenshot.py`
- Modify: `application/chann_app/services/pdf/__init__.py` (it is 0 bytes)
- Test: `tests/unit/test_round21c_chart_plan.py` (the import half only; the rest arrives in Task 16)

**Interfaces:**
- Produces: a written answer — **"canvas paints" or "canvas is blank"** — that decides Task 16's renderer, plus two PNGs on disk and a line in the handoff. And `from .pdf import PdfOptions, get_renderer` working.

**Pass/fail criterion (state it before running):** the probe passes when `preview_image`
returns more than 1 KB of PNG for **both** pages. The *decision* is separate and is made by
**looking at the two images**: if `canvas.png` shows bars, JavaScript is awaited and either
renderer is possible; if it is blank or shows only the heading, the renderer in Task 16 is
**inline SVG with no JavaScript at all**. There is no third outcome and no guessing —
`smart_browz.take_screenshot(html)` takes no options (`smartbrowz.py:243`), so there is no
viewport, no delay and no wait-for-selector to reason about.

- [ ] **Step 1: Fix the import that has been silently failing**

`application/chann_app/services/pdf/__init__.py` is empty, so `reports_ai.py:586`'s
`from .pdf import PdfOptions, get_renderer` raises `ImportError`, which the `except
Exception` at :592 swallows — **the AI report's PDF has never been produced.** Write:

```python
"""The PDF/screenshot renderer seam.

Re-exported here because `from .pdf import PdfOptions, get_renderer` read
naturally and was written that way in `reports_ai.publish_files` — against
an empty `__init__`, so it raised ImportError into an `except Exception`
and every AI report quietly came back without its PDF (found 23 ก.ย. 2569,
round 21C).
"""
from __future__ import annotations

from .base import (
    NullPdfRenderer, PdfOptions, PdfRenderer, PdfResult, RendererUnavailable, get_renderer,
)

__all__ = [
    "NullPdfRenderer", "PdfOptions", "PdfRenderer", "PdfResult",
    "RendererUnavailable", "get_renderer",
]
```

and pin it, in a new `tests/unit/test_round21c_chart_plan.py`:

```python
"""Round 21C — the picture road."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))


class TestTheRendererSeamImports:
    def test_the_package_re_exports_what_reports_ai_asks_for(self):
        from chann_app.services.pdf import PdfOptions, get_renderer

        assert PdfOptions().page_format == "A4"
        assert get_renderer("null").name == "null"

    def test_the_report_pdf_path_no_longer_swallows_an_import_error(self):
        source = (ROOT / "application/chann_app/services/reports_ai.py").read_text(encoding="utf-8")
        assert "from .pdf import PdfOptions, get_renderer" in source
```

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_chart_plan.py -q
```
Expected: FAIL first (`ImportError: cannot import name 'PdfOptions'`), then PASS.

- [ ] **Step 2: Write the probe**

`scripts/dev/probe-smartbrowz-screenshot.py`:

```python
#!/usr/bin/env python3
"""Does SmartBrowz's screenshot wait for JavaScript to paint? (round 21C)

`SmartBrowzPdfRenderer.preview_image` calls `smart_browz.take_screenshot(html)`
with NO options — no viewport, no delay, no wait-for-selector
(services/pdf/smartbrowz.py:243). Whether a canvas drawn by a script is in
the picture is therefore not knowable from the signature, and the whole
chart renderer depends on the answer.

So: two pages, one that needs JavaScript and one that does not, both
screenshotted for real, both written to disk. Then a HUMAN LOOKS AT THEM.
A test cannot see a blank canvas (round 20L).

    OR_KEY= irrelevant; this needs the SmartBrowz credentials:
      SMARTBROWZ_CLIENT_ID= SMARTBROWZ_CLIENT_SECRET= SMARTBROWZ_REFRESH_TOKEN= \\
      CATALYST_PROJECT_ID= CATALYST_ZAID= /tmp/dv/bin/python scripts/dev/probe-smartbrowz-screenshot.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

OUT = Path("/tmp/21c-probe")

CANVAS_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;margin:0;padding:24px;background:#fff}
h1{font-size:18px;margin:0 0 12px}</style></head><body>
<h1>CANVAS — drawn by JavaScript</h1>
<canvas id="c" width="600" height="220"></canvas>
<script>
  const ctx = document.getElementById('c').getContext('2d');
  ctx.fillStyle = '#178a50';
  [140, 90, 200, 60].forEach((h, i) => ctx.fillRect(20 + i * 150, 220 - h, 90, h));
</script></body></html>"""

SVG_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;margin:0;padding:24px;background:#fff}
h1{font-size:18px;margin:0 0 12px}</style></head><body>
<h1>INLINE SVG — no JavaScript</h1>
<svg width="600" height="220" viewBox="0 0 600 220" xmlns="http://www.w3.org/2000/svg">
  <rect x="20"  y="80"  width="90" height="140" fill="#178a50"/>
  <rect x="170" y="130" width="90" height="90"  fill="#178a50"/>
  <rect x="320" y="20"  width="90" height="200" fill="#178a50"/>
  <rect x="470" y="160" width="90" height="60"  fill="#178a50"/>
</svg></body></html>"""


async def main() -> int:
    from chann_app.services.pdf import PdfOptions, get_renderer

    OUT.mkdir(parents=True, exist_ok=True)
    renderer = get_renderer("smartbrowz")
    verdicts = []
    for name, html in (("canvas", CANVAS_PAGE), ("svg", SVG_PAGE)):
        try:
            result = await renderer.preview_image(html, PdfOptions())
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: FAILED — {type(exc).__name__}: {exc}")
            verdicts.append(False)
            continue
        content = result.content or b""
        path = OUT / f"{name}.png"
        path.write_bytes(content)
        ok = len(content) > 1024
        print(f"{name}: {len(content):,} bytes -> {path}  ({'ok' if ok else 'TOO SMALL'})")
        verdicts.append(ok)
    print("\nNow LOOK at both files. If canvas.png shows four green bars, JavaScript is")
    print("awaited. If it shows only the heading, the chart renderer must be inline SVG")
    print("with no JavaScript — which is what the design assumes. Write the answer, and")
    print("the two byte counts, into docs/SESSION_HANDOFF.md.")
    return 0 if all(verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 3: Get the credentials from the deployed service and run it**

```bash
cd ~/stage-fix/r21c
eval "$(gcloud run services describe chann-crm-ai-dev-application \
  --project=chann1-1 --region=asia-southeast1 \
  --format='value[delimiter="\n"](spec.template.spec.containers[0].env)' \
  | /tmp/dv/bin/python -c '
import re, sys
want = {"SMARTBROWZ_CLIENT_ID","SMARTBROWZ_CLIENT_SECRET","SMARTBROWZ_REFRESH_TOKEN","CATALYST_PROJECT_ID","CATALYST_ZAID","CATALYST_API_DOMAIN","CATALYST_ENVIRONMENT"}
for name, value in re.findall(r"name=.([A-Z_]+).; value=.([^}\n]*).", sys.stdin.read()):
    if name in want:
        print(f"export {name}={value!r}")
')"
/tmp/dv/bin/python scripts/dev/probe-smartbrowz-screenshot.py
```
`describe` is read-only (CLAUDE.md allows reading; it forbids *changing* GCP state).
**Never echo the values** — the `eval` above prints nothing itself, and the env vars stay in
this shell. If the parse comes back empty, print the field names only
(`gcloud run services describe … --format='value(spec.template.spec.containers[0].env[].name)'`)
and ask the owner to export the five by hand.

- [ ] **Step 4: LOOK at the two PNGs**

```bash
ls -l /tmp/21c-probe/ && echo "open both in the Cloud Shell editor preview"
```
Open `/tmp/21c-probe/canvas.png` and `/tmp/21c-probe/svg.png`. Write the verdict — the two
byte counts and what each image shows — into the handoff (Task 17) and into a comment at the
top of `chart_plan.py` (Task 16). **The renderer choice is made here and recorded, not
argued about later.**

- [ ] **Step 5: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "fix(round21c): the pdf package re-exports its own seam, and a probe answers what a screenshot waits for" && git log --oneline -1
```

---

### Task 16: The chart plan — the model designs the picture, the server computes it

**Files:**
- Create: `application/chann_app/services/chart_plan.py`
- Modify: `application/chann_app/services/reports_ai.py:550-562` (`publish_chart_for` tries the plan first)
- Modify: `application/chann_app/routers_phase2.py:5118` (add `_charge_for_the_question` beside `_charge_for_the_picture`)
- Test: `tests/unit/test_round21c_chart_plan.py` (extends Task 15's file)

**Interfaces:**
- Consumes: `preview_image` (Task 15's verdict), `charts.Chart`/`charts.render_or_none`, `reports_ai.publish_chart`, `basic_reports`' envelope.
- Produces:
  - `chart_plan.ChartPlan` (frozen dataclass: `kind, title, subtitle, unit, series_label, highlight, labels, note`)
  - `chart_plan.validate_chart_plan(plan: dict, *, labels: list[str], unit: str, rows: int) -> ChartPlan`
  - `chart_plan.design(question: str, *, labels, values, unit, language, client=None) -> ChartPlan | None`
  - `chart_plan.render_chart_html(plan: ChartPlan, *, values: list[float], company_name: str = "", oa: str = "sales") -> str`
  - `chart_plan.publish_for_basic_report(client, *, report, license_id, language) -> tuple[str | None, bool]`
  - `chart_plan.publish_for_spec(spec, result, language, *, license_id) -> tuple[str | None, bool]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_round21c_chart_plan.py`:

```python
import pytest  # noqa: E402

from chann_app.services import chart_plan  # noqa: E402

LABELS = ["เลยกำหนด", "ยังไม่ถึงกำหนด"]
GOOD = {"kind": "bar", "title": "ยอดค้างชำระ", "subtitle": "23 ก.ย. 2569",
        "unit": "money", "series_label": "บาท", "highlight": "เลยกำหนด",
        "labels": ["เลยกำหนด", "ยังไม่ถึงกำหนด"], "note": "เลยกำหนดคิดเป็นสองในสาม"}


class TestTheValidator:
    def test_a_good_plan_becomes_a_frozen_plan(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        assert plan.kind == "bar" and plan.highlight == "เลยกำหนด"
        with pytest.raises(Exception):
            plan.kind = "line"          # frozen

    @pytest.mark.parametrize("bad,why", [
        ({**GOOD, "kind": "pie"}, "is not allowed"),
        ({**GOOD, "labels": ["เลยกำหนด", "อย่างอื่น"]}, "do not match"),
        ({**GOOD, "labels": ["เลยกำหนด"]}, "do not match"),
        ({**GOOD, "unit": "count"}, "does not match"),
        ({**GOOD, "highlight": "ไม่มีอันนี้"}, "not one of the labels"),
        ({**GOOD, "title": "x" * 200}, "too long"),
        ({**GOOD, "note": "<script>alert(1)</script>"}, "markup"),
        ({**GOOD, "values": [1, 2]}, "unknown field"),
    ])
    def test_every_refusal_is_named(self, bad, why):
        with pytest.raises(chart_plan.ChartPlanInvalid, match=why):
            chart_plan.validate_chart_plan(bad, labels=LABELS, unit="money", rows=2)

    def test_the_model_can_never_send_a_number(self):
        assert "values" not in chart_plan.ChartPlan.__dataclass_fields__
        assert "values" not in chart_plan.ALLOWED_FIELDS

    def test_a_value_card_needs_exactly_one_number(self):
        with pytest.raises(chart_plan.ChartPlanInvalid, match="nothing to plot"):
            chart_plan.validate_chart_plan(
                {**GOOD, "kind": "bar", "labels": ["เดียว"]}, labels=["เดียว"], unit="money", rows=1)

    def test_too_many_labels_for_the_kind_is_refused(self):
        many = [f"ช่าง {i}" for i in range(12)]
        with pytest.raises(chart_plan.ChartPlanInvalid, match="too many labels"):
            chart_plan.validate_chart_plan(
                {**GOOD, "kind": "bar", "labels": many, "highlight": None, "unit": "count"},
                labels=many, unit="count", rows=12)


class TestTheRenderer:
    def test_the_page_is_self_contained_and_has_no_javascript(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[10000.0, 5000.0], company_name="ร้านทดสอบ")
        assert "<script" not in html.lower()
        assert "http://" not in html and "https://" not in html   # nothing fetched at render time
        assert "<svg" in html
        assert "10,000.00" in html and "ร้านทดสอบ" in html

    def test_the_highlighted_bar_is_the_one_the_plan_named(self):
        plan = chart_plan.validate_chart_plan(GOOD, labels=LABELS, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[10000.0, 5000.0])
        assert html.count('class="bar highlight"') == 1

    def test_a_long_label_does_not_leave_the_card(self):
        labels = ["ชื่อที่ยาวมากจริง ๆ นะครับยาวจนน่าจะล้นกรอบแน่นอน", "สั้น"]
        plan = chart_plan.validate_chart_plan(
            {**GOOD, "labels": labels, "highlight": None}, labels=labels, unit="money", rows=2)
        html = chart_plan.render_chart_html(plan, values=[1234567.0, 1.0])
        assert "…" in html           # truncated, not overflowing


class TestTheFallback:
    @pytest.mark.asyncio
    async def test_a_refused_plan_still_produces_a_picture(self, monkeypatch):
        """An invalid plan is not an error the person sees: the code builds
        the deterministic plan instead (spec §7.5)."""
        async def bad_design(*args, **kwargs):
            raise chart_plan.ChartPlanInvalid("kind 'pie' is not allowed")

        monkeypatch.setattr(chart_plan, "design", bad_design)
        monkeypatch.setattr(chart_plan, "_screenshot", _no_screenshot)
        monkeypatch.setattr(chart_plan, "publish_chart", _fake_publish)
        url, plottable = await chart_plan.publish_for_spec(
            {"entity": "invoices", "metric": "sum", "field": "outstanding", "group_by": "status",
             "date_range": None, "date_field": "issue_date", "filter": {}},
            {"rows": [{"key": "issued", "label": "ออกแล้ว", "value": 10000.0}], "total": 10000.0},
            "th", license_id="L1")
        assert plottable is True
        assert url == "https://example/chart.png"     # the Pillow fallback was published


async def _no_screenshot(html: str) -> bytes | None:
    return None


async def _fake_publish(png, *, license_id, store=None):
    return "https://example/chart.png"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_round21c_chart_plan.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'chann_app.services.chart_plan'`.

- [ ] **Step 3: Write the module**

`application/chann_app/services/chart_plan.py`. Two notes to carry into the file: the
verdict from Task 15's probe goes in the module docstring, and `_KINDS_FALLBACK` maps
anything Pillow cannot draw onto something it can.

```python
"""The model designs the picture; the server computes it (round 21C).

Owner, 23 ก.ย. 2569: the picture may be designed by AI, never calculated
by it. So the model is handed the numbers we already worked out and asked
for a *plan* — kind, labels, unit, which bar to highlight — and the plan
has NO field for a value. It cannot send a wrong number because it cannot
send a number.

The plan is rendered as one self-contained HTML page with inline SVG and
NO JavaScript, screenshotted through SmartBrowz `preview_image`
(services/pdf/smartbrowz.py:238 — built, paid for and until now unused),
and stored by the existing `reports_ai.publish_chart`. Nothing downstream
of that changes: same path, same signed link, same LINE image, same quota.

<PROBE VERDICT — paste Task 15's result here: the two byte counts and
whether canvas.png painted. If it did not, this docstring is also the
reason the renderer is SVG-only.>

When anything at all goes wrong — a refused plan, a model that is not
configured, a screenshot that times out — the picture falls back, in this
order: the deterministic plan the code builds, then `charts.py` (Pillow),
then words. A picture is never worth losing the answer over.
"""
from __future__ import annotations

import html as html_escape
import json
import logging
from dataclasses import dataclass

from . import charts
from .reports_ai import publish_chart

log = logging.getLogger(__name__)

ALLOWED_KINDS = ("bar", "hbar", "line", "value", "donut")
#: What Pillow can actually draw (`charts._KINDS`), for the fallback.
_KINDS_FALLBACK = {"bar": "bar", "hbar": "hbar", "line": "line", "value": "value", "donut": "bar"}
ALLOWED_UNITS = ("money", "count", "score", "percent")
ALLOWED_FIELDS = frozenset(
    {"kind", "title", "subtitle", "unit", "series_label", "highlight", "labels", "note"})
MAX_LABELS = {"bar": 8, "hbar": 10, "line": 14, "donut": 6, "value": 1}
MAX_TEXT = 80
THEME = {"sales": "#178a50", "technician": "#1f6fd6", "customer": "#e8731a"}


class ChartPlanInvalid(ValueError):
    """The model's design was not usable. Never shown to a person — the
    caller falls back to the plan the code would have made."""


@dataclass(frozen=True)
class ChartPlan:
    kind: str
    title: str
    subtitle: str
    unit: str
    series_label: str
    highlight: str | None
    labels: tuple[str, ...]
    note: str


def _text(value, field: str) -> str:
    said = str(value or "").strip()
    if len(said) > MAX_TEXT:
        raise ChartPlanInvalid(f"{field} is too long")
    if "<" in said or ">" in said:
        raise ChartPlanInvalid(f"{field} may not contain markup")
    return said


def validate_chart_plan(plan: dict, *, labels: list[str], unit: str, rows: int) -> ChartPlan:
    """Refuse anything that is not exactly a design of THIS result."""
    if not isinstance(plan, dict):
        raise ChartPlanInvalid("plan must be an object")
    unknown = set(plan) - ALLOWED_FIELDS
    if unknown:
        raise ChartPlanInvalid(f"unknown field '{sorted(unknown)[0]}'")
    kind = str(plan.get("kind") or "bar")
    if kind not in ALLOWED_KINDS:
        raise ChartPlanInvalid(f"kind '{kind}' is not allowed")
    said_unit = str(plan.get("unit") or unit)
    if said_unit not in ALLOWED_UNITS or said_unit != unit:
        raise ChartPlanInvalid("unit does not match the report")
    given = [str(one) for one in (plan.get("labels") or [])]
    if sorted(given) != sorted(labels):
        raise ChartPlanInvalid("labels do not match the result")
    if len(given) > MAX_LABELS[kind]:
        raise ChartPlanInvalid(f"too many labels for {kind}")
    if kind == "value" and rows != 1:
        raise ChartPlanInvalid("a value card needs exactly one number")
    if kind != "value" and rows < 2:
        raise ChartPlanInvalid("nothing to plot: one number is not a chart")
    highlight = plan.get("highlight")
    if highlight not in (None, "") and str(highlight) not in given:
        raise ChartPlanInvalid("highlight is not one of the labels")
    return ChartPlan(
        kind=kind,
        title=_text(plan.get("title"), "title"),
        subtitle=_text(plan.get("subtitle"), "subtitle"),
        unit=said_unit,
        series_label=_text(plan.get("series_label"), "series_label"),
        highlight=str(highlight) if highlight else None,
        labels=tuple(given),
        note=_text(plan.get("note"), "note"),
    )


DESIGN_PROMPT = (
    "You are designing ONE chart for numbers that are already final. "
    "You will be given the question, the labels and their values.\n"
    f"Reply with JSON only, using exactly these fields: {sorted(ALLOWED_FIELDS)}.\n"
    f"kind: one of {list(ALLOWED_KINDS)} — bar for a few categories, hbar for names, "
    "line for months in order, donut for parts of one whole, value for a single number.\n"
    "labels: the SAME labels you were given, in the order you want them drawn. "
    "Do not rename them, do not add one, do not drop one.\n"
    "highlight: one of those labels, or null.\n"
    "title/subtitle/series_label/note: short Thai, at most 80 characters each, no HTML.\n"
    "NEVER include a number, a total or a value: they are not yours to give. "
    "You are choosing how this is drawn, not what it says."
)


async def design(question: str, *, labels: list[str], values: list[float], unit: str,
                 language: str = "th", client=None) -> ChartPlan:
    """Ask the model for a design. Raises ChartPlanInvalid; never returns
    a plan that has not been through the validator."""
    from .ai.client import complete_json
    from .reports_ai import extract_json

    told = json.dumps(
        {"question": question, "unit": unit, "language": language,
         "data": [{"label": label, "value": value} for label, value in zip(labels, values)]},
        ensure_ascii=False)
    raw = await complete_json(DESIGN_PROMPT, told, client=client)
    data = raw if isinstance(raw, dict) else extract_json(str(raw))
    return validate_chart_plan(data, labels=labels, unit=unit, rows=len(values))


def code_plan(*, title: str, subtitle: str, labels: list[str], unit: str,
              group_by: str | None = None) -> ChartPlan:
    """The design the code would have chosen. Used when the model is not
    configured, is unavailable, or answers with something we refuse."""
    from .reports_ai import CHART_KIND

    kind = "value" if len(labels) < 2 else CHART_KIND.get(str(group_by or ""), "bar")
    if len(labels) > MAX_LABELS[kind]:
        kind = "hbar"
    return ChartPlan(kind=kind, title=title[:MAX_TEXT], subtitle=subtitle[:MAX_TEXT],
                     unit=unit, series_label="", highlight=None,
                     labels=tuple(labels[:MAX_LABELS[kind]]), note="")
```

then the renderer and the publishers in the same file:

```python
def _fmt(value: float, unit: str) -> str:
    if unit == "count":
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def _short(label: str, limit: int = 22) -> str:
    """A label that would leave the card is cut with an ellipsis HERE,
    where it can be tested — not by the SVG, which just overflows
    silently (round 20L: "เสนอราคาแล้ว" became "เสนอราคาแล้" + "ว")."""
    said = str(label or "")
    return said if len(said) <= limit else said[: limit - 1] + "…"


def render_chart_html(plan: ChartPlan, *, values: list[float], company_name: str = "",
                      oa: str = "sales") -> str:
    """One self-contained page: inline SVG, inline CSS, no script, no
    network. The OA's colour, the shop's name, and nothing else."""
    e = html_escape.escape
    colour = THEME.get(oa, THEME["sales"])
    peak = max([abs(v) for v in values] + [1.0])
    rows = list(zip(plan.labels, values))
    body: list[str] = []
    if plan.kind in ("bar", "donut", "value") and len(rows) == 1:
        body.append(f'<p class="big">{e(_fmt(rows[0][1], plan.unit))}</p>'
                    f'<p class="biglabel">{e(_short(rows[0][0], 40))}</p>')
    elif plan.kind == "hbar":
        body.append('<div class="hbars">')
        for label, value in rows:
            width = max(2.0, 100.0 * abs(value) / peak)
            klass = "bar highlight" if label == plan.highlight else "bar"
            body.append(
                f'<div class="hrow"><span class="hlabel">{e(_short(label))}</span>'
                f'<span class="htrack"><span class="{klass}" style="width:{width:.1f}%"></span></span>'
                f'<span class="hvalue">{e(_fmt(value, plan.unit))}</span></div>')
        body.append("</div>")
    else:
        width = 100.0 / max(len(rows), 1)
        body.append(f'<svg viewBox="0 0 {len(rows) * 120} 320" class="chart" role="img">')
        for index, (label, value) in enumerate(rows):
            height = max(4.0, 240.0 * abs(value) / peak)
            klass = "bar highlight" if label == plan.highlight else "bar"
            x = index * 120 + 20
            body.append(
                f'<rect class="{klass}" x="{x}" y="{280 - height:.1f}" width="80" '
                f'height="{height:.1f}" rx="6"/>'
                f'<text class="v" x="{x + 40}" y="{272 - height:.1f}" text-anchor="middle">'
                f'{e(_fmt(value, plan.unit))}</text>'
                f'<text class="l" x="{x + 40}" y="302" text-anchor="middle">'
                f'{e(_short(label, 12))}</text>')
        body.append("</svg>")
    return (
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=1040">'
        f"<title>{e(plan.title)}</title><style>"
        "*{box-sizing:border-box}"
        "body{font-family:'IBM Plex Sans Thai','Noto Sans Thai',sans-serif;margin:0;"
        "padding:32px;width:1040px;background:#faf7f2;color:#1a2030}"
        ".card{background:#fff;border:1px solid #e5e0d8;border-radius:16px;padding:28px}"
        "h1{font-size:26px;margin:0 0 2px}.sub{color:#5a6478;font-size:14px;margin:0 0 20px}"
        f".bar{{fill:{colour};background:{colour};opacity:.55}}"
        f".bar.highlight{{opacity:1}}"
        ".chart{width:100%;height:340px}"
        ".v{font-size:14px;fill:#1a2030}.l{font-size:13px;fill:#5a6478}"
        ".hrow{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:15px}"
        ".hlabel{width:220px;flex:none}.htrack{flex:1;background:#f0ece4;border-radius:6px;height:18px}"
        ".bar{display:block;height:18px;border-radius:6px}"
        ".hvalue{width:150px;text-align:right;font-variant-numeric:tabular-nums}"
        ".big{font-size:72px;font-weight:600;margin:20px 0 0;text-align:center}"
        ".biglabel{text-align:center;color:#5a6478;margin:0}"
        ".note{color:#5a6478;font-size:13px;margin:18px 0 0}"
        ".foot{color:#8b93a3;font-size:12px;margin:14px 0 0}"
        "</style></head><body><div class=\"card\">"
        f"<h1>{e(plan.title)}</h1><p class=\"sub\">{e(plan.subtitle)}</p>"
        + "".join(body)
        + (f'<p class="note">{e(plan.note)}</p>' if plan.note else "")
        + f'<p class="foot">{e(company_name)}{" · " if company_name else ""}'
          f'{e(plan.series_label)}</p>'
        "</div></body></html>"
    )


async def _screenshot(page: str) -> bytes | None:
    """HTML → PNG through SmartBrowz. None on any failure — the caller
    falls back to Pillow, and the answer is never lost."""
    from .pdf import PdfOptions, get_renderer

    try:
        result = await get_renderer("smartbrowz").preview_image(page, PdfOptions())
    except Exception:  # noqa: BLE001
        log.info("chart screenshot unavailable; falling back to the drawn chart")
        return None
    content = result.content or b""
    return content if len(content) > 512 else None


async def _publish(plan: ChartPlan, *, values: list[float], license_id: str,
                   company_name: str, oa: str, footer: str, language: str) -> str | None:
    png = await _screenshot(render_chart_html(
        plan, values=values, company_name=company_name, oa=oa))
    if png is None:
        png = charts.render_or_none(charts.Chart(
            title=plan.title, subtitle=plan.subtitle,
            points=[(label, float(value)) for label, value in zip(plan.labels, values)],
            kind=_KINDS_FALLBACK[plan.kind], oa=oa, language=language,
            money=plan.unit == "money", footer=footer or plan.note,
        ))
    if png is None:
        return None
    return await publish_chart(png, license_id=str(license_id))


async def publish_for_basic_report(client, *, report: dict, license_id: str,
                                   language: str = "th", ai_client=None,
                                   company_name: str = "") -> tuple[str | None, bool]:
    """A picture of one of the five. THIS is what costs a credit — the
    numbers were free (spec §5)."""
    rows = report.get("rows") or []
    labels = [str(row.get("label_th") if language != "en" else row.get("label_en")) for row in rows]
    values = [float(row.get("value") or 0) for row in rows]
    if not labels:
        return None, False
    title = str(report.get("title_th") if language != "en" else report.get("title_en"))
    subtitle = (report.get("notes_th" if language != "en" else "notes_en") or [""])[0]
    unit = str(report.get("unit") or "count")
    try:
        plan = await design(title, labels=labels, values=values, unit=unit,
                            language=language, client=ai_client)
    except Exception:  # noqa: BLE001
        log.info("chart plan refused or unavailable; drawing the code's own design")
        plan = code_plan(title=title, subtitle=subtitle, labels=labels, unit=unit)
    url = await _publish(plan, values=values, license_id=license_id,
                         company_name=company_name, oa="sales", footer=subtitle,
                         language=language)
    return url, True


async def publish_for_spec(spec: dict, result: dict, language: str, *, license_id: str,
                           ai_client=None, company_name: str = "") -> tuple[str | None, bool]:
    """The ad-hoc road's picture, designed rather than drawn."""
    from .reports_ai import describe

    rows = result.get("rows") or []
    if not rows:
        return None, False
    labels = [str(row.get("label") or "") for row in rows]
    values = [float(row.get("value") or 0) for row in rows]
    unit = "count" if spec.get("metric") == "count" else "money"
    title = describe(spec, language)
    try:
        plan = await design(title, labels=labels, values=values, unit=unit,
                            language=language, client=ai_client)
    except Exception:  # noqa: BLE001
        plan = code_plan(title=title, subtitle="", labels=labels, unit=unit,
                         group_by=spec.get("group_by"))
    url = await _publish(plan, values=values, license_id=license_id,
                         company_name=company_name, oa="sales", footer="", language=language)
    return url, True
```

- [ ] **Step 4: Let the ad-hoc road use it, keeping Pillow as the floor**

`reports_ai.publish_chart_for` (:550) becomes:

```python
async def publish_chart_for(
    spec: dict, result: dict, language: str, *, license_id: str, store=None,
) -> tuple[str | None, bool]:
    """(link, plottable). Round 21C: the model designs the picture and the
    server computes it (`services/chart_plan.py`). A single number still
    goes down the old road — a value card has nothing to design."""
    if result.get("rows"):
        from . import chart_plan

        return await chart_plan.publish_for_spec(
            spec, result, language, license_id=license_id)
    chart = chart_for(spec, result, language)
    if chart is None:
        return None, False
    png = charts.render_or_none(chart)
    if png is None:
        return None, True
    return await publish_chart(png, license_id=license_id, store=store), True
```

- [ ] **Step 5: Charge for an answered ad-hoc question**

In `routers_phase2.py`, beside `_charge_for_the_picture` (:5118) — **leave that function's
body exactly as it is; `tests/unit/test_round20l_a_picture_is_a_picture.py` greps its source
for the literal line `quota = await chart_quota.spend_one(`**:

```python
async def _charge_for_the_question(client, license_id: str, out: dict) -> dict:
    """Round 21C: an ad-hoc question costs a credit too, because it costs a
    model call (owner, 23 ก.ย. 2569). A clarifying question costs nothing —
    nothing was answered — and the five fixed reports never come here at
    all, which is what makes charging this road fair: a shop that has run
    out can still get its five numbers (spec §5).
    """
    from .services import chart_quota

    if out.get("clarify") or not out.get("result"):
        return out
    quota = await chart_quota.spend_one(client, license_id=license_id)
    out["quota"] = {
        "allowed": bool(quota.get("allowed")), "used": int(quota.get("used") or 0),
        "allowance": int(quota.get("allowance") or 0), "unknown": bool(quota.get("unknown")),
    }
    return out
```

and call it in `ai_report_ask` (:5115) and `ai_report_run` (:5187):
`return await _charge_for_the_question(client, license_id, await _charge_for_the_picture(client, license_id, out))`
— a request that draws a picture spends one, not two: `_charge_for_the_picture` already
returns early when there is no chart, so guard the second call with
`if not out.get("quota"):` inside `_charge_for_the_question` (add that line at the top of the
body, after the clarify check). The same pairing goes into `chat._handle_ai_report`, after
its existing picture charge.

- [ ] **Step 6: Run the tests**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest \
  tests/unit/test_round21c_chart_plan.py tests/unit/test_ai_reports.py \
  tests/unit/test_round20c_chart_quota.py tests/unit/test_round20l_a_picture_is_a_picture.py -q
```
Expected: all passed. `test_round20l_…` failing means the charge lines were edited — put them back.

- [ ] **Step 7: Render every kind and every edge case, and LOOK**

```bash
cd ~/stage-fix/r21c && JWT_SECRET=test-jwt-secret /tmp/dv/bin/python - <<'PY'
import asyncio, sys, pathlib
sys.path.insert(0, "application")
from chann_app.services import chart_plan, charts
OUT = pathlib.Path("/tmp/21c-charts"); OUT.mkdir(exist_ok=True)
cases = {
  "bar-normal":      ("bar",  ["ใหม่","เสนอราคาแล้ว","ปิดสำเร็จ","ไม่สำเร็จ"], [379000.0, 120000.0, 99000.0, 0.0], "money"),
  "bar-seven-digits":("bar",  ["ก","ข"], [1340000.0, 12.0], "money"),
  "bar-zero":        ("bar",  ["ก","ข"], [0.0, 0.0], "money"),
  "bar-negative":    ("bar",  ["ก","ข"], [-5000.0, 5000.0], "money"),
  "hbar-ten":        ("hbar", [f"ช่างคนที่ {i}" for i in range(10)], [float(10-i) for i in range(10)], "count"),
  "hbar-long-label": ("hbar", ["ชื่อที่ยาวมากจริง ๆ นะครับยาวจนน่าจะล้นกรอบ","สั้น"], [3.0, 1.0], "count"),
  "line-months":     ("line", [f"เดือน {i}" for i in range(1, 7)], [1.0,5.0,3.0,9.0,2.0,7.0], "money"),
  "value-one":       ("value",["คะแนนเฉลี่ย"], [2.67], "score"),
  "donut-three":     ("donut",["เลยกำหนด","ยังไม่ถึงกำหนด","ชำระแล้ว"], [10000.0,5000.0,7000.0], "money"),
}
for name, (kind, labels, values, unit) in cases.items():
    plan = chart_plan.ChartPlan(kind=kind, title=name, subtitle="23 ก.ย. 2569", unit=unit,
                                series_label="บาท" if unit=="money" else "", highlight=labels[0],
                                labels=tuple(labels), note="ตัวอย่างหมายเหตุที่ยาวพอสมควร")
    (OUT / f"{name}.html").write_text(
        chart_plan.render_chart_html(plan, values=values, company_name="ร้านทดสอบ"), encoding="utf-8")
    png = charts.render_or_none(charts.Chart(
        title=name, points=list(zip(labels, values)), kind=chart_plan._KINDS_FALLBACK[kind],
        money=unit == "money", language="th", footer="fallback"))
    if png: (OUT / f"{name}-pillow.png").write_bytes(png)
print("\n".join(sorted(p.name for p in OUT.iterdir())))
PY
```
Then **screenshot the nine HTML pages through the real renderer** (the same credentials as
Task 15) and **open every PNG**, both roads:

```bash
cd ~/stage-fix/r21c && /tmp/dv/bin/python - <<'PY'
import asyncio, sys, pathlib
sys.path.insert(0, "application")
from chann_app.services.chart_plan import _screenshot
OUT = pathlib.Path("/tmp/21c-charts")
async def main():
    for page in sorted(OUT.glob("*.html")):
        png = await _screenshot(page.read_text(encoding="utf-8"))
        print(page.stem, len(png or b""))
        if png: (OUT / f"{page.stem}-smartbrowz.png").write_bytes(png)
asyncio.run(main())
PY
```
**Look at all eighteen images.** The round-20L rule: a clipped label, a number outside its
card and a row overlapping the footer are invisible to every test in this plan. Fix what you
see, re-render, look again.

- [ ] **Step 8: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "feat(round21c): the model designs the picture and the server computes it, with Pillow as the floor" && git log --oneline -1
```

---

### Task 17: The guide, its picture, the handoff and the tester's checklist

**Files:**
- Modify: `application/chann_app/services/guides.py:436-461` (the `ai-reports` step)
- Modify: `scripts/dev/render-guide-images.py` (the `sales-ai-report` scene)
- Modify: `docs/SESSION_HANDOFF.md` (a "รอบ 21C" entry above "รอบ 21B")
- Modify: `~/CHECKLIST-หลัง-deploy.md` (section VI, outside the repo)

- [ ] **Step 1: Rewrite the guide step**

In `application/chann_app/services/guides.py`, in the `ai-reports` step, put the five first —
they are what a shop should reach for — and keep everything that is still true. Replace the
`body` and the first `how` group with:

```python
                "body": {
                    "th": "ห้ารายงานที่ใช้บ่อยกดได้เลย ระบบคำนวณเอง ไม่ใช้เครดิต · อยากรู้อย่างอื่นก็พิมพ์เป็นภาษาคน",
                    "en": "Five everyday reports are one tap away and cost nothing; type anything else in plain words.",
                },
                "how": [
                    {"group": {"th": "ห้ารายงานที่ถูกเสมอ", "en": "The five fixed reports"}},
                    {"th": "มูลค่าดีลทั้งหมด — ทุกดีลที่ยังไม่ถูกลบ แยกตามขั้น", "en": "Pipeline value — every live deal, by stage", "type": "ยอดมูลค่าดีลทั้งหมด"},
                    {"th": "ยอดปิดสำเร็จเดือนนี้ เทียบเดือนที่แล้ว (ตามวันที่ปิดจริง)", "en": "Won this month against last month, by real close date", "type": "เดือนนี้ปิดได้เท่าไหร่"},
                    {"th": "งานซ่อมค้างแยกตามช่าง — เปิดอยู่ มอบหมายแล้ว กำลังทำ", "en": "Open jobs by technician — open, assigned, in progress", "type": "งานซ่อมค้างแยกตามช่าง"},
                    {"th": "ยอดค้างชำระ แยกเลยกำหนดกับยังไม่ถึงกำหนด", "en": "Outstanding invoices, overdue split out", "type": "ยอดค้างชำระ"},
                    {"th": "คะแนนความพึงพอใจเฉลี่ย เฉพาะใบที่ลูกค้าตอบแล้ว", "en": "Average satisfaction, answered forms only", "type": "คะแนนความพึงพอใจเฉลี่ย"},
                    {"th": "ห้าอันนี้ไม่ใช้เครดิต AI และตอบเหมือนกันทุกครั้ง · กดปุ่มใต้คำตอบเพื่อดูอันอื่นหรือ \"ดูเป็นรูป\"", "en": "These five cost no AI credit and answer the same every time; the buttons under the answer reach the others, or a picture"},
                    {"group": {"th": "ถามอย่างอื่น", "en": "Anything else"}},
                    {"th": "ถามเป็นประโยค", "en": "Ask in a sentence", "type": "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / ลูกค้าใหม่เดือนนี้แยกตามผู้ดูแล"},
                    {"th": "ถามอย่างอื่นและรูปที่ AI ออกแบบใช้เครดิตของร้าน (ค่าเริ่มต้น 30 ครั้งต่อเดือน) · ใช้ครบแล้วห้ารายงานข้างบนยังใช้ได้ตามปกติ", "en": "Ad-hoc questions and AI-designed pictures use the shop's monthly credits (30 by default); when they run out, the five above still work"},
                ]
```

keeping the remaining `how` entries (the clarifying question, the fixed charts, the dashboard
rows, the permission line, the "AI never touches the database" line) exactly as they are, and
updating `commands` to lead with the five:

```python
                "commands": ["ยอดมูลค่าดีลทั้งหมด", "เดือนนี้ปิดได้เท่าไหร่", "งานซ่อมค้างแยกตามช่าง", "ยอดค้างชำระ", "คะแนนความพึงพอใจเฉลี่ย", "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน", "ขอกราฟยอดขาย"],
                "example": "ยอดมูลค่าดีลทั้งหมด",
```

Also update the invoice step (`grep -n '"key": "invoices"' application/chann_app/services/guides.py`
in the **customer** guide, and the sales guide's quote/invoice lines) with the two new
sentences: correcting a bill, and "ส่งใบแจ้งหนี้ให้ลูกค้า".

Then:

```bash
cd ~/stage-fix/r21c && /tmp/dv/bin/python scripts/dev/render-guides.py
JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/unit/test_guides.py -q
```

- [ ] **Step 2: Redraw the picture — in the script, never by hand**

In `scripts/dev/render-guide-images.py`, the `sales-ai-report` scene currently draws a LINE
thread. Redraw it as **the dashboard page it now is**: the five cards in a grid with their
titles, blurbs and numbers, and the question box beneath them. Keep the house palette and the
Sarabun fonts the script already loads. Then:

```bash
cd ~/stage-fix/r21c && /tmp/dv/bin/python scripts/dev/render-guide-images.py
ls -l application/chann_app/static/help/sales-ai-report.png
```

- [ ] **Step 3: LOOK at it**

Open `application/chann_app/static/help/sales-ai-report.png`. The rule from round 21A and
20L: **no test can see this image**, and the last three bugs in these pictures (a label
outside its card, rows over the footer, a clipped Thai word) were all found by opening the
file. Check every card's number fits, no Thai word is cut, and the page looks like the real
screen from Task 14. Re-run the script until it does. **Never edit the PNG.**

- [ ] **Step 4: The handoff entry**

Above `**รอบ 21B (23 ก.ย. — DEV, ต่อจาก \`c0a71a3\`)` in `docs/SESSION_HANDOFF.md`, insert:

```markdown
**รอบ 21C (23 ก.ย. — DEV, ต่อจาก `1c29bda`): รายงานที่ถูก — นิยามมูลค่าดีลเดียว, ห้ารายงานพื้นฐาน, ใบแจ้งหนี้ปิดดีล, และรูปที่ AI ออกแบบ**

- **เจ้าของ:** *"ยอดมูลค่าดีลทั้งหมดขึ้น 0 ในรูปแบบใหม่"* → วินิจฉัยที่ `docs/superpowers/specs/2026-09-23-ai-reports-diagnosis.md`
  แล้วสั่ง 8 ข้อ: มูลค่าดีล = `amount` ที่พิมพ์ชนะ ไม่งั้นรวมรายการ · *"ใบแจ้งหนี้จะอัพเดตไปที่ดีลตอนชำระเงินแล้ว"* ·
  *"invoice ต้องแก้ไขรายการสินค้าหรือข้อมูลต่างๆได้เหมือน quote ด้วย"* · เพิ่ม `closed_at` · รูป AI ออกแบบได้แต่ห้ามคำนวณ ·
  รายงานพื้นฐานห้ามกินเครดิต · ห้ารายงานตายตัว · *"ถ้าข้อมูลลูกค้ามีการผูก line ไว้อยู่แล้ว … ส่งไปให้ลูกค้าผ่านไลน์ได้เลย"*
  · spec: `docs/superpowers/specs/2026-09-23-ai-reports-design.md`
- **ของเดิมพังตรงไหน:** `phase17.py:104` รวม `deals.amount` อย่างเดียว ซึ่งเป็น NULL ทุกใบที่มูลค่าอยู่ในรายการสินค้า →
  ตอบ 0 · มีนิยาม "มูลค่าดีล" อยู่ 4 ที่ และ 2 ที่ไม่ตรงกัน · filter เป็น `==` ค่าเดียว งานค้าง/ยอดค้างชำระจึงนับขาดเงียบ ๆ ·
  ไม่มี `closed_at` เลย "ปิดเดือนนี้" จึงใช้วันคาดว่าจะปิด
- **Data:** `repositories/deal_value.py` นิพจน์เดียว (`coalesce(amount, sum(qty*price), 0)`) ใช้ร่วมกันทั้ง `pipeline_summary`
  และ phase17 · filter รับลิสต์ → `IN` (สูงสุด 10 ค่า) · migration **0038_deal_closed_at** (+index, backfill จาก `updated_at`) ·
  `basic_reports.py` ห้ารายงาน · `InvoiceRepository._editable/update_lines/update_details` · `add_payment` คืนดีลที่ปิด และ
  `DealRepository.close_won_from_invoice` เขียนรายการ/มูลค่า/สถานะ/`closed_at` ในทรานแซกชันเดียว
- **Application:** `services/deal_value.py` · `services/basic_reports.py` (โมเดลเลือกว่าอันไหน ไม่คิดเลข) ·
  `services/chart_plan.py` (แผนกราฟที่ validate แล้ว → HTML/SVG ธีม OA → SmartBrowz `preview_image` → PNG เดิม;
  Pillow เป็น fallback) · `services/document_send.py` ส่งเอกสารให้ลูกค้าทางไลน์ · `pdf/__init__.py` ที่ว่างเปล่ามาตลอด
  ทำให้ PDF ของรายงาน AI ไม่เคยออกเลย — แก้แล้ว
- **แชท (model-first — วัดด้วย ask-model.py ก่อน/หลัง ทีละประโยค):** <วางผลจริงของ 15 ประโยคที่นี่>
- **จอ (ui-ux-pro-max):** การ์ดห้าใบเหนือกล่องคำถามใน `/liff/sales/reports/ai` · แก้รายการในแผงใบแจ้งหนี้ (ปิดเมื่อมีการชำระ) ·
  ปุ่ม "ส่งให้ลูกค้าทางไลน์" ในแผงใบแจ้งหนี้และหน้าใบเสนอราคา — ปิดใช้งานพร้อมเหตุผลที่มองเห็นเมื่อลูกค้าไม่ได้ผูกไลน์
- **SmartBrowz probe (ทำก่อนเขียน renderer):** <canvas.png … ไบต์ / svg.png … ไบต์ — และเห็นอะไร> → เลือก <SVG ล้วน / ได้ทั้งสองแบบ>
- **เครดิต:** ห้ารายงานพื้นฐาน = ฟรีเสมอ · รูปที่ AI ออกแบบ = 1 · คำถาม ad-hoc ที่ตอบแล้ว = 1 (เปลี่ยนจากกฎ 17-18 ก.ย.
  ได้เพราะห้ารายงานฟรีรับหน้าที่ "ร้านต้องรู้ตัวเลขตัวเองเสมอ" แทน) · คอลัมน์ยังชื่อ `ai_chart_quota` (ไม่ย้ายชื่อเพื่อคำ)
- **ตัวเลขจะขยับ (ตั้งใจ):** ดีลที่มีทั้ง `amount` และรายการสินค้าจะใช้ `amount` แทนผลรวมรายการ · รายงาน AI ของดีลเลิกเป็น 0
- เทสต์: `tests/unit/test_round21c_{deal_value,reports,invoice_edit,invoice_chat,document_send,basic_reports,reports_chat,chart_plan,invoice_ui,reports_ui}.py`,
  `tests/integration/test_round21c_{deal_value,closed_at,invoice_edit,invoice_settle,basic_reports}.py`,
  scenario `round21c-reports.yaml` + `round21c-invoice-edit.yaml` · รูปคู่มือ `sales-ai-report` วาดใหม่และ**ดูด้วยตาแล้ว**
- **หลัง deploy ต้องพิสูจน์ของจริง:** ถาม "ยอดมูลค่าดีลทั้งหมด" ใน DEV ต้องได้เลขเท่าการ์ดไปป์ไลน์ · ชำระใบแจ้งหนี้ครบ
  แล้วดีลต้องปิดพร้อมรายการจากใบนั้น · กด "ดูเป็นรูป" แล้วรูปต้องมาจริงและ**เปิดดูแล้ว**
```

- [ ] **Step 5: Checklist section VI**

Append to `~/CHECKLIST-หลัง-deploy.md`:

```markdown
## VI. รอบ 21C (`__21C_SHA__`) — รายงานที่ถูก, ใบแจ้งหนี้ปิดดีล, ส่งเอกสารทางไลน์

- [ ] **21C-1** Sale OA: พิมพ์ "ยอดมูลค่าดีลทั้งหมด" → ได้ตัวเลขจริง (ไม่ใช่ 0) และ**ตรงกับการ์ด "มูลค่าดีล" บนหน้าไปป์ไลน์**
- [ ] **21C-2** แดชบอร์ด > รายงาน AI: การ์ดห้าใบขึ้นครบ มีตัวเลข ไม่มีเลขล้นกรอบ ทั้งบนมือถือและบนจอใหญ่ · กล่องถามคำถามยังอยู่ใต้การ์ด
- [ ] **21C-3** "งานซ่อมค้างแยกตามช่าง" → รวมงานสถานะ เปิดอยู่+มอบหมายแล้ว+กำลังทำ (เทียบกับหน้ารายการงาน) · มีแถว "ยังไม่มอบหมาย" ถ้ามีงานที่ยังไม่มีช่าง
- [ ] **21C-4** "ยอดค้างชำระ" → แยก "เลยกำหนด" กับ "ยังไม่ถึงกำหนด" และรวมได้เท่ากับหน้าใบแจ้งหนี้
- [ ] **21C-5** กดปุ่ม "ดูเป็นรูป" ใต้รายงาน → ได้รูปในแชทจริง (ไม่ใช่แค่ลิงก์) · **เปิดรูปดู**: ป้ายไม่ถูกตัด ตัวเลขอยู่ในกรอบ
- [ ] **21C-6** ห้ารายงานพื้นฐานกดกี่ครั้งก็ได้ **ตัวเลขเครดิตไม่ขยับ** · ถามคำถามอื่น 1 ครั้ง แล้วเครดิตขยับ 1
- [ ] **21C-7** ใบแจ้งหนี้ที่ยังไม่รับชำระ: แก้จำนวนสินค้าได้ทั้งในแชทและในแผงบนจอ · ยอดรวม/VAT เปลี่ยนตาม · ถ้าออกเอกสารไปแล้ว มีข้อความว่าต้องออกเอกสารใหม่
- [ ] **21C-8** รับชำระใบนั้นให้ครบ → ตอบว่า "ปิดดีล D-… เป็นปิดสำเร็จ" · เปิดดีลนั้น: สถานะ ปิดสำเร็จ · รายการสินค้าและมูลค่า = ตามใบแจ้งหนี้ · ลองแก้ใบแจ้งหนี้อีกครั้ง → ถูกปฏิเสธพร้อมเหตุผล
- [ ] **21C-9** ลูกค้าที่**ผูกไลน์แล้ว**: กด "ส่งให้ลูกค้าทางไลน์" ที่แผงใบแจ้งหนี้ → ลูกค้าได้ข้อความพร้อมลิงก์เปิดได้ · พิมพ์ "ส่งใบเสนอราคา Q-… ให้ลูกค้า" ในแชทก็ได้ผลเดียวกัน
- [ ] **21C-10** ลูกค้าที่**ยังไม่ผูกไลน์**: ปุ่มเป็นสีจาง กดไม่ได้ และมีข้อความบอกเหตุผลใต้ปุ่ม · ในแชทตอบว่าส่งไม่ได้พร้อมชื่อลูกค้าและวิธีผูก
- [ ] **21C-11** คู่มือ (พิมพ์ "วิธีใช้" หรือ Dashboard > วิธีใช้): ขั้น "ถามรายงานด้วย AI" ขึ้นห้ารายงานก่อน และรูปตรงกับหน้าจริง
```

- [ ] **Step 6: Commit**

```bash
cd ~/stage-fix/r21c && git add -A && git commit -q -m "docs(round21c): the guide leads with the five, the handoff records what moved, the tester has section VI" && git log --oneline -1
```

---

### Task 18: The full gate, the patch, the deploy script, and the runtime proof

**Files:**
- Create: `~/round21c-deploy.sh` (from `~/round20z-deploy.sh` — the last round **with** a migration)
- Verify: the whole gate; `tests/integration` on `pg20w`; a fresh-clone apply; DEV runtime.

- [ ] **Step 1: The full gate on THIS tree**

`~/stage-fix/tools/gate.sh` starts with `cd ~/stage-fix/registry`, so run a copy pointed here
or it tests the previous round:

```bash
sed 's#cd ~/stage-fix/registry#cd ~/stage-fix/r21c#' ~/stage-fix/tools/gate.sh > ~/stage-fix/out/gate-21c.sh
cd ~/stage-fix/r21c && setsid nohup bash ~/stage-fix/out/gate-21c.sh > ~/stage-fix/out/gate-21c.log 2>&1 < /dev/null &
```
Wait for `=== gate done (clean)` (≈10 min; never run two gates at once). Then, separately:

```bash
cd ~/stage-fix/r21c && TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5435/chann_test \
  JWT_SECRET=test-jwt-secret /tmp/dv/bin/python -m pytest tests/integration -q -p no:cacheprovider | tail -1
```

- [ ] **Step 2: The real model, because chat changed**

```bash
cd ~/stage-fix/r21c
OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/dev/simulate-phrasings.py --real | tail -5
OR_KEY="$(cat ~/.or_key)" /tmp/dv/bin/python scripts/agent-test/run.py --real | tail -3
```
**Read the output, do not just check the exit code** (CLAUDE.md, 18 ก.ย.: the offline run
reports `0 long replies` because it never asks the model at all). Record both results in the
handoff. A reply over 15 lines halts the deploy later — fix it here.

- [ ] **Step 3: Squash to one patch and validate it on a fresh clone**

```bash
cd ~/stage-fix/r21c && git add -A && git diff --binary origin/main > /tmp/r21c.patch \
  && N=$(wc -l < /tmp/r21c.patch) && cp /tmp/r21c.patch ~/round21c-v1-$N.patch && echo ~/round21c-v1-$N.patch
rm -rf /tmp/fc-21c && git clone -q ~/chann-crm-ai/.git /tmp/fc-21c && cd /tmp/fc-21c \
  && git checkout -q 1c29bda && git apply --3way --check ~/round21c-v1-$N.patch && echo CLEAN
```
(`origin/main` must already be round 21B's `1c29bda` — if 21B is not deployed yet, **stop**:
21C ships after it.)

- [ ] **Step 4: The deploy script**

Copy `~/round20z-deploy.sh` to `~/round21c-deploy.sh` and change:

- the header comment → round 21C, migration `0038_deal_closed_at`, and the three owner
  sentences from the handoff entry;
- `PATCH_NAME="round21c-v1-$N.patch"` and `PATCH_LINES=$N`;
- `COMMIT_SUBJECT='feat(round21c): one definition of what a deal is worth, five reports that are always right, a paid bill that closes its deal, and pictures the model designs but never computes'`;
- `BASE_SUBJECT` = round 21B's subject — take it from `git log -1 --pretty=%s origin/main`;
- the STAGE 2 verify block, to these greps (each one a symbol only this round introduces):
  - `EXPECTED_MIGRATION_HEAD = "0038_deal_closed_at"` in `data/chann_data/main.py`
  - `database/alembic/versions/0038_deal_closed_at.py` exists and contains `down_revision = "0037_api_keys"`
  - `DEAL_VALUE = func.coalesce(` in `data/chann_data/repositories/deal_value.py`
  - `deal_value_subquery(` in `data/chann_data/repositories/phase9.py` **and** in `phase17.py`
  - `class BasicReportRepository` in `data/chann_data/repositories/basic_reports.py`
  - `def close_won_from_invoice` in `data/chann_data/repositories/phase9.py`
  - `def _editable` in `data/chann_data/repositories/invoices.py`
  - `def validate_chart_plan` in `application/chann_app/services/chart_plan.py`
  - `async def send_document_to_customer` in `application/chann_app/services/document_send.py`
  - `("send", "invoice")` in `application/chann_app/services/chat.py`
  - `"/reports/basic/"` in `presentation/app/liff/sales/reports/ai/AiReports.tsx`
  - `closed_at` in `data/chann_data/schemas.py`
  - the twelve test files exist
  → then `info "symbol ครบ 13 จุด (21C)"`;
- STAGE 3: keep the two SmartBrowz `--deselect` lines **exactly as they are**;
- STAGE 5's migration comment: `0038 adds a nullable column and backfills it; the running data tier does not read it, so database → migrate → data is safe` and `info "migration ผ่าน (head ควรเป็น 0038_deal_closed_at)"`;
- the commit body: the owner's sentences and what was built, in the round-20z style;
- the closing `TXT` block → checklist section **VI**, and the
  `sed -i "s/__21C_SHA__/${SHORT}/" "$HOME/CHECKLIST-หลัง-deploy.md"` line before it;
- every `round20z-deploy.sh` mention → `round21c-deploy.sh`.

```bash
bash -n ~/round21c-deploy.sh && echo SYNTAX OK
grep -c "round20z" ~/round21c-deploy.sh   # must be 0
```

- [ ] **Step 5: Deploy (the owner's standing authorisation: a green clone deploys)**

```bash
cd ~/chann-crm-ai && git status --porcelain | head -3 && git log --oneline -1   # clean, and on 1c29bda
cd ~/chann-crm-ai && setsid nohup env ALLOW_APPLY=YES bash /home/thanawinmax2/round21c-deploy.sh \
  > ~/deploy-21c.log 2>&1 < /dev/null &
```
Detached, because a session interrupt kills a foreground deploy mid-flight. Watch for
`DEPLOY OK — <sha>` (≈35 min). On `HALT`, read the log, fix in `~/stage-fix/r21c`, regenerate
the patch and rerun — the script resumes at build once the commit is on origin.

- [ ] **Step 6: Runtime proof**

```bash
AU=$(gcloud run services describe chann-crm-ai-dev-application --project=chann1-1 \
     --region=asia-southeast1 --format='value(status.url)')
curl -fsS "$AU/health" | grep -o '"git_commit":"[0-9a-f]*"'
curl -fsS "$AU/health" | grep -o '"expected_migration_head":"[^"]*"'
```
`git_commit` must be the SHA that was pushed, and the head must read `0038_deal_closed_at`.
**Nothing is "deployed" until `/health` says so** (CLAUDE.md).

Then, on DEV as the owner: ask "ยอดมูลค่าดีลทั้งหมด" in the Sale OA and compare it with the
pipeline card; pay an invoice in full and open its deal; press "ดูเป็นรูป" and **open the
picture**. Those three are 21C-1, 21C-8 and 21C-5 on the checklist, and they are the
acceptance evidence — not the test run.

- [ ] **Step 7: Tester guide artifact**

Read `https://claude.ai/artifact/V16FfyaeNmXGdfBq2vcU6d` with the Artifact tool, update the
DEV SHA and the round note, add rows `21C-1…11` to the checklist section in the same shape as
the `21B-…` rows, and republish to the same URL.

- [ ] **Step 8: Reset the worktree**

```bash
cd ~/stage-fix/r21c && git fetch -q origin && git reset -q --hard origin/main && git log --oneline -1
```
