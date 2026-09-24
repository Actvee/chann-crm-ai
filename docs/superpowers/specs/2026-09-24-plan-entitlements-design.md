# Feature control by sales plan (round 21D) — design

**Owner's ask (24 ก.ย. 2569):** *"ตัวควบคุมฟีเจอร์ตาม plan"* — the owner uploaded the sales-plan
comparison table (`docs/superpowers/specs/2026-09-24-sales-plans-source.html`) and asked that
the system enforce it. Four plans: **Starter · Pro · Enterprise · Enterprise Plus**.

Spec of the round before this one: `docs/superpowers/specs/2026-09-23-ai-reports-design.md`
(21C, migration `0038_deal_closed_at`). 21D sits on top of it.

**Status: APPROVED by the owner (24 ก.ย. 2569) with one change — a downgrade is refused while
active members exceed the target plan's limit (§13 Q2).** §13 records the five decisions.
Implementation plan: `docs/superpowers/plans/2026-09-24-plan-entitlements.md`.

---

## 1. What this is

One new idea and one rule:

- **A licence has a plan** (`licenses.plan_code`). The plan decides which **features** the
  shop has, how many **AI report credits** a month it gets, and how many **users** it may
  have. The platform admin sets it in the `/admin` console. There is no billing.
- **Entitlement sits beside permission, never instead of it.** Today a request passes if the
  person's role holds the permission key (and, in chat, if the OA offers it). After 21D it
  must also pass `entitled(plan, key)`. A role can grant only what the plan includes; a plan
  never grants anything a role does not.

What the person sees when the plan is the reason: **a refusal that names the plan that has the
feature**, in the same shape as today's permission refusal, on every surface (chat, dashboard,
external API, LINE OAs). Nothing is ever deleted when a plan goes down; locked data comes back
when the plan goes up.

**Model-first is unchanged** (`docs/MODEL_FIRST.md`). The plan check runs where the permission
check runs: after the model has read the sentence and proposed `(action, entity)`. A keyword
may *decline* on plan grounds (e.g. the typed `สร้างรายงานด้วย AI:` prefix on Starter), never
*act*. No new trigger words, no prompt changes except one read verb (§6.1, measured first).

---

## 2. The plan matrix — extracted from the table, cell by cell

Source: `2026-09-24-sales-plans-source.html`. 41 rows. **24 rows are "always on"** (✓ in all
four plans); **17 rows map to 10 entitlement keys**. Every row is listed; nothing is guessed.

| # | Group | Row (verbatim) | Starter | Pro | Enterprise | Ent. Plus | Key |
|---|---|---|---|---|---|---|---|
| 1 | การจัดการลูกค้า | เพิ่มลูกค้า (ทีละคน / หลายคน / Import CSV) | ✓ | ✓ | ✓ | ✓ | always on |
| 2 | | ตรวจข้อมูลซ้ำจากเบอร์/อีเมล | ✓ | ✓ | ✓ | ✓ | always on |
| 3 | | เปลี่ยน Lead เป็นลูกค้ายืนยัน | ✓ | ✓ | ✓ | ✓ | always on |
| 4 | | จัดเก็บข้อมูลลูกค้า | ✓ | ✓ | ✓ | ✓ | always on |
| 5 | | ประวัติการทำรายการของลูกค้า | ✓ | ✓ | ✓ | ✓ | always on |
| 6 | | ผูก LINE ลูกค้ากับร้าน | — | ✓ | ✓ | ✓ | `feature.customer_line_link` |
| 7 | การจัดการดีล | สร้างดีล กำหนดมูลค่า วันปิดดีล | ✓ | ✓ | ✓ | ✓ | always on |
| 8 | | อัปเดตและติดตามสถานะของดีล | ✓ | ✓ | ✓ | ✓ | always on |
| 9 | | จัดการหลายดีลพร้อมกัน | ✓ | ✓ | ✓ | ✓ | always on |
| 10 | สินค้า | สร้างและจัดการรายการสินค้า | ✓ | ✓ | ✓ | ✓ | always on |
| 11 | | รหัสสินค้า ราคา และรายละเอียด | ✓ | ✓ | ✓ | ✓ | always on |
| 12 | | Import CSV | ✓ | ✓ | ✓ | ✓ | always on |
| 13 | | ค้นหาสินค้าผ่าน LINE / Dashboard | ✓ | ✓ | ✓ | ✓ | always on |
| 14 | ใบเสนอราคา | สร้างใบเสนอราคา | ✓ | ✓ | ✓ | ✓ | always on |
| 15 | | อัปเดตและติดตามสถานะใบเสนอราคา | ✓ | ✓ | ✓ | ✓ | always on |
| 16 | | บันทึกเอกสารทุกครั้งที่ออก | ✓ | ✓ | ✓ | ✓ | always on |
| 17 | ใบแจ้งหนี้ | สร้างใบแจ้งหนี้ | ✓ | ✓ | ✓ | ✓ | always on |
| 18 | | ติดตามสถานะใบแจ้งหนี้และการชำระเงิน | ✓ | ✓ | ✓ | ✓ | always on |
| 19 | | บันทึกเอกสารทุกครั้งที่ออก | ✓ | ✓ | ✓ | ✓ | always on |
| 20 | นัดหมาย / งานติดตาม | นัดหมายลูกค้า / ติดตามงาน | ✓ | ✓ | ✓ | ✓ | always on |
| 21 | | แจ้งเตือนอัตโนมัติ | ✓ | ✓ | ✓ | ✓ | always on |
| 22 | | Daily Summary ทุกเช้า 08:00 น. | ✓ | ✓ | ✓ | ✓ | always on |
| 23 | แชทลูกค้า | แชทกับลูกค้า (Live Chat + รูป) | — | ✓ | ✓ | ✓ | `feature.live_chat` |
| 24 | | LINE บริการลูกค้า | — | ✓ | ✓ | ✓ | `feature.customer_line_link` |
| 25 | | ตั้งค่า SLA การตอบแชท | — | ✓ | ✓ | ✓ | `feature.live_chat` |
| 26 | รายงาน | รายงานพื้นฐาน | ✓ | ✓ | ✓ | ✓ | always on |
| 27 | | ถามรายงานด้วย AI (ตาราง/กราฟ) | — | 30 ครั้ง/เดือน | 100 ครั้ง/เดือน | เพิ่มโควต้าได้ | `quota.ai_reports_per_month` |
| 28 | งานบริการ / งานซ่อม | LINE ช่าง / ทีมช่าง | — | ✓ | ✓ | ✓ | `feature.service` |
| 29 | | รับงาน เช็คอิน + พิกัด ส่งรูปหน้างาน | — | ✓ | ✓ | ✓ | `feature.service` |
| 30 | | รายงานการซ่อม PDF + ลายเซ็น | — | ✓ | ✓ | ✓ | `feature.service` |
| 31 | | แบบประเมินหลังงานเสร็จ | — | ✓ | ✓ | ✓ | `feature.service` |
| 32 | ทะเบียนเครื่อง / ประกัน | บันทึก Serial Number รุ่นที่ซื้อ-ระยะประกัน | — | ✓ | ✓ | ✓ | `feature.warranty` |
| 33 | | Import CSV | — | ✓ | ✓ | ✓ | `feature.warranty` |
| 34 | | ตรวจสถานะประกันจากงานซ่อม | — | ✓ | ✓ | ✓ | `feature.warranty` |
| 35 | แบบฟอร์มเอกสาร | AI ร่างแบบฟอร์ม / อัปโหลด Word Template | — | ✓ | ✓ | ✓ | `feature.custom_documents` |
| 36 | บทบาทและสิทธิ์ | บทบาทมาตรฐาน | ✓ | กำหนดเองได้ | กำหนดเองได้ | กำหนดเองได้ | `feature.custom_roles` (standard roles always on) |
| 37 | | ขั้นตอนอนุมัติหลายระดับ | — | — | ✓ | ✓ | `feature.multi_level_approval` |
| 38 | | ประวัติการใช้งาน (Audit Log) | ✓ | ✓ | ✓ | ✓ | always on |
| 39 | สิทธิ์ความเป็นเจ้าของร้าน | โอนความเป็นเจ้าของร้าน | ✓ | ✓ | ✓ | ✓ | always on |
| 40 | การเชื่อมต่อ API | เชื่อมต่อ API ภายนอก | — | — | ✓ | ✓ | `feature.external_api` |
| 41 | จำนวนผู้ใช้งาน | (จำนวนผู้ใช้งาน) | 5 คน | 15 คน | 50 คน | มากกว่า 50 คน | `limit.members` |

