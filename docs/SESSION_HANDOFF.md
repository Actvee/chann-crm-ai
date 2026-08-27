# Session Handoff — 27 Aug 2026

Written because the conversation doing Phases 3-9 hit its context limit
several times across the day. This is what the next AI session needs to
pick up cleanly — read this before `docs/CHANN_CRM_AI_MASTER_SPEC.md`, not
instead of it.

Supersedes the earlier 27 Aug 2026 version of this file (written mid-day,
before Phase 9 existed).

---

## 🔴 DO THIS FIRST — secret rotation, and the leak is still LIVE in HEAD

`envs/dev/terraform.tfvars.bak-<timestamp>` files were committed 7 times in
August 2026 (a deploy-script bug — the `.gitignore` rule for
`terraform.tfvars` didn't match the `.bak-*` suffix). Each is a **full copy**
of every secret this project uses:

- `admin_secret`
- `database_password`
- LINE channel secret + access token, all 3 OAs (Sales/Customer/Technician)
- `jwt_secret`
- `openrouter_api_key`

**New finding, 27 Aug 2026:** the `.gitignore` rule was added afterwards, but
**gitignore does not untrack files already in the index**. All 7 files were
therefore still tracked in `HEAD` of a public repo — not merely in history.
Verify with:

```bash
git ls-files 'infrastructure/terraform/envs/*/terraform.tfvars.bak-*'
```

The commit described under "Uncommitted work" below removes them from the
index. That fixes HEAD only — **it does not remove them from history.**

**Rotate before anything else, in this order:**
1. `openrouter_api_key` — the only one with direct ongoing cost exposure
2. LINE channel secrets/tokens, all 3 OAs — LINE Developers Console
3. `database_password` — `gcloud sql users set-password chann_crm_ai_app --instance=chann1-dev-pg --prompt-for-password`, then update `terraform.tfvars`, plan, apply
4. `admin_secret` and `jwt_secret` — new random values in `terraform.tfvars`, plan, apply (both tiers share these)

After rotating, decide whether to scrub history (`git filter-repo` or BFG).
Scrubbing rewrites every commit hash, so coordinate with anyone holding a
clone.

---

## Where things stand

**Deployed and live on DEV**, in order: Phase 1 (Identity) → 2 (Permissions)
→ 3 (Audit Log) → 4 (AI Infra) → 5 (i18n) → 6 (Chat) → 6.5 (Tenant
Registration) → 7 (Master Data) → 8 (Profiles) → OA-scoping/conversation-
continuity/profile-eligibility fix → OA-aware identity resolution for
Customer/Technician → Phase 9 (CRM core: customers/deals/storefront) →
last-customer-reference + product chat command → last_name+phone
validation rule + safety net → **`_unwrap()` 204-body crash fix (a critical
bug that had silently broken conversation continuity since Phase 6),
deployed and confirmed via `/health` matching `git_commit`.**

`origin/main` HEAD is **`56f296f` — `fix(phase9): _unwrap() crashes on 204
No Content -- critical bug`**.

**Not started (past this point):** Phases 10-20 haven't been started.

---

## Uncommitted work waiting to be deployed — two UX gaps found in live testing

Patch: `phase9-storefront-ux.patch` + `phase9-storefront-ux-deploy.sh`. Two
independent fixes on top of `56f296f` (the real live HEAD), found by the
owner testing the `_unwrap` fix live — both are genuine improvements, not
bugs in the strict sense; the underlying features worked, they just fed
back confusing or presumptuous replies.

### 1. Missing-field prompts leaked raw field-name keys

Reported: asking to create a customer without a last name or phone
answered "กรุณาระบุlast_name, phone" — the English machine-facing key
names, verbatim, mixed into a Thai sentence. `ask_for_missing()` only ever
joined the raw `missing` list with no translation step at all. This
affected every caller of `ask_for_missing`, not just the customer-creation
case, since it's the shared function the whole slot-filling mechanism uses.

Fixed with a small `MISSING_FIELD_LABELS` lookup (`first_name`, `last_name`,
`phone`, `email`, `address`, `target_name`, `product_id`, `product_name`,
`unit_price` → Thai/English labels). Anything not in the table still falls
back to the raw key rather than silently dropping it — an ugly label is
better than a missing one.

### 2. A bare product name in Customer OA fell through to the wrong flow

Reported: typing just "พัดลม" (no "ค้นหา" prefix) got "พิมพ์รหัสร้านค้า
หรือชื่อร้านเพื่อค้นหา" instead of any product results — the storefront
trigger only recognised an explicit "ค้นหา [term]" prefix per the spec's
literal wording, so a bare word fell straight through to the registration
flow's shop-name search, which knows nothing about products at all.

The owner's own framing of the fix mattered here: a bare word is genuinely
**ambiguous** for a customer — "พัดลม" could mean "I want to buy one" or
"how's the repair ticket I filed for mine going?" — so the fix should not
just silently assume "product search" either. Implemented as a two-step
confirmation specifically for the bare-word case (an explicit "ค้นหา
[term]" is already unambiguous and still goes straight to results, no
confirmation needed):

- Bare word, ≥2 chars, not shaped like a company code → try a storefront
  search silently; if it finds nothing, return `None` exactly as before
  (zero regression to shop-code/shop-name lookup). If it finds something,
  ask "พบสินค้าที่เกี่ยวข้องกับ '{query}' ต้องการดูรายการสินค้าไหม?"
  instead of listing results outright.
- A "ใช่"/"yes"/"ok"-shaped reply, or simply re-typing "ค้นหา ...", shows
  the cached results (no second Data-tier query needed — the results from
  the first search are cached in the same `pending_intent` mechanism).
- Anything else (e.g. "พัดลมที่แจ้งซ่อมไว้เป็นยังไงบ้าง") clears the
  pending confirmation and returns `None`, letting the message be handled
  by whatever it actually was instead of being forced into the storefront
  flow. Ticket-status lookup itself isn't built yet (Phase 12) — this
  doesn't add that capability, it just stops presumptuously hijacking a
  message that wasn't a product search in the first place.

New pending-intent state `entity="storefront_confirm"` carries the query
and cached results between the two turns, with its own short TTL (120s —
shorter than the 300s selection-list TTL, since a stale unanswered "did you
want to search?" going cold quickly is the safer default).

### Validated

On top of `56f296f` (the real live HEAD) on a clean clone: applies cleanly
(3-way), **280 tests pass**, 0 skipped (real Postgres), `check-model-kwargs.py`
OK, both tiers boot (data 80 routes, app 21 paths). No migration.

---

## Already deployed (27 Aug 2026) — for context, not action

### 8. `_unwrap()` 204 No Content crash fix (`56f296f`)

**Critical bug, confirmed deployed.** `DataClient._unwrap()` called
`resp.json()` unconditionally on every successful response, including a
bare `204 No Content` — which by HTTP definition has an empty body, so
parsing it as JSON always raised `JSONDecodeError`. This affected every
call to `set_pending_intent`, `clear_pending_intent`, and
`set_last_customer_ref` — meaning conversation continuity had been broken
since Phase 6 first built it, silently, because before the webhook-level
exception-logging safety net (deployed one patch earlier), an uncaught
exception here just killed the request with no reply and nothing in the
logs. The safety net didn't introduce this bug; it's what finally made a
years-old — well, hours-old in this project's young life, but structurally
ancient — bug visible for the first time. Fixed with one change in the one
shared place: `_unwrap()` now returns `None` for `204`/empty-body
responses instead of trying to parse them, covering all 9 endpoints in
this codebase that return `204`. Also fixed in the same patch: the
last_name+phone hard-validation refusal now registers its own
`pending_intent`, so a bare follow-up reply naming only the missing field
actually completes the request (it silently didn't before). New test file
`tests/unit/test_data_client.py` exercises `DataClient` against a real
`httpx.MockTransport` — `FakeDataClient` (used everywhere else) never
touches the real HTTP-unwrapping code path at all, which is why this bug
went uncaught through every earlier patch.

### 7. last_name+phone validation + silent-failure safety net (`50d78ab`)

Two fixes, confirmed deployed via `/health` matching `git_commit`.
Customer creation now requires `last_name` AND `phone` (not "any one of
four fields") per explicit owner instruction — a first name alone doesn't
reliably identify a walk-in customer later, and phone is how staff follow
up. Separately, several handlers added across recent patches
(`create_deal`, `update_customer`, `promote_customer`, `upsert_product`)
had no exception handling at all, and `webhook.py`'s main loop had no
top-level safety net either — an uncaught exception anywhere in "decide
what to reply" killed the whole request silently. Fixed both layers: added
try/except to all four handlers, and a broad `try/except` around the
whole decision block in `webhook.py`, logging the real exception and
falling back to a plain apology. This safety net is what surfaced the
`_unwrap` bug documented above for the first time — see that section for
what it actually was.

### 6. Last-customer-reference + product chat command (`c4fe8b5`)

Two independent gaps found by the owner testing Phase 9 live, after
customer/deal CRUD and deal stage transitions were already confirmed
working end to end.

**"สร้างดีล" with no name at all didn't know who was just discussed.**
`pending_intent` (Phase 6) is deliberately cleared the instant an action
completes — exactly the moment a "who was this about" reference needs to
start existing. Fixed with a new, separate Redis key,
`k_last_customer_ref(chann_uid, oa)`, written whenever a customer create/
update/promote succeeds, read as a fallback only when a deal-create names
no customer at all. An explicit name always wins over the remembered one;
the reply says explicitly when the fallback was used.

**"เพิ่มสินค้า" via chat did not work at all** — returned the full
permission-suggestion list, which looked like a permission problem but
was not one. `entity="product"` was in `chat.py`'s `ACTION_PERMISSIONS`
(gate was fine) but the AI intent prompt was never told product's field
shape, and there was no chat dispatch handler at all — Phase 7 only ever
built product CRUD through the internal API. Fixed both: added product's
field shape to the prompt, wired real execution via the already-existing
`ProductRepository.upsert`.


### 5. Phase 9 — CRM Core: Lead/Contact/Deal + Storefront (`8e4ee4c`, `dbbe063`)

Fully deployed and confirmed working live: create customer, promote to
Contact, create deal, deal stage transitions (new→proposed→won) all
tested successfully on real LINE traffic by the owner. Storefront search
works but returned no results in testing because no product existed yet
in any tenant — not a bug (see gap #2 above, now fixed).

**Has a real migration this time**: `0008_phase9_crm_core` (3 new tables:
`customers`, `deals`, `deal_products`). `EXPECTED_MIGRATION_HEAD` in
`data/chann_data/main.py` is bumped in the same patch — a guard test
(`test_expected_head_matches_the_newest_migration`) fails loudly if these
ever drift apart again.

### What this patch builds (Master Spec 9.1-9.7)

1. **Data tier** — `Customer` (stage: lead/contact), `Deal`
   (`deal_id` like `D-2026-0001`, globally unique — see the model's
   docstring for why this deliberately differs from quotes' per-tenant
   numbering), `DealProduct` (line items, `product_id` nullable for
   off-catalogue items). Repository: `CustomerRepository`, `DealRepository`
   (9.6's stage machine: `new→proposed→won/lost`, `won/lost→new` gated by
   `allow_reopen`), `StorefrontRepository` (cross-tenant product search +
   auto-lead). 14 new Data-tier endpoints.

2. **Chat wiring** — real execution replaces the Phase 6 stub
   (`_pending_execution_reply`) for `entity="customer"` and `entity="deal"`:
   - Create/update/promote a customer by **name** (never by an id the user
     would type) — ambiguous names ask to clarify, same pattern
     `registration.py`'s shop search already uses.
   - Create a deal against an existing customer (looked up by name).
   - Deal stage transitions are matched **directly against the message**,
     not sent through the AI parser — a deal code (`D-YYYY-NNNN`) is a
     stable, machine-parseable token and the stage vocabulary is a small
     closed set, so free-text understanding buys nothing and only risks a
     hallucinated stage. Two subtle bugs were caught and fixed by the
     integration tests before this ever reached a patch:
     - `"ไม่สำเร็จ"` (lost) contains `"สำเร็จ"` (won) as a literal Thai
       substring — checking won's keyword first misclassified every lost
       deal as won. Lost is now checked first.
     - `"เปิดดีล D-2026-0001 ใหม่"` splits the reopen phrase across the
       deal code. The code is now stripped from the message before keyword
       matching, not after.
   - **The AI intent prompt now describes customer/deal's real field
     shape** (`first_name`/`last_name`/`phone`/`email`/`address`/`notes`,
     `target_name` for update/promote/deal-create) — this was a known,
     previously-documented gap: without it the model invents plausible-
     looking field names that don't exist anywhere else to check against.

3. **Storefront (9.4)** — cross-tenant product search, wired into
   `webhook.py` **before** the `is_unregistered` check, not inside
   `handle_chat_message`. This matters: an unregistered Customer OA visitor
   never reaches `handle_chat_message` at all (the webhook routes them to
   `handle_registration` first), so storefront browsing has to be its own
   webhook-level branch to work for someone who has never linked to any
   shop yet — which is the primary path the spec describes (browse
   anonymously → pick a product → pick a shop → become a Lead there).
   Trigger phrase is literally specified in the spec: `"ค้นหา [keyword]"`.
   Selection state (the numbered result list) lives in the same
   `pending_intent` Redis mechanism Phase 6 built for slot-filling —
   `entity="storefront"` distinguishes it from an unrelated in-progress
   conversation on the same channel.

### What this patch deliberately does NOT touch

- Presentation-tier Dashboard CRUD for customers/deals (spec 9.2 lists it,
  but this project has stayed chat-first through every phase so far — no
  Presentation tier exists yet for anything).
- Quote generation (Phase 10) — `deals.stage="proposed"` is reachable by
  chat, but nothing produces an actual quote document yet.
- Any change to the identity-resolution fix already deployed in `ecf0724`.

### Files touched (14, all on top of `ecf0724`)

```
application/chann_app/data_client.py               ← 12 new Phase 9 methods
application/chann_app/line/webhook.py               ← storefront hook before is_unregistered
application/chann_app/services/ai/intent.py         ← customer/deal field-shape prompt block
application/chann_app/services/chat.py              ← customer/deal execution, deal-stage commands, storefront
data/chann_data/main.py                             ← EXPECTED_MIGRATION_HEAD bump
data/chann_data/models.py                           ← Customer, Deal, DealProduct
data/chann_data/repositories/phase9.py              ← new file: Customer/Deal/Storefront repositories
data/chann_data/routers/internal.py                 ← 14 new endpoints
data/chann_data/schemas.py                          ← Customer/Deal/DealProduct/Storefront schemas
database/Dockerfile                                 ← new file: migration runner image (see below)
database/alembic/versions/0008_phase9_crm_core.py   ← new migration
database/requirements.txt                           ← +pydantic/pydantic-settings (env.py needs them)
tests/integration/test_database_from_empty.py       ← +4 tests (spec 9.7's mandatory list)
tests/unit/test_phase6_chat.py                      ← +19 tests (customer/deal chat, storefront)
```

### How the migration actually runs against Cloud SQL — no IAM changes

The deploying account has no `roles/cloudsql.client` (confirmed via
`gcloud projects get-iam-policy ... --filter="bindings.role:roles/cloudsql.client"`
→ 0 items), and the owner has been explicit more than once: don't touch IAM,
don't suggest granting roles, don't ask for it either. `cloud-sql-proxy`
needs that role on the account running it, so it's the wrong tool here.

Instead, the migration runs as a **Cloud Run Job** — `database/Dockerfile`
builds a small image (alembic + `chann_data.models` for the metadata
`env.py` needs, from the repo root so `database/` and `data/chann_data`
sit as siblings the way `env.py`'s relative path expects) with `CMD ["sh",
"-c", "cd database && python -m alembic upgrade head"]`. Deployed with
`--set-cloudsql-instances`, exactly like the existing `data` Cloud Run
service already is — the job inherits the **default compute service
account** (neither Cloud Run resource in Terraform sets an explicit one),
which already proves out Cloud SQL connectivity today. No new IAM grant of
any kind. Deploying/executing a Cloud Run Job uses the same permission
surface as deploying a Cloud Run service, which this account already does
successfully.

Validated locally by reproducing the exact container filesystem layout in a
temp directory and running the exact `CMD` against real Postgres — caught
two real bugs before they'd have surfaced in Cloud Shell:
- Alembic resolves `script_location` relative to **CWD**, not the ini
  file's own path — `python -m alembic -c database/alembic.ini upgrade
  head` from `/srv` looked for `/srv/alembic`, not `/srv/database/alembic`.
  Fixed by `cd database` first, matching how this project's other scripts
  already invoke alembic.
- `database/requirements.txt` didn't include `pydantic`/`pydantic-settings`,
  which `env.py` needs transitively (`chann_data.db` → `chann_data.config`).

---

### 4. OA-aware identity resolution for Customer/Technician (`ecf0724`)

Found by the owner testing the OA-scoping fix (below) on real LINE traffic:
`resolve_context()` decided "which company does this message belong to"
using `memberships_of()`, which queried `license_members` (the STAFF table)
regardless of which OA the message arrived on. An account already
registered as Sales staff at Company X was treated as already "belonging to
Company X" the instant it messaged Customer OA or Technician OA too, with
no registration step at all.

Fixed: `memberships_of(chann_uid, oa=)` now filters by role
(`oa="technician"` → only `role=="technician"`; `oa="sales"`/omitted →
everyone except technician). Customer OA resolves via
`customer_license_links` instead of `license_members` entirely. A
`"technician"` role was added to `DEFAULT_ROLE_TEMPLATES`, and
`create_invite()` self-heals a missing default role on first use so
existing tenants don't need a migration. A new Sales-OA chat command,
"ขอรหัสเชิญช่าง" (requires `member.manage`), mints a one-time invite coded
`role="technician"`.

The following three fixes shipped in `5eacfd2` and are confirmed live via
`terraform apply` (3 changed, 0 destroyed). Kept here because the new patch
above builds directly on the `ctx.oa` concept they introduced.

### 1. Profile self-edit restricted to Technician/Customer OA (Spec 8.1)

Sales OA is deliberately excluded. That channel is where leads, deals and
quotes are discussed, so "แก้เบอร์เป็น 08x" becomes genuinely ambiguous
there once Phase 9 exists — whose number, the sender's or the customer they
were just discussing?

### 2. Conversation continuity (closes a real gap in Spec 6.4)

Spec 6.4 describes parsing one message in isolation, which quietly assumes
every message is self-contained. **The bot itself produces messages that are
not:** it asks "what is the phone number?", and the honest human answer is a
bare `0812345678` — no verb, no entity, unparseable alone.

Fixed with a short-TTL pending-intent record in Redis (Data tier, 3
endpoints, 600s). Deliberately Redis not Postgres: conversational scratch
state, not business data needing an audit trail. If Redis is down the safe
degrade is "ask fresh", never "assume the old answer" — the *opposite* of
ADR-006's permission-cache rule, but the same underlying principle: an
outage must never let stale state override what the user just said.

The merge (`_is_continuation` / `_merge_pending` in `chat.py`) is
conservative on purpose. A message naming a different entity, or asking
"what can I do", abandons the pending state instead of being folded into it.
Filing a genuinely new request into an unrelated half-built record is far
worse than re-asking.

### 3. OA channel scoping — the bug that drove the root-cause find

**Reported live:** an Owner account (holds all 51 permission keys) texting
through the **Technician or Customer OA** and typing "ทำอะไรได้บ้าง" saw
`อนุมัติ`, `จัดการการเรียกเก็บเงิน`, `จัดการบทบาทและสิทธิ์` — capabilities
Master Spec §6's OA activity tables place **exclusively under Sales OA**.

`suggest_what_you_can_do()` had no concept of "channel" at all. It read the
tenant's `permission_keys` and showed everything. Holding a tenant permission
and a channel actually offering it through chat are two different questions;
the gate only ever asked the first.

**Root cause runs deeper than the symptom.** LINE issues the **same
`userId`** to one physical account across every channel under one provider —
confirmed against LINE's own developer docs, and exactly how this project's
three OAs are set up (one company, one provider). Meanwhile
`chann_identities` is global by design (one row per LINE user) and
`primary_role` is **fixed at first contact**. So the moment that account
messages a *different* OA, `primary_role` is stale.

**Design decision (owner chose option A, 27 Aug 2026):** keep one identity
per person across all OAs; adjust behaviour per the current OA rather than
splitting identity by OA. Option B (unique constraint on
`(line_user_id, oa)`) was rejected — it would break `chann_uid`'s meaning as
a global handle.

Implementation: `ResolvedContext` gains an **`oa`** field — the current
message's actual channel, ground truth from the webhook. Every per-message
eligibility decision now reads `oa`, never `primary_role`:

| Decision | Now reads |
|---|---|
| Profile self-edit eligibility | `ctx.oa` |
| Pending-intent cache key | `(chann_uid, oa)` |
| Capability scope / suggest list | `ctx.oa` |
| Permission gate | `ctx.oa` + tenant permission |

`OA_ALLOWED_PERMISSION_KEYS` in `chat.py` transcribes the spec's OA activity
tables directly. `None` for Sales OA is deliberate: that table already covers
nearly everything a tenant does, so there's nothing left to narrow beyond the
existing tenant-permission gate.

**Proven end-to-end**, not just at unit level — same account, same 51 keys,
same message, three channels:

| Channel | Sees |
|---|---|
| Sales | all 22 groups, incl. approval + billing |
| Technician | `ticket.*` + `service_report.*` only |
| Customer | `customer.*` + `ticket.*` + `warranty.*` only |

### Files touched (8)

```
application/chann_app/services/chat.py       ← main: OA table, helpers, gate, continuity
application/chann_app/services/identity.py   ← ResolvedContext.oa
application/chann_app/services/ai/intent.py  ← PENDING_PROMPT_BLOCK
application/chann_app/data_client.py         ← 3 pending-intent methods
data/chann_data/cache.py                     ← k_pending_intent(chann_uid, oa)
data/chann_data/routers/internal.py          ← 3 endpoints
data/chann_data/schemas.py                   ← PendingIntentIn/Out
tests/unit/test_phase6_chat.py               ← +13 tests
```

---

## Known issues / open threads

- **CI/CD via Workload Identity Federation is blocked** — the deploying GCP
  account has no Owner/IAM-admin role. Manual build+push+deploy is the only
  path until someone with Owner sets it up.
- ~~Phase 9 must pass each entity's real field schema into the AI intent
  prompt~~ — **done** in the Phase 9 patch above (`ai/intent.py`'s prompt
  now describes customer/deal's real fields).
- ~~Phase 9 also inherits the ambiguity that fix #1 side-steps~~ — **done**:
  customer create/update/promote and deal-create all resolve a name to a
  record via `_find_one_customer_by_name`, asking to clarify on an
  ambiguous match rather than guessing. `may_edit_on_behalf` /
  `check_profile_edit` (the on-behalf profile path, distinct from this) are
  still not wired into chat — nothing in Phase 9 needed them.
- **Quote generation (Phase 10) is next in spec order** — `deals.stage`
  reaches `"proposed"` via chat already, but no document is produced yet.
- `view_reports` is intentionally one broad permission key — deferred to
  Phase 17 where reports actually get designed.
- **Rich Menu test (Phase 19) must avoid hardcoded role names** —
  Principle #10 requires permission-key checks only.
- Stage/prod are **parked** for cost (Cloud SQL stopped, Redis/VPC-connector
  deleted). Recreate steps in `docs/ENVIRONMENT_RESOURCE_MAP.yaml`.

---

## Patterns worth reusing (all proven in this codebase)

- **A hand-written `FakeDataClient` is not a substitute for testing the real
  HTTP client.** `_unwrap()`'s `204`-body bug went undetected through every
  Phase 6/9 patch because `FakeDataClient` (used throughout
  `tests/unit/test_phase6_chat.py`) never calls the real `DataClient` at
  all — it's a hand-written stand-in returning plain Python objects,
  bypassing httpx entirely. A small, separate test file
  (`tests/unit/test_data_client.py`) that exercises `DataClient` against a
  real `httpx.MockTransport` is what actually caught this. If a bug can
  only manifest in the wiring between two tiers, a fake that skips that
  wiring can never catch it, however thorough the tests built on top of it.
- **A real local Postgres catches things a mocked one can't.** Two earlier
  sessions validated Phase 8/6 patches with integration tests *skipped*
  (no `TEST_DATABASE_URL` available in the sandbox) and shipped clean. This
  session installed Postgres locally (`apt-get install postgresql-16`,
  works fine through `archive.ubuntu.com`/`security.ubuntu.com`) and ran
  the real integration suite — it caught 4 failures in the technician/
  customer-scoping patch immediately (two stale role-count assertions, one
  missing FK row in a test, one test that assumed a person can hold two
  roles at one license when `redeem_invite` doesn't allow that), and later
  caught two subtle Phase 9 deal-stage-parsing bugs no unit test with a
  `FakeDataClient` would ever have exercised realistically. If Postgres is
  available, use it — don't rely on the skip path just because it's
  historically been fine before.
- **`scripts/check-model-kwargs.py`** — statically checks every SQLAlchemy
  model constructor call against real mapped columns, no database needed.
  Catches "I assumed this column exists" before integration tests.
- **`git apply --3way`, not plain `git apply`.** Plain apply needs exact
  context and fails outright on trivial drift; `--3way` merges against the
  blobs already in the repo. Two separate sessions lost time to this.
- **`git apply --reverse --check` to detect "already applied"** — file
  existence is not a version check and has silently skipped a corrected
  patch at least twice here.
- **`git add -A` then verify `git status` is clean** before claiming a phase
  is committed. An explicit `git add <file-list>` dropped real code from
  three separate commits in this project.
- **Verify config with `grep -n <name>`, never `grep -c` against a negative
  pattern** — `grep -c` returns 0 both when a line has a value and when the
  line doesn't exist at all.
- **Anchor-based idempotent Python fixers** for files that drift between an
  AI's local copy and the deployed tree (`services/chat.py` and
  `tests/unit/test_phase6_chat.py` especially). Match exact code fragments,
  never line numbers; `ast.parse()` before writing; refuse loudly on
  mismatch rather than half-applying. Build anchors from a file the user
  pastes back from the live deployment, never from a local reconstruction.
- **`GODEBUG=netdns=go`** fixes Cloud Shell's intermittent IPv6 routing
  failures with `gcloud`/`terraform` (symptom: `cannot assign requested
  address`, and terraform failing a *different* data source each run).
  `sysctl` and `/etc/gai.conf` tweaks alone are not sufficient.

---

## For an AI picking this up cold

Read in order: this file → `CLAUDE.md` (§14.1 is AI-agent guardrails, every
rule came from a real incident here) → `CONTRIBUTING.md` → the phase section
of `docs/CHANN_CRM_AI_MASTER_SPEC.md` you're about to work on.

Before considering anything done: run `./scripts/phase2-source-verify.sh` —
it builds its own clean venv and is the only trustworthy verification here;
ad-hoc `pytest` against ambient packages has produced false failures. Never
claim a phase is deployed without hitting `/health` on the real Cloud Run
service and confirming `git_commit` matches what you just pushed. Never run
`terraform apply` unattended.