Row 36 is the one mixed row: *standard roles* are ✓ everywhere (always on); *customising* is
Pro and up. The key covers only the customising half.

Rows 6 and 24 share a key on purpose: "LINE บริการลูกค้า" **is** the Customer OA, and the
Customer OA is where a customer links to a shop — the table gives them identical columns and
the code has one road for both (`customer_license_links`). Rows 23 and 25 likewise (the SLA
is the live-chat SLA, `live_chat.SLA_KEYS`).

### 2.1 The plans in code

```
                       Starter   Pro    Enterprise   Enterprise Plus
feature.customer_line_link   —      ✓        ✓             ✓
feature.live_chat            —      ✓        ✓             ✓
feature.service              —      ✓        ✓             ✓
feature.warranty             —      ✓        ✓             ✓
feature.custom_documents     —      ✓        ✓             ✓
feature.custom_roles         —      ✓        ✓             ✓
feature.multi_level_approval —      —        ✓             ✓
feature.external_api         —      —        ✓             ✓
quota.ai_reports_per_month   0     30      100           100 (admin top-up on Pro and up, §13 Q3)
limit.members                5     15       50           no cap (None)
```

`plan_code` values: `starter`, `pro`, `enterprise`, `enterprise_plus`. Display labels:
`Starter`, `Pro`, `Enterprise`, `Enterprise Plus` (never translated — they are product names).
Each plan has a `rank` (0–3); "Pro ขึ้นไป" is computed as the lowest-ranked plan that includes
the key, never typed by hand.

---

## 3. Data model

### 3.1 Decision: a plan enum + a feature matrix in code (not a `plans` table)

**Recommendation: `licenses.plan_code` (a constrained string) + `PLANS` as a Python literal in
the Data tier (`data/chann_data/plans.py`).** Per-licence exceptions live in
`license_settings`, which already exists for exactly this.

Why not a `plans` table:
- A feature key means nothing until code enforces it. A row added to a table at runtime
  ("Pro now has external_api") would produce a state no test ever ran — the same trap the
  permission catalogue avoids (review C9, 6 Sep 2026: *"catalogue entries nothing enforced"*).
- Four fixed plans. A plan change is a product decision that ships with code and a gate run.
- The "table and code agree" test (§11.1) is a direct comparison of the owner's HTML with one
  literal. With a table it would compare the HTML with a migration's seed rows, and drift
  would appear the first time someone edits a row in production.
- The flexible part — "this one shop gets more AI credits" — is a per-licence number, which is
  a `license_settings` row (the admin already edits `ai_chart_quota` there).

**This departs from the Master Spec on purpose, and says so.** `docs/CHANN_CRM_AI_MASTER_SPEC.md`
§17.5.3 (Billing & Subscription, never built) sketches a `subscription_plans` table
(`plan_code TRIAL|STARTER|PRO|ENTERPRISE`, `max_members`, `features_json`, `price_monthly`) and
`license_subscriptions`. That design is for billing, which is out of scope (§14), and its four
codes do not match the owner's table (no Enterprise Plus; "TRIAL" as a plan, where here a trial
is a status). 21D keeps the part that is enforcement and leaves the billing part for when 17.5 is
built: the `plan_code` strings here are the keys such a table would use (`starter`, `pro`,
`enterprise`, `enterprise_plus`), so a later `subscription_plans` row (price, provider ids) joins
on `plan_code` while the feature matrix stays in code. Also note 17.5's `invoices` table name is
already taken by the shop's own customer invoices (round 20V) — 17.5 will need another name.

Why the Data tier owns the matrix (not the Application): the two limits must be enforced
**inside the Data tier's own transaction** — a member joining and a credit being spent are
both races (two invite redemptions in the same second; two report questions in the same
second — `consume_ai_chart` already locks for this). The Application receives the resolved
entitlement with the principal, exactly as it receives `permission_keys` today. This follows
the permission catalogue's precedent (`chann_data/permissions.py` owns keys;
the Application reads them through the Data API).

### 3.2 Where the plan lives

```python
# data/chann_data/models.py — License
plan_code: Mapped[str] = mapped_column(String(24), nullable=False,
                                       default="pro", server_default="pro")
# CHECK ck_licenses_plan_code: plan_code IN ('starter','pro','enterprise','enterprise_plus')
```

```python
# data/chann_data/plans.py  (new — holds no behaviour but resolve())
FEATURE_KEYS = ("feature.customer_line_link", "feature.live_chat", "feature.service",
                "feature.warranty", "feature.custom_documents", "feature.custom_roles",
                "feature.multi_level_approval", "feature.external_api")
QUOTA_KEYS = ("quota.ai_reports_per_month",)
LIMIT_KEYS = ("limit.members",)

@dataclass(frozen=True)
class Plan:
    code: str; label: str; rank: int
    features: frozenset[str]
    ai_reports_per_month: int
    members: int | None          # None = no cap
    quota_top_up: bool           # may the admin add credits (Enterprise Plus)

PLANS: dict[str, Plan] = {...}   # the §2.1 matrix, literally
DEFAULT_PLAN = "pro"

def resolve(license, settings: dict) -> dict:
    """{"code","label","features":[...],"locked":{key: min_plan_code},
        "limits":{"members": int|None, "ai_reports_per_month": int}}"""
```

`resolve()` is returned **with** every principal the Data tier already builds:
`authorization_context`, `memberships_of` (beside `license_status`), `resolve_api_key`, and
`platform_tenant(s)`. One shape, one function.

**Cache seam (important).** `authorization_context` is cached per `(license, chann_uid,
channel)` (`k_permissions`). The plan must **not** be folded into that entry, or a plan change
reaches each person only when their entry expires. The plan is cached under its own
licence-level key `k_license_plan(license_id)` and the admin's plan PATCH invalidates that one
key. (The tier-seam lesson in CLAUDE.md §-1: this is exactly the bug that looks right from
either tier alone.)

### 3.3 Trials

**One field, no separate "trial plan".** A self-registered licence is created with
`status="trial"` and `plan_code="pro"` (the server default): a trial shows the product people
most often buy, including technicians and customer LINE. When the admin makes the shop active
they choose the plan in the same form (§9); the plan field is always visible, so the admin can
also set a trial to Starter or Enterprise to demo exactly what a prospect is buying. Trial
expiry and suspension (`services/trials.py`) are unchanged and independent of the plan.

### 3.4 When the plan changes

- **Effective immediately** (no billing, no proration, no scheduled change). The admin's PATCH
  writes `plan_code`, writes one `audit_log` row (`action="update"`, `entity_type="license"`,
  `field_changes={"plan_code": {old, new}}` — `update` is an existing verb of the CHECK
  constraint; no new verb), and invalidates `k_license_plan`.
- **The shop's owner is told** on the Sales OA (best-effort, `send_notification`,
  type `plan_changed`): `แพ็กเกจของ {company} เปลี่ยนเป็น {plan} แล้ว` + one line per feature
  gained or locked. Dual delivery like the trial notices.
- **Nothing is deleted, ever.** A plan going down locks; a plan going up unlocks the same rows.

What a downgrade does, per feature (Pro → Starter is the worst case):

| Locked | What stays in the database | What people see |
|---|---|---|
| `feature.service` | tickets, reports, photos, surveys, technician memberships, teams | Tickets/Reports/Teams/Approvals/Satisfaction pages show the locked panel (§8.2); chat refuses (§6); Technician OA answers with the plan notice (§7.2); job-SLA / approval-SLA / survey sweeps skip the shop; Daily Summary omits the job section |
| `feature.warranty` | warranties | Warranties page locked; chat refuses; ext API 403 |
| `feature.customer_line_link` | `customer_license_links` rows | Customer OA answers the plan notice (§7.3); new links refused; pushes to customers (documents, receipts, ticket status, surveys) skipped; "ส่งให้ลูกค้าทางไลน์" disabled with the plan reason |
| `feature.live_chat` | chat sessions and messages | Chats page locked; "คุยกับร้าน" on the Customer OA is already locked by the link key; chat-SLA sweep skips |
| `feature.custom_documents` | uploaded templates and versions | New documents render with the system default template; already-issued PDFs are unchanged (their `data_snapshot` is frozen); Templates page read-only with the plan notice |
| `feature.custom_roles` | custom roles and their grants | Members keep the role they hold and its grants (removing them would lock people out of their day); creating/editing a role or assigning someone to a non-standard role is refused |
| `feature.multi_level_approval` (Enterprise → Pro) | the saved multi-step rule | New reports open **step 1 only**; reports already mid-flow keep the steps they were opened with; the config page shows the saved rule with "ใช้เฉพาะขั้นแรกตามแพ็กเกจ Pro" |
| `feature.external_api` (Enterprise → Pro) | API keys (not revoked) | Every request 403 `plan_required`; the keys work again on upgrade; the API page shows the keys read-only with the plan notice |
| `quota.ai_reports_per_month` | usage counter | New allowance applies at once; credits already used this month still count |
| `limit.members` | every member | **The downgrade is refused** while active members exceed the target plan's limit (§5.7, §13 Q2) — the admin inactivates people first. No shop is ever over its limit. |

---

## 4. Entitlement keys

Ten keys in three families. The vocabulary is closed: `check-perms.py` rejects any key not in
`plans.FEATURE_KEYS | QUOTA_KEYS | LIMIT_KEYS` (§10).

| Key | Thai label (refusals, admin) | English label |
|---|---|---|
| `feature.customer_line_link` | ผูก LINE ลูกค้ากับร้าน | Customer LINE link |
| `feature.live_chat` | แชทกับลูกค้า | Customer chat |
| `feature.service` | งานบริการ / งานซ่อม และทีมช่าง | Service jobs and technicians |
| `feature.warranty` | ทะเบียนเครื่อง / ประกัน | Warranty register |
| `feature.custom_documents` | แบบฟอร์มเอกสารของร้านเอง | Custom document templates |
| `feature.custom_roles` | สร้างและแก้บทบาทเอง | Custom roles |
| `feature.multi_level_approval` | ขั้นตอนอนุมัติหลายระดับ | Multi-level approval |
| `feature.external_api` | เชื่อมต่อ API ภายนอก | External API |
| `quota.ai_reports_per_month` | ถามรายงานด้วย AI | AI reports |
| `limit.members` | จำนวนผู้ใช้งาน | Users |

`entitled(plan, "quota.ai_reports_per_month")` is `allowance > 0` — on Starter the AI report
road is **locked**, not "used up": the person hears the plan, not a counter.

### 4.1 From permission keys to features — the one mapping

Most gated features already have their own permission-key family. The Application holds one
table (`application/chann_app/services/entitlements.py`):

```python
#: A permission key in one of these families is usable only when the plan has the feature.
PERMISSION_FEATURE: dict[str, str] = {
    "ticket.":          "feature.service",
    "service_report.":  "feature.service",
    "approval.":        "feature.service",      # approvals are of service reports only
    "warranty.":        "feature.warranty",
    "chat_session.":    "feature.live_chat",
}
#: Every other permission key, named — so a NEW key must be classified, not forgotten.
ALWAYS_ON_PERMISSIONS: frozenset[str] = frozenset({"customer.read", ..., "pdpa.request.process"})
```

Features that share a permission key with always-on work are checked **at the route / handler**
by name, not by key:

| Feature | Shared key | Where the named check goes |
|---|---|---|
| `feature.custom_roles` | `role.manage` | create role, edit a role's grants, compile-policy, assign a non-standard role (standard: owner/admin/member/cs/technician) |
| `feature.custom_documents` | `setting.manage` | template upload, AI draft, activate a non-default template, the renderer's template choice |
| `feature.external_api` | `setting.manage` | `api_principal` (every ext request), api-key **create** route and chat verb (list and revoke stay open on every plan — ruling R-D) |
| `feature.multi_level_approval` | `approval.manage` | saving a workflow with > 1 step; opening steps for a new report |
| `feature.customer_line_link` | (customer OA principal; `invoice/quote.update` for send) | customer link, customer principal resolve, `document_send`, customer pushes |
| `feature.service` (technician teams, satisfaction) | `team.manage`, `view_reports` | technician-team routes/verbs; survey summary route; the two service basic reports (§5.6) |

---

## 5. Enforcement — one road for all surfaces

### 5.1 The principal carries the plan

```python
@dataclass(frozen=True)
class TenantPrincipal:
    ...
    permission_keys: frozenset[str]          # role grants MINUS plan-locked keys (effective)
    plan: PlanView = PlanView.unknown()      # code, label, features, locked, limits
    plan_locked_keys: frozenset[str] = frozenset()   # held by the role, locked by the plan

    def require(self, key):              # unchanged signature
        if key in self.plan_locked_keys:
            raise plan_required(feature_of(key), self.plan)       # 403, §5.2
        ...existing permission check...
    def require_feature(self, feature): # new — for the §4.1 named checks
        if not self.plan.has(feature):
            raise plan_required(feature, self.plan)
```

Built in **one** place — `resolve_tenant_principal` (LIFF) and `api_principal` (ext) — from
the Data tier's `plan` payload. Subtracting locked keys from `permission_keys` is what makes
every one of the ~146 existing `principal.require(...)` calls and every chat
`"x" in set(permission_keys)` check plan-aware **without editing them**; `plan_locked_keys` is
what lets them say *why*.

`entitled(license, key)` from the brief is `principal.plan.has(key)` on a request, and
`entitlements.entitled(plan_payload, key)` in sweeps that have no principal.

**Fail direction.** If the Data tier returns no plan (old Data image during a rolling deploy,
or a read failure) the principal gets `PlanView.unknown()`, which behaves as **Pro** — the
backfill value — so a transient failure never locks a paying shop out of what it had. The
quota already fails open for the same reason (`chart_quota.spend_one`).

### 5.2 The standard refusal shape

```json
HTTP 403
{"error": "plan_required",
 "feature": "feature.service",
 "plan": "starter",
 "min_plan": "pro",
 "message": "Service jobs and technicians are included from the Pro plan. This shop is on Starter."}
```

The ext API's `_error_body` already reads `error` then `message`, so the external shape comes
out as `{"error": {"code": "plan_required", "message": "…"}}` with no change to it. The LIFF
pages translate `plan_required` in `_strings.ts` using `feature`/`min_plan`.

### 5.3 Chat

- **Central gate** (`chat.py` ~L30177, the model road): after `required_permission` and before
  the "no permission" branch, `if needed in plan_locked_keys or not entitled(feature_for_intent)`
  → `plan_refusal(...)`. Order: unknown feature → wrong OA → **plan** → permission → handler.
  Plan comes before permission because "your shop's plan doesn't have it" is the truer answer
  (and a role grant is irrelevant until the plan has it).
- **`suggest_what_you_can_do`** gets the same branch (it is what the trigger roads fall into).
- **The ~40 bare `ChatReply(text=_t(SUGGEST_NO_PERMISSION_LEAD, language))` sites** are
  converted mechanically to `_no_permission(ctx, key, language)`, which returns the plan
  refusal when the key is plan-locked and the existing text otherwise. A unit test forbids a
  new bare use. (Without this, a Starter owner typing a service trigger would be told to ask
  the owner — themselves — for a permission.)
- **Refusals are decline-only.** Nothing new acts on a word. The `สร้างรายงานด้วย AI:` prefix
  on Starter declines before the report model call (a word declining — allowed); every other
  plan refusal is on the model's `(action, entity)`.
- The refusal carries the guide button like every other refusal; for the owner it also carries
  the contact button (§8.3).

### 5.4 Dashboard (Presentation)

- `GET /licenses/{id}/me/permissions` adds `plan: {code, label, features, locked, limits,
  usage: {members, ai_reports_used, ai_reports_month}}` — the session the pages already load,
  no extra call.
  As built (Task 5, ruling R-C): `permission_keys` = the EFFECTIVE keys (locked removed),
  `held_keys` = the role's keys before the plan, `plan_locked_keys` = held ∖ effective, `plan`
  (no usage — that is `GET /licenses/{id}/plan`), `sales_contact` (owner / `setting.manage` only).
  The nav tests *permission* against `held_keys` and the lock against `plan`.
- `NavEntry` gains `feature?: string`. `mayOpen` is unchanged (permissions); a new
  `lockedBy(entry, plan)` decides the lock, and `navState(entry, access)` gives one answer per
  entry. **As built (ruling R-C): a locked entry is shown with a lock icon and the reason under
  the label ("Pro ขึ้นไป") to anyone whose ROLE holds the permission that would open it
  (`held_keys`), and a tap opens the locked page (§8.2); it stays hidden from anyone without that
  permission, exactly as before this round.** (The earlier wording — shown only to holders of
  `setting.manage`, hidden from everyone else — was superseded: a salesperson whose role grants
  service jobs is told why the page is locked instead of finding it gone.) An entry with
  `lockMode: "part"` stays open when the rest of the page is usable with the effective keys.
  ui-ux rule: *disabled needs a reason* — every disabled control in this round has its reason
  visible, not in a tooltip only.
- Pages opened directly (bookmark, deep link) render `<PlanLocked feature=… />` (§8.2) instead
  of the page, from the same `plan` payload — and the API refuses anyway.
- Buttons inside always-on pages that reach a locked feature (e.g. "ส่งให้ลูกค้าทางไลน์",
  "เพิ่มขั้นอนุมัติ", "อัปโหลดแบบฟอร์ม", "สร้างบทบาท", "เชิญสมาชิก" at the limit) are disabled
  with the reason line under them — the same pattern 21C used for "ลูกค้ายังไม่ผูกไลน์".
- **ui-ux-pro-max is invoked for every dashboard change in this round** (owner rule, 20 Sep
  2026) and the guide images for touched pages are re-rendered and looked at.

Nav entries → feature (from `_nav-model.tsx`): `chats` → live_chat · `tickets`, `teams`,
`reports` (service reports), `satisfaction`, `approvals` → service · `warranties` → warranty ·
`templates` → custom_documents · `apiKeys` → external_api · `aiReports` → *not locked*; the page
stays (the basic reports are always on) and only its question box is locked on Starter (§5.6).
`roles` → *not locked*; the page stays (standard roles), its create/edit controls are.

### 5.5 External API

- `api_principal`: after the key resolves, `require_feature("feature.external_api")` →
  403 `plan_required` on **every** route, including `/me`. Key checks stay first (401 for a bad
  key still wins — an attacker learns nothing about the shop's plan).
- Inside, routes keep `principal.require(...)`; tickets/warranties are covered by the key
  families automatically (Enterprise has them, but the road is the same).
- Making a key refuses on plan — the API page's create button and the chat verb `("create", "api_key")`.
  Listing and revoking keys stay open on every plan (pre-flight ruling R-D, 24 ก.ย. 2569): a
  downgraded shop must be able to see and revoke what an outside system still holds; the keys
  themselves already answer 403 `plan_required` through `api_principal`.
- `docs/API.md` gains the `plan_required` code in its error table.

### 5.6 AI report credits and the basic reports

- The allowance is **plan default, unless the admin set an override**:
  `allowance = setting("ai_chart_quota") if set and plan.quota_top_up_allowed else plan.ai_reports_per_month`
  (see §13 Q3 for which plans honour the override). Computed in the Data tier
  (`LicenseSettingRepository.ai_chart_allowance`), so `consume_ai_chart` stays the one atomic
  spend. `DEFAULT_QUOTA = 30` disappears from both tiers; the column/setting names stay
  (`ai_chart_quota` — 21C's rule: *no rename for a word*).
- **Starter:** `quota.ai_reports_per_month` is locked → the ad-hoc road and the AI-designed
  picture refuse on plan (no model call for the report, no credit). The five basic reports stay
  free and are not a quota matter.
- **The two service basic reports on Starter** (`open_jobs_by_tech`, `satisfaction_avg`): the
  chooser (`CHOOSE_PROMPT`) is **not changed** (Ruling 22: any prompt change re-measures seven
  sentences). If it chooses one of the two on a shop without `feature.service`, the answer is
  the plan refusal for `feature.service`; the dashboard shows three cards, not five (§13 Q4).
- Every place that prints "30" learns the allowance: the receipt already carries
  `allowance`; `guides.py` step text ("ค่าเริ่มต้น 30 ครั้งต่อเดือน") becomes "ตามแพ็กเกจ
  (Pro 30 · Enterprise 100 ครั้งต่อเดือน)" and `render-guides.py` is re-run.

### 5.7 Invite codes and the member limit

- **Who counts:** distinct `chann_uid` with an `active` row in `license_members`, any channel,
  owner included. A person who is both sales and technician is one user. Customers never count.
- **Hard check in the Data tier:** `redeem_invite` locks the licence row (`SELECT … FOR UPDATE`),
  counts, and raises `MemberLimitReached(limit, plan)` → 409
  `{"error": "member_limit_reached", "limit": 5, "plan": "starter"}`. The same check runs on
  re-activating a member (status → active). Creating a company (the owner, count 1) never
  trips it.
- **Early refusal in the Application:** minting an invite (chat and dashboard) is refused when
  the count is already at the limit, so the owner hears it before handing out a code.
- **A downgrade is refused while the shop is over the target's limit (owner, 24 ก.ย. 2569).**
  "Active member" is a distinct `chann_uid` with a `license_members.status = 'active'` row (any
  channel). A member is inactivated by the existing remove road — `MemberRepository.set_status(...,
  status="removed")`, reached from the admin console's "ถอดออก" and the shop's own members page —
  which keeps the row for the audit trail. The Data tier's plan PATCH locks the licence row,
  counts, and when `active > target.members` answers **409**
  `{"error": "plan_member_limit", "members": M, "limit": L, "inactivate": N, "plan": "<code>",
  "message": "ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น <plan>"}` (N = M − L,
  `<plan>` the display label) and the plan stays unchanged. Reactivating a removed member, the
  platform's "move member" into a shop, and an invite redemption are all the same seat check, so
  after this round **no shop can be over its limit** — except through the migration, whose
  backfill rules are built so that never happens and which asserts it (§12).
- **A technician invite needs `feature.service`** (minting and redeeming): on Starter the code
  cannot be made; a code made before a downgrade is refused at redeem (§7.2).
- The owner is told (best-effort) when someone was turned away by the limit, so a failed join
  is not silent.

### 5.8 Rich menu

**Correction to the brief (and to CLAUDE.md §13's stated intent):** Phase 19's rich menu as
built is chosen by **OA and language**, not by permission (`services/richmenu.py`, `scripts/richmenu/generate.py`) — every Sales OA user gets
the same six-plus-six tiles. A per-plan menu would also be wrong for a person in two shops on
the same OA (the menu is per LINE user, the plan is per shop).

**Recommendation for 21D: no per-plan menus.** Every tile either sends a text message (which
goes through chat and receives the plan refusal — "รายการรออนุมัติ", "ทีมช่าง") or opens a
LIFF page (which shows the locked panel — "แชทลูกค้า"). A Starter-specific menu is listed as
§13 Q6, default *not now*.

### 5.9 Background work

Sweeps with no principal call `entitlements.entitled(plan_payload, key)` per licence before
acting: job SLA (`job_sla.py`), approval SLA (`approval_sla.py`), chat SLA
(`live_chat.py`), survey sending, customer pushes (`notify.py` customer targets,
`document_send.py`), Daily Summary (job section). Follow-up reminders, trial expiry and the
daily summary itself are always on.

---

## 6. Chat specifics

### 6.1 Reading the plan in chat (parity)

The dashboard gains a plan card (§8.4), so chat must answer "ร้านใช้แพ็กเกจอะไร" /
"เหลือเครดิตรายงาน AI เท่าไหร่" / "เชิญได้อีกกี่คน". New pair `("read", "plan"): "setting.manage"`
in `ACTION_PERMISSIONS`, a `plan` entity in `INTENT_SYSTEM_PROMPT`, handler
`_handle_plan_read`. **Model-first order:** run `scripts/dev/ask-model.py` on the three
sentences above **before** touching the prompt, record the raw JSON before/after in the
handoff (21C practice). If the deployed model already returns `read`/`setting`-like shapes the
handler maps them; no trigger words.

### 6.2 The refusal (exact copy)

```python
PLAN_REQUIRED = {
  "th": "🔒 «{feature}» อยู่ในแพ็กเกจ {min_plan} ขึ้นไป — ร้านนี้ใช้แพ็กเกจ {plan}\n"
        "ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ",
  "en": "🔒 «{feature}» is included from the {min_plan} plan — this shop is on {plan}.\n"
        "Nothing the shop already has has been deleted.",
}
PLAN_REQUIRED_OWNER_TAIL = {       # owner, or anyone holding setting.manage
  "th": "อยากเปิดใช้: ติดต่อทีม Chann CRM AI {contact}",
  "en": "To upgrade, contact the Chann CRM AI team {contact}",
}
PLAN_REQUIRED_MEMBER_TAIL = {
  "th": "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย",
  "en": "If you need it, let the shop owner know.",
}
MEMBER_LIMIT_REACHED = {
  "th": "ร้านมีผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว — เอาสมาชิกที่ไม่ได้ใช้ออก หรืออัปเกรดแพ็กเกจเพื่อเชิญเพิ่ม",
  "en": "The shop has all {limit} users its {plan} plan allows — remove someone who no longer uses it, or upgrade to invite more.",
}
AI_REPORTS_LOCKED_HINT = {        # appended on Starter's AI-report refusal
  "th": "รายงานพื้นฐาน (มูลค่าดีลทั้งหมด · ยอดปิดเดือนนี้ · ยอดค้างชำระ) ยังถามได้ฟรีเสมอ",
  "en": "The basic reports (pipeline value · won this month · outstanding invoices) are always free.",
}
```

`{feature}` is the §4 Thai label, `{min_plan}` / `{plan}` the display labels, `{contact}` the
configured contact (§8.3). Example, Starter owner typing *"เปิดงานซ่อมให้คุณสมชาย"*:

```
🔒 «งานบริการ / งานซ่อม และทีมช่าง» อยู่ในแพ็กเกจ Pro ขึ้นไป — ร้านนี้ใช้แพ็กเกจ Starter
ข้อมูลเดิมของร้านยังอยู่ครบ ไม่มีอะไรถูกลบ
อยากเปิดใช้: ติดต่อทีม Chann CRM AI LINE @channcrm
```

Five lines at most — inside the 15-line ceiling and `check-chat-format`.

---

## 7. LINE OAs on a plan without the feature

### 7.1 Sales OA
Everything always-on works; locked verbs get §6.2. Tiles as §5.8.

### 7.2 Technician OA (no `feature.service` — Starter, or a Pro shop downgraded)

- **Minting a technician invite** on the Sales OA/dashboard: refused with §6.2 for
  `feature.service`.
- **Scanning / typing an invite code** made before a downgrade:
  `รหัสนี้ใช้ไม่ได้ตอนนี้ — ร้าน {company} ใช้แพ็กเกจ {plan} ซึ่งยังไม่มีงานบริการและทีมช่าง แจ้งเจ้าของร้านได้เลย`
  No membership row is written; the code is not consumed (it works after an upgrade, until it
  expires as usual).
- **An existing technician** (downgraded shop) sending anything on the Technician OA while that
  shop is active: `ร้าน {company} ปิดงานบริการและทีมช่างไว้ชั่วคราว (แพ็กเกจ {plan}) — งานและรายงานเดิมยังอยู่ครบ แจ้งเจ้าของร้านถ้าต้องการเปิดใช้`
  Once per message; profile / language / shop switch still work (they are not the shop's
  feature). A technician in two shops can switch to the other shop.

### 7.3 Customer OA (no `feature.customer_line_link`)

- **Linking by company code**: `ร้าน {company} ยังไม่เปิดให้บริการลูกค้าทาง LINE — ติดต่อร้านได้ที่ {company_phone}`
  (phone line omitted when the shop has none). No link row written.
- **A customer already linked** (downgraded shop): every message about that shop gets the same
  sentence; the storefront search across shops still works (it is platform-wide, not the
  shop's feature). The link row is kept.

---

## 8. What the person sees on the dashboard

### 8.1 Nav
Locked item, for holders of `setting.manage`: lock glyph + label + second line
`Pro ขึ้นไป` (muted, ≥ 4.5:1 contrast). Tap → the locked page (§8.2), not nothing.

### 8.2 Locked page — `<PlanLocked>`
```
[lock icon]
ฟีเจอร์นี้อยู่ในแพ็กเกจ {min_plan} ขึ้นไป
{feature}: {one-line description from the plan table}
ร้านของคุณใช้แพ็กเกจ {plan} · ข้อมูลเดิมยังอยู่ครบ ไม่มีอะไรถูกลบ
[ ติดต่อทีม Chann เพื่ออัปเกรด ]          ← owner / setting.manage only
ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย         ← everyone else, no button
```
English: *"This feature is included from the {min_plan} plan"* / *"Your shop is on {plan} ·
nothing already recorded has been deleted"* / *"Contact Chann to upgrade"*.

### 8.3 What "upgrade" does
**There is no billing and no self-serve upgrade.** The CTA opens the Chann team's contact —
one configured value `CHANN_SALES_CONTACT` (label + URL, e.g. a LINE OA link), Application
runtime config (added to `RUNTIME_CONFIG_CONTRACT.md`, not a secret). When unset the button is
not drawn and the text reads `ติดต่อทีม Chann CRM AI ที่ดูแลร้านของคุณ`. The platform admin then
changes the plan in `/admin` (§9). Logging "upgrade interest" is not in this round.

### 8.4 Plan card (company page, `setting.manage`)
`แพ็กเกจ {plan}` · `ผู้ใช้ {n}/{limit} คน` (or `{n} คน · ไม่จำกัด`) · `เครดิตรายงาน AI เดือนนี้ {used}/{allowance}`
· the eight features with ✓ or 🔒 + min plan. Read-only; the contact button beside it.

### 8.5 Disabled-with-reason lines (inline, under the control)
- Invite at limit: `ผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว`
- AI question box on Starter: `ถามรายงานด้วย AI มีในแพ็กเกจ Pro ขึ้นไป · รายงานพื้นฐานด้านบนใช้ได้ฟรีเสมอ`
- Approval config "เพิ่มขั้น" on Pro: `อนุมัติหลายระดับมีในแพ็กเกจ Enterprise ขึ้นไป`
- Roles "สร้างบทบาท" on Starter: `สร้างบทบาทเองมีในแพ็กเกจ Pro ขึ้นไป · ใช้บทบาทมาตรฐานได้ตามปกติ`
- Send-by-LINE when the shop lacks the link feature: `ส่งทางไลน์มีในแพ็กเกจ Pro ขึ้นไป`

---

## 9. Admin console (`/admin`)

- **Tenant list:** a `แพ็กเกจ` column and filter (`?plan=`), beside status.
- **Tenant page (`TenantEdit.tsx`):**
  - `แพ็กเกจ` select (4 options). Changing it opens a confirm dialog that lists, from the Data
    tier's counts, what gets locked and what stays: *"จะล็อก: งานบริการ (ใบงานเปิดอยู่ 12 ·
    ช่าง 3 คน), เชื่อมต่อ API (key ใช้งานอยู่ 1) · ไม่มีข้อมูลถูกลบ"* and, when members exceed the
    new limit, the confirm button is not offered: the dialog says
    *"ต้องปิดใช้งาน (inactivate) สมาชิก 4 คนก่อนลดแพ็กเกจเป็น Starter"* (9 active, limit 5) and
    points at the members card below, where "ถอดออก" inactivates. If the PATCH is sent anyway
    (a stale page), the Data route answers the same sentence with 409 and the plan is unchanged.
    Confirm → PATCH.
  - **Usage vs limits** (read-only): users `n / limit`, AI credits `used / allowance` this month.
  - `เพิ่มโควต้ารายงาน AI` (the existing `ai_chart_quota` field, relabelled): empty = plan
    default; a number = this shop's monthly allowance. Honoured on Pro and up (§13 Q3);
    disabled with its reason on Starter.
- **API:** `PATCH /platform/tenants/{id}` accepts `plan_code` (422
  `{"error":"unknown_plan"}` otherwise); `GET` returns `plan_code`, `plan` (resolved),
  `usage`, and `plan_change_preview` counts via `GET /platform/tenants/{id}/plan-preview?to=`.
  Only `require_admin` routes write the plan; no tenant route can.

---

## 10. Parity and checkers

Owner rule 2: *what chat can do the UI can do.* The new failure to catch is **"gated in chat but
not on the dashboard"** (or the API) — a Starter shop refused in LINE and served on the screen.

- **`check-perms.py`** learns the new family: it collects `require_feature("…")`,
  `entitled(…, "…")`, `plan.has("…")` (Python) and `feature: "…"` / `planHas("…")` (tsx), and
  fails on any key not in `plans.FEATURE_KEYS | QUOTA_KEYS | LIMIT_KEYS`. It also fails when a
  permission key in `PERMISSION_KEYS` is in **neither** `PERMISSION_FEATURE` **nor**
  `ALWAYS_ON_PERMISSIONS` — a new key has to be classified.
- **`check-parity.py`** gains a plan section: for every `(entity, action)` in
  `ACTION_PERMISSIONS`, the feature it resolves to (by key family or by the named-check table,
  `entitlements.NAMED_FEATURE_CHECKS`) must be the same feature the matching dashboard route
  enforces and the nav entry for that page declares. Output lines like
  `PLAN  warranty/create: chat=feature.warranty  dashboard=—` ; accepted exceptions go in
  `ACCEPTED_PLAN` with a reason, as today.
- **`check-routes.py`**: every ext route is behind `api_principal` (already) — and
  `api_principal` must contain `require_feature("feature.external_api")`.
- **`check-i18n-usage.py`**: the new strings in both languages.

---

## 11. Tests and gate

### 11.1 Unit — the matrix is the table
`tests/unit/test_round21d_plan_matrix.py` **parses the owner's HTML**
(`docs/superpowers/specs/2026-09-24-sales-plans-source.html`) and, with a `ROW_MAP` (row label →
key or `ALWAYS_ON`) that lives beside `PLANS`, asserts:
1. every row in the HTML is in `ROW_MAP` and vice versa (41 = 41);
2. an `ALWAYS_ON` row is ✓ in all four columns;
3. for a feature row, the set of ✓ columns equals the set of plans whose `features` contain
   the key;
4. the quota row parses to (—, 30, 100, "เพิ่มโควต้าได้") and equals
   (0, 30, 100, 100 + `quota_top_up`); the users row to (5, 15, 50, >50) and equals
   (5, 15, 50, None);
5. row 36's ✓/"กำหนดเองได้" equals `feature.custom_roles` absent/present.
If marketing edits the table, this test fails until the code is changed on purpose.

Also unit: `resolve()` shapes; `min_plan` is computed (rank); `PERMISSION_FEATURE ∪
ALWAYS_ON_PERMISSIONS == PERMISSION_KEYS` and disjoint; principal subtracts locked keys and
`require` raises `plan_required` (not `permission required`) for a locked key; `PlanView.unknown()`
behaves as Pro; refusal copy (owner vs member tail, ≤ 5 lines); no bare
`SUGGEST_NO_PERMISSION_LEAD` reply remains in `chat.py`.

### 11.2 Boundary
Member limit at `limit-1 / limit / limit+1`; Enterprise Plus with 500 members; two concurrent
redemptions for the last seat (one wins); quota at `allowance-1 / allowance`; override 0 vs
empty; Starter quota 0 → *locked*, not *used up*; approval workflow with 1 vs 2 steps on Pro vs
Enterprise; a principal with no plan payload.

### 11.3 Integration (Postgres)
- Migration `0039_license_plan`: upgrade on a copy with (a) a plain shop → `pro`, (b) a shop
  with an unrevoked API key → `enterprise`, (c) a shop with a 2-step workflow → `enterprise`,
  (d) 16 active members → `enterprise`, (e) 51 → `enterprise_plus`; downgrade drops the column;
  `EXPECTED_MIGRATION_HEAD == "0039_license_plan"` (update the 21C test that pins 0038).
- Plan change Pro → Starter → Pro through the admin PATCH: tickets/warranties/chat sessions/
  templates/custom roles are **byte-identical** after the round trip; the auth cache does not
  serve the old plan (the `k_license_plan` invalidation); an audit row exists.
- Enterprise → Pro: an ext request 403 `plan_required`; after Pro → Enterprise the same key works.
- Multi-step workflow on Pro opens step 1 only; a report opened before the downgrade finishes
  its steps.

### 11.4 Agent-test scenario
`scripts/agent-test/scenarios/plan-starter.yaml` (+ `plan-pro-unchanged.yaml`): a Starter shop — "เปิดงานซ่อมให้สมชาย",
"ลงทะเบียนประกัน SN123", "สร้างรายงานด้วย AI: ยอดขายแยกตามเดือน", "ยอดมูลค่าดีลทั้งหมด"
(free, answered), "เชิญช่าง" → the exact §6.2 texts; the sixth invite redemption refused.
And a Pro shop scenario proving nothing changed for today's shops (the regression that matters
most, since every existing shop becomes Pro). Shipped scenarios are the contract: the full gate
runs, not the chat subset.

### 11.5 The gate for this round
Unit + boundary + integration + sims (`0 FINDINGS`) + `check-parity` (with the new plan
section, clean) + every `check-*` + `typecheck`/`build` + **`simulate-phrasings --real`** (chat
is touched: the refusal branch and §6.1) + `ask-model.py` before/after for §6.1 + guide images
re-rendered **and looked at** for the pages that change (company plan card, locked page, AI
report box on Starter, invite at limit).

Tester checklist gains section **X. รอบ 21D** in `~/CHECKLIST-หลัง-deploy.md`, in the V–W
style: set a test shop to Starter in `/admin` → every locked item's chat reply, nav lock, locked
page, disabled-with-reason lines, ext API 403, technician invite refused, 6th member refused;
set it back to Pro → everything back with the same data.

---

## 12. Migration

```
revision      = "0039_license_plan"          # 17 chars (≤ 32)
down_revision = "0038_deal_closed_at"
```
1. `ADD COLUMN licenses.plan_code VARCHAR(24) NOT NULL DEFAULT 'pro'` (every existing row → Pro
   in one statement) + `CHECK ck_licenses_plan_code`.
2. Backfill **up** so nothing a shop uses today disappears:
   - `enterprise` where the licence has an unrevoked `api_keys` row, **or** an active
     `approval_workflows` row with `jsonb_array_length(rules_json->'steps') > 1`, **or** more
     than 15 distinct active members;
   - `enterprise_plus` where more than 50.
   The migration prints the count per plan (the deploy log is the record).
   **Member-limit assertion (owner, 24 ก.ย. 2569):** after the backfill the migration counts
   active people per licence and, for any licence whose count exceeds its plan's limit, prints
   it and moves it to the smallest plan that fits. With the rules above this never fires; it
   exists so "no shop is over its limit" is true by construction, not by argument.
3. No `plans` table; no setting rows written (an existing `ai_chart_quota` value stays and keeps
   meaning "this shop's monthly allowance").
4. Downgrade: drop the constraint and the column.
5. `data/chann_data/main.py`: `EXPECTED_MIGRATION_HEAD = "0039_license_plan"`; the deploy runs
   the migrate job **before** the data image (CLAUDE.md §-1).

Trials: covered by the default (`pro`, §3.3).

---

## 13. The owner's decisions (24 ก.ย. 2569)

Each question below carried a default; the owner answered all five that needed an answer.
Q6–Q8 stand as written (defaults accepted).

1. **Backfill — DECIDED (the default):** existing shops → **Pro**; a shop with an active API key,
   a multi-step approval chain, or **more than 15** active members → **Enterprise**; **more than
   50** → **Enterprise Plus**. New trials → **Pro** (the server default). Migration §12 also
   asserts no shop ends over its limit.
2. **A shop over its user limit — DECIDED, CHANGED from the default:** a downgrade is
   **refused** while active members exceed the target plan's limit. The admin console shows
   *"ต้องปิดใช้งาน (inactivate) สมาชิก N คนก่อนลดแพ็กเกจเป็น <plan>"*, the plan stays unchanged,
   and the Data route answers **409** with that reason and N (§5.7). Only active members count;
   a removed (`status="removed"`) row does not. No "everyone keeps working over the limit" state
   exists after this round.
3. **AI credits — DECIDED (the default):** Enterprise Plus starts at **100/month**; the admin
   top-up override (`ai_chart_quota`) is honoured on **Pro and up**; values already set keep
   working; Starter stays 0 (locked, not "used up").
4. **Starter's two service basic reports — DECIDED (the default):** Starter sees **three** basic
   reports; งานซ่อมค้างแยกตามช่าง and คะแนนความพึงพอใจเฉลี่ย are hidden and asking for them gets
   the service plan refusal.
5. **The upgrade button — DECIDED (the default):** it opens the owner-supplied contact
   **`CHANN_SALES_CONTACT`** (format `label|url`, tfvars → Application env); **hidden when unset**,
   and the text then reads *"ติดต่อทีม Chann CRM AI ที่ดูแลร้านของคุณ"*.
6. **Rich menu per plan.** Not in this round — one menu per OA; locked tiles answer with the
   plan refusal (default accepted).
7. **Custom roles after a downgrade to Starter.** Members keep the custom role they hold;
   creating/editing roles and assigning a custom role is refused (default accepted).
8. **Plans in code, not the Master Spec's `subscription_plans` table** (§3.1). Code matrix now; a
   billing table joins on the same `plan_code` when Phase 17.5 is built (default accepted).

## 14. Out of scope (said so nobody thinks they were forgotten)

- Billing, invoices *to* shops, payment providers, proration, plan price.
- Self-serve upgrade/downgrade by the shop owner; an "upgrade request" queue.
- Per-seat pricing or buying extra users on Starter/Pro/Enterprise (the member limit is fixed
  per plan; Enterprise Plus has no cap).
- Per-plan rich menus (§13 Q6); scheduled/future-dated plan changes; plan history screens
  beyond the audit log.
- Changing what a *role* means: permissions are untouched; the plan only removes.
