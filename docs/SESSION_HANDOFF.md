# Session Handoff — 1 Sep 2026

Written because the conversations doing Phases 3-13 repeatedly hit their
context limits. This is what the next AI session needs to pick up
cleanly — read this before `docs/CHANN_CRM_AI_MASTER_SPEC.md`, not
instead of it.

Supersedes all earlier versions. The previous version froze at `c585b78`;
everything from there to `ee1a3ab` and beyond landed in one very long
run covering Phases 11, 12, 13 and 16, the customer-facing half of the
product, and a long chain of production bug reports from real use in
LINE.

Read "Where things stand" before touching anything. Several fixes in that
run directly undo assumptions an earlier fix in the SAME run made, and the
reasoning for why the LAST one is correct is not obvious without the
failed attempts in front of you.

**⚠️ A standing risk with this file, proven three times now.** A previous
session wrote a corrected version of this handoff and never committed
it — the repo kept the stale text while a newer copy circulated outside
git. Separately, `git apply` was pointed at the WRONG saved patch file
three times (an old one instead of the latest), each time appearing to
succeed before the mismatch was caught by checking `MIGRATION_HEAD` in
the verify output. And once, a stale HEAD was mistaken for a divergent
branch and nearly force-reset over. If a copy of this file, or of a
patch, disagrees with `git log` or `MIGRATION_HEAD`, trust git.

**⚠️ Deploys are manual and the order matters.** Migrations run as a
Cloud Run Job and MUST complete before the service images are deployed,
or `EXPECTED_MIGRATION_HEAD` will not match and `/health` reports a stale
schema. Cloud Shell resets `sysctl`, installed fonts and the active
project on every restart; IPv6 breaks `terraform apply` at random and
`GODEBUG=netdns=go` plus disabling IPv6 is the reliable fix, with
`gcloud run services update --image` as the fallback that has worked
every time.

---

## 🔴 Secret rotation — DEFERRED BY THE OWNER until the end of the project

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

A later commit removed them from the index. That fixes HEAD only — **it
does not remove them from history.**

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

`origin/main` HEAD is **`ee1a3ab` — warranties, serial routing, and quotes
that own their line items**, plus one patch applied on top and deployed
with it (see the commit log for the exact SHA once pushed).

Migration head is **`0019_quote_products`**.

Everything below was exercised in LINE and in the LIFF dashboard on DEV.
Where something is known NOT to work, it says so.

### The bug that ate most of a day, and what it teaches

Issuing a quote appeared to fail for hours. The eventual cause was
`ck_audit_log_action`: the constraint has allowed six verbs since Phase 3,
and every phase since has written verbs outside that list —
`link_document`, `upsert`, `status`, `remove_product`, `claim`, `reject`,
`check_in`, `check_out`.

An audit entry shares a transaction with the change it records. So the
constraint did not merely reject the audit row: it rolled back the PDF
render, the GCS upload and the `generated_documents` insert alongside it.
The file existed in the bucket, unreachable, while the salesperson was
told the quote had not been issued.

**The lesson is the ordering, not the list.** A constraint guarding a
write that shares a transaction with it destroys good work alongside bad,
silently, and surfaces something three layers from the cause. Migration
`0017` widened it; `chann_data/audit_actions.py` is now the single source
and a test scans the routers for any `action=` literal missing from it.

Two more failures had the same shape — correct upstream, broken at a
boundary, invisible in logs:

* **503 on viewing a document.** The Presentation proxy parses every
  response as JSON; a PDF is not JSON, `res.json()` threw, the catch
  turned it into 503. No error in the Application Tier (it succeeded), no
  503 in its access log (it never returned one). A boundary test now pins
  the proxy's pass-through list to the routes that return files.
* **"ไม่สามารถเปิดลิงก์ได้" when opening a PDF.** LINE's in-app browser
  refuses `blob:` URLs, and LIFF is the only place this dashboard runs.
  Documents now go through a signed link and `liff.openWindow`. A boundary
  test fails the build if any quote page builds a blob URL again.

When something works in isolation and fails in the product, suspect the
boundary before the logic, and read the layer that does not appear in the
logs.

### Phases 11, 12, 13 and 16 are on DEV

* **11 — assignment engine.** The AI writes a rule once, at configuration
  time; the runtime engine only reads it, and lives in the Data tier
  because the decision must happen inside the same transaction and lock
  that reads current loads and writes the assignment. The mandatory race
  test drives ten real concurrent transactions against a five-a-day cap.
  `มอบหมาย T-… ให้อัตโนมัติ` invokes it.
* **12 — tickets and the dispatch gate.** Refuses to dispatch until name,
  phone, address, date and time exist, and names which are missing.
  Visibility is enforced server-side (`visible_to`), never in the client.
* **13 — field service.** Check-out requires a report; the technician is
  asked one question at a time rather than handed a format. GPS is stored
  on the photo, not the ticket, because check-in and check-out happen in
  the same place hours apart.
* **16 — warranties and serial routing.** A serial identifies the
  product, the shop and the warranty at once. The cross-tenant lookup is
  the only query here that deliberately leaves a tenant: it returns
  nothing but what identifies a shop, matches exactly (never by prefix),
  and is audited with `cross_tenant=true` whether or not it finds
  anything.

### The customer flow finally exists

Phase 12.4 starts with a customer reporting a fault, and that entry point
was never built — `create_ticket` had no caller, so every capability in
Phases 12 and 13 operated on rows nothing could create. A customer now
reports in one message with only a description; the address and the
appointment are asked for afterwards, which is also where the dispatch
gate gets what it needs. They can move or cancel the appointment, and the
assigned technician is told when they do.

### Chat no longer depends on the AI for its own buttons

Quick replies are text the system writes itself, in a shape it chose.
Routing that through a model failed in production and spent a call per
tap. Deterministic now: creating deals and quotes, adding, editing and
removing line items, check-in, check-out, claiming a ticket.

**But deterministic paths must yield, not refuse.** Twice a
short-circuit was added that answered "please name a customer" when
context was absent — removing the AI's ability to read a name out of a
longer sentence. Both times existing tests caught it. If a deterministic
path cannot answer, fall through.

### Substring collisions keep happening

Five so far, every one caught by tests: `ไม่สำเร็จ`/`สำเร็จ`,
`ออกเอกสารใหม่`/`ออกเอกสาร`, `ตั้งกฎมอบหมาย`/`มอบหมาย`,
`เปิดดีล`/`เปิดดีลใหม่ (reopen)`, and `เพิ่มสินค้า` meaning both "line
item" and "catalogue entry". Check every new trigger against the existing
list before adding it.

### Owner-approved departures from the Master Spec

* `deal_id` is per-tenant, not globally unique (recorded earlier).
* **`new → lost` is allowed.** Spec 9.6 lists no such transition, which
  made a deal that dies before anyone quotes it impossible to close — the
  most ordinary way for one to end. The alternatives were leaving it open
  forever or inventing a quote that was never made; both corrupt the
  pipeline numbers the stage exists to produce. `new → won` is still
  refused.
* **Quotes own their line items** (`quote_products`, migration `0019`).
  A quote used to be a pointer at a deal, so two quotes on one deal were
  necessarily identical and editing a deal rewrote drafts already under
  discussion. Copied at creation, independent after. Issued quotes cannot
  be edited at all.

### Context, and why the TTL matters

`last_customer_ref` and `last_entity_ref` are what let someone say
"สร้างดีล" without naming anyone. Both were 600 seconds, which was long
enough for a demo and too short for work: a salesperson confirmed a
customer at 10:21, took a call, asked for a deal at 10:38, and was shown a
list of permissions. Both are an hour now. Viewing a customer writes BOTH
refs — it used to write only the generic one, which is why the fallback
never fired.

---

## Immediate next actions (in order)

1. **Rotate the LINE channel secret exposed in a chat transcript.** One
   Sales channel secret was printed in full while debugging environment
   variables. Rotate it in the LINE Developers console and update
   `terraform.tfvars`. This is separate from, and more urgent than, the
   deferred rotation below.

2. **Nothing displays a Service Report's PDF.** The report is written,
   stored and now readable in the dashboard, and `attach_document` exists
   to link a rendered file — but nothing renders one. Wire it to
   SmartBrowz the same way quotes are, using
   `document_type='service_report'` on the generic template tables
   (Master Spec 13.3 requires that, not a second template model).

3. **Phase 14 — approval workflow and satisfaction survey.** Service
   reports already carry `submitted`/`approved`/`rejected` and the
   dashboard can move between them; Phase 14 is the rest of it.

4. **Phase 15, 17, 17.5, 18** remain. Phase 16 was pulled forward and is
   done. Phase 16.5 (PDPA) is untouched.

5. **CI auth method is still undecided** — WIF vs SA key vs Cloud Build
   trigger. Every deploy so far has been manual from Cloud Shell.


## Already deployed, section by section (oldest first)

Each heading below records what one patch in the `abd8f93..c585b78` run
added, preserved as it was written at the time rather than compressed —
the "why" for several small decisions only makes sense in the context of
what had just gone wrong. Skip to "Where things stand" above for the
current state; come back here for the reasoning behind a specific piece.

### `2cc6fb8` — the real SmartBrowz render adapter, wired through PdfRenderer

Deployed on top of `f13154f`. No migration. (This section was titled
"Uncommitted work waiting to be deployed" for a while after it had
already shipped — see the warning at the top of this file about the
correction never being committed.)

### The actual render adapter now exists — properly wired through the seam Phase 1 already built

Wanted to first verify SmartBrowz connectivity works before building
anything template-related. Along the way, checking exactly HOW to call
SmartBrowz turned into real research (Zoho does not publicly document a
raw REST endpoint for the PDF & Screenshot component — only SDK usage),
and writing the code turned up a real gap: I initially put the
`zcatalyst_sdk` import directly in a new standalone module, which an
*existing* boundary test caught immediately —
`tests/boundary/test_tier_boundaries.py::test_domain_code_does_not_import_a_pdf_vendor_sdk`,
which enforces ADR-021's own stated intent: "domain code depends on the
PdfRenderer protocol, never a vendor SDK directly." Phase 1 had already
scaffolded exactly this seam (`application/chann_app/services/pdf/base.py`
— `PdfRenderer` Protocol, `PdfOptions`/`PdfResult` dataclasses,
`NullPdfRenderer`, a `get_renderer(name)` factory that already had a
`"smartbrowz"` branch stubbed as `NotImplementedError`) — it was just
never implemented until now.

**Fixed properly, not by loosening the test:**
`application/chann_app/services/pdf/smartbrowz.py` is the one new file
in the Application tier allowed to import `zcatalyst_sdk` — the test now
has one narrow, explicit exception for exactly this path (the adapter,
by definition, has to import the vendor SDK somewhere, or the
abstraction could never be implemented at all; the boundary the test
protects is that nothing *else* depends on it). `get_renderer("smartbrowz")`
in `base.py` now returns a real `SmartBrowzPdfRenderer` instance via a
local import (never touches `zcatalyst_sdk` itself, so `base.py` stays
outside the boundary check).

### How the adapter actually talks to SmartBrowz

Uses the official `zcatalyst-sdk` PyPI package (`zcatalyst-sdk==1.4.0`,
added to `application/requirements.txt`), initialized in Zoho's own
documented "third-party application" mode — confirmed via their
"Catalyst Python SDK Integration in Third-Party Applications" doc, the
correct path for exactly this scenario (an app deployed outside
Catalyst, authenticated via a Self Client's OAuth credentials) rather
than guessing at an undocumented REST endpoint.

**A real, non-obvious requirement found along the way:** the SDK's
`ICatalystOptions` needs a **ZAID** (Zoho Account ID) in addition to
`client_id`/`client_secret`/`refresh_token`/`project_id` — confirmed
mandatory by inspecting `zcatalyst_sdk.types.ICatalystOptions.__required_keys__`
directly, not assumed from docs alone. First attempt at finding it went
down the wrong path (one specific third-party-integration doc example
happened to use Catalyst's own Authentication component to demonstrate
retrieving a ZAID, which looked like a real prerequisite — it is not).
The correct, much simpler location: Catalyst console -> Project Settings
-> Environments -> General, which directly lists ZAID/API Key/Application
URL per environment (confirmed against Zoho's own "Environment Settings"
help page). New config: `CATALYST_ZAID` (alongside the pre-existing
`catalyst_project_id`/`catalyst_environment`/`catalyst_api_domain`
placeholder fields — which, it turns out, had never been wired into
Terraform either, the exact same gap the previous patch fixed for the
`SMARTBROWZ_*` variables; fixed now, all four `catalyst_*` fields wired
into `application_runtime_env` in the same patch).

**Deliberately does NOT use `smartbrowz_auth.py`'s `SmartBrowzTokenManager`**
(the Data-tier-cached token manager built one patch ago, before this
adapter existed) — `zcatalyst-sdk`'s own `RefreshTokenCredential` already
refreshes and caches an access token internally per-process (confirmed by
reading its source: `self._cached_token`, checked against `time()` before
refreshing). Duplicating that against the Data-tier cache as well would
just be two caches disagreeing with each other for no real benefit at
this project's scale — a handful of Cloud Run instances each refreshing
independently at most once per ~55 minutes is nowhere near Zoho's
documented rate limit (10 access tokens per refresh_token per 10
minutes). `SmartBrowzTokenManager` is kept as-is, unused by this adapter,
in case a future scale-up ever makes the shared-cache benefit worth the
added complexity.

### What this patch builds

1. **`application/chann_app/services/pdf/smartbrowz.py`** —
   `SmartBrowzPdfRenderer` implementing the `PdfRenderer` protocol
   (`render()`/`preview_image()`, both wrapping the SDK's sync
   `convert_to_pdf`/`take_screenshot` calls in `asyncio.to_thread` so
   they never block the event loop), plus `verify_connection()` — a
   standalone diagnostic call (fixed trivial HTML, never returns PDF
   bytes) for 10.6's own requirement to verify the real auth path from
   the deployed environment before building anything on top of it.
2. **New endpoint** `POST /api/v1/platform/smartbrowz/verify-connection`
   (`routers_admin.py`) — behind the existing `require_admin` platform-
   admin JWT dependency (not a new, separate auth scheme; every call
   spends a real SmartBrowz API request against the project's quota, so
   it should not be an unauthenticated route). Returns 503 for
   `SmartBrowzNotConfigured`, 502 for `SmartBrowzRenderError` — the two
   look identical from outside otherwise and need completely different
   fixes.
3. **Terraform**: `variable "catalyst_project_id"`, `variable "catalyst_zaid"`,
   `variable "catalyst_api_domain"`, `variable "catalyst_environment"`
   added to `variables.tf` and wired into `application_runtime_env` in
   `cloud_run.tf` (the same gap-fixing pattern as the previous patch, just
   for the four `catalyst_*` fields that turned out to be missing too).
4. **`requirements-test.txt` gap found and fixed separately, live, before
   this patch**: the owner's Cloud Shell had `pytest==9.1.1` installed
   globally (from something else entirely) with no `pytest-asyncio` at
   all — every async test in this project failed to collect. This
   project's own `requirements-test.txt` (`pytest==8.3.4`,
   `pytest-asyncio==0.25.2`) was never being installed by any deploy
   script's STAGE 2 — fixed directly in
   `phase10-smartbrowz-tfwiring-deploy.sh` (already deployed) and
   confirmed by reproducing the exact broken environment locally
   (forced `pytest==9.1.1`, removed `pytest-asyncio`) and watching the
   exact same collection error, then confirming the fix resolves it.

### New tests

`tests/unit/test_smartbrowz_pdf_renderer.py` — 5 tests, using **real
network calls to Zoho with intentionally-fake credentials** rather than
mocking `zcatalyst-sdk`'s internal HTTP client (it uses `requests`, not
this project's own httpx-based `DataClient`, so the clean
`httpx.MockTransport` injection pattern `test_smartbrowz_auth.py`/
`test_data_client.py` use isn't available here without patching library
internals — a real round trip with fake credentials is fast, since Zoho
rejects immediately, and proves the actual error-handling paths this
module depends on). Covers: missing config raises before any network
call; fake credentials are cleanly rejected by the real endpoint; preview
also requires config; `verify_connection()` surfaces the same typed
errors the endpoint translates to 503/502; `get_renderer("smartbrowz")`
returns the right adapter type.

### What still needs to happen before a real PDF can be generated

1. This patch deploys.
2. The owner adds the real secret values (`smartbrowz_client_id`,
   `smartbrowz_client_secret`, `smartbrowz_refresh_token`,
   `catalyst_project_id`, `catalyst_zaid`) to their own gitignored
   `terraform.tfvars` directly — never through chat, never committed.
   `smartbrowz_accounts_url`/`catalyst_api_domain`/`catalyst_environment`
   can all stay at their defaults (already confirmed correct: US
   datacenter, Development environment).
3. `terraform plan`/`apply` to push those real values to the live
   Application-tier service.
4. Log in via the existing `/api/v1/platform/login` (username/password —
   see "no platform admin account exists yet" below) to get a bearer
   token, then call `POST /api/v1/platform/smartbrowz/verify-connection`
   with it — this is the actual 10.6 verification step, not yet
   performed against real credentials.
5. Only after that does building the actual quote-to-PDF pipeline (data
   snapshot builder, a real HTML template, `generated_documents`
   recording) make sense — still not started.

### No platform admin account exists yet in this deployment

Discovered while preparing to test the new endpoint (it requires
`require_admin`, the same JWT session flow every other admin endpoint in
this tier already uses). There is deliberately no public API to create
one (would be a privilege-escalation surface). This project already has
exactly the right tool for this, `database/scripts/seed_reference.py`
(idempotent by username, reads `PLATFORM_ADMIN_BOOTSTRAP_PASSWORD` or
falls back to a DEV-only default with a loud warning) — it was simply
never run against the live database. It's already baked into the
existing, already-deployed migration Cloud Run Job image
(`database/Dockerfile` copies the whole `database/` directory, seed
script included), so running it needs no new image build — just a
one-off execution with the command overridden:

```
gcloud run jobs execute chann-crm-ai-dev-migrate \
  --project=chann1-1 --region=asia-southeast1 \
  --command=python3 --args=database/scripts/seed_reference.py \
  --update-env-vars=APP_ENV=dev \
  --wait
```

(Optionally add `,PLATFORM_ADMIN_BOOTSTRAP_PASSWORD=<something>` to the
`--update-env-vars` value instead of accepting the DEV fallback
password — either is fine for a DEV environment per the script's own
documented reasoning.)

### Validated

On top of `f13154f` (the real live HEAD) on a clean clone: applies
cleanly (3-way), **310 tests pass**, 0 skipped (real Postgres, real
network calls to Zoho with fake credentials), `check-model-kwargs.py`
OK, both tiers boot (data 98 routes, app 22 paths). No migration.

---

### `d3f908d` through `e2830e7` — Phase 10 completion: company identity, quotes, PDFs, storage, CRM UI, dashboard

Seventeen commits condensed to what matters if you didn't read them as
they landed:

- **Company identity fields** (`d3f908d`, migration `0010`): `legal_name`,
  `tax_id`, `company_address`, `company_phone`, `company_email`,
  `vat_rate` added to `licenses`, all nullable (existing tenants can't be
  backfilled with data never collected). `is_document_ready()` /
  `missing_for_documents()` in application logic decide "can we render a
  document", not a `NOT NULL` constraint. `vat_rate = NULL` means "not
  VAT-registered" (no VAT line on the document at all) — a different
  state from `0` (registered at 0%).
- **Company profile via chat + dashboard** (`52195d4`): both surfaces
  hit the same `setting.manage`-gated endpoint. Chat commands are
  deterministic, never AI-routed — these values print on a legal
  document, so a model "correcting" a tax ID is worse than a rejected
  command. Multi-field messages (comma or newline separated) write
  atomically: one bad field refuses the whole message rather than
  half-applying it.
- **Quote-to-PDF pipeline** (`4ead818`): `services/documents/snapshot.py`
  owns every money/tax number (Decimal, `ROUND_HALF_UP` not Python's
  default half-even, which disagrees with a customer's own calculator on
  exactly the `.005` cases) and freezes the VAT rate into the snapshot so
  a later rate change can't alter how an already-issued document
  reproduces. `services/documents/html.py` turns a frozen snapshot into
  HTML with zero arithmetic of its own. Storage sits behind a
  `DocumentStore` seam mirroring `PdfRenderer`; uploads are create-only
  (`if_generation_match=0`) so bytes an audit row calls evidence can
  never be silently replaced; store-then-record order means a failure
  leaves a findable orphan object rather than a database row pointing at
  nothing.
- **CRM reads + dashboard actions + quick replies + rich menu**
  (`65bf9d1`): `ACTION_PERMISSIONS` had registered
  `("read","customer")`/`("read","deal")` since Phase 9 with no handler
  behind either — a list request passed the permission gate and fell
  through to nothing. `scripts/setup-richmenu.py` generates its own PNG
  rather than shipping a committed one, and hard-refuses if no
  Thai-capable font is installed: a rich menu without Thai glyphs
  uploads fine, passes every LINE API check, and shows the user a grid
  of empty boxes — caught by *looking at the rendered image*, not by
  trusting that the code ran.
- **Visual redesign + per-row Flex actions** (`e7531b7`): a list reply's
  single "view details" quick reply used to point at the first row
  always — replaced with a Flex bubble where each row carries its own
  button. A first attempt at a per-row colour rail (nested spacer box,
  then a CSS gradient) pushed ten rows of Thai text past LINE's 10KB
  Flex bubble limit, which silently fails the whole send; colouring
  existing subtitle text instead cost nothing and reads the same.
- **Real customer/deal codes** (`e2830e7`, migrations `0011`+`0012`) —
  see "Where things stand" above for the ADR-level detail; this is the
  commit that did it.

## Already deployed (27 Aug 2026) — for context, not action

### 12. Terraform SmartBrowz variable wiring (`f13154f`)

Confirmed deployed (`terraform apply`: 3 changed, 0 destroyed).
`grep`-ing the Terraform config for `smartbrowz`/`SMARTBROWZ` had turned
up nothing at all — no `variable` block to receive `SMARTBROWZ_CLIENT_ID`
etc. from `terraform.tfvars`, no line in `application_runtime_env`
passing any of them to the Application-tier Cloud Run service. The
token-refresh code from the previous patch would have deployed fine and
only failed the moment something actually tried to use it. Fixed:
`variable "smartbrowz_accounts_url"` (default `https://accounts.zoho.com`),
`variable "smartbrowz_client_id"`, `variable "smartbrowz_client_secret"`
(`sensitive = true`), `variable "smartbrowz_refresh_token"`
(`sensitive = true`) added and wired into `application_runtime_env`.
Also caught and fixed live during this same session, separately: the
owner's Cloud Shell had `pytest==9.1.1` with no `pytest-asyncio` at all
(this project's `requirements-test.txt` was never installed by any
deploy script) — every async test failed to collect; fixed in the same
deploy script, reproduced the exact broken environment locally to
confirm.

### 11. SmartBrowz OAuth token-refresh mechanism + customer disambiguation (`7a6255c`)

#### SmartBrowz OAuth access-token management, built ahead of real credentials

Built before the owner had generated real credentials — the piece that
could be built and fully tested first: automatic access-token refresh,
so whenever the real SmartBrowz render adapter (10.4-10.6, still not
built) eventually needs a bearer token, it never has to think about
expiry itself. (The owner has since generated real credentials — see
the Terraform-wiring item above for what that surfaced.)

**Architecture note, checked before building:** the Application tier has
no direct Redis access — `REDIS_URL` is only wired into the Data tier's
Cloud Run environment (`infrastructure/terraform/cloud_run.tf`'s
`application_runtime_env` doesn't include it). Caching the access token
in Application-tier process memory would mean every Cloud Run instance
refreshing independently on its own cold start, wasting calls against
Zoho's documented refresh-rate limit (10 access tokens per refresh_token
per 10 minutes). So the cache lives in the Data tier instead — a new,
deliberately **global** (not per-tenant) Redis key,
`k_smartbrowz_token()`, since this is one shared Catalyst project
credential serving every tenant, not something each company brings its
own copy of.

`application/chann_app/services/smartbrowz_auth.py`'s
`SmartBrowzTokenManager`:
- `get_access_token()` — returns the cached token if the Data tier still
  has one, otherwise refreshes through Zoho's `/oauth/v2/token` endpoint
  and caches the result (with a shorter TTL than the token's real
  expiry — a safety margin, `REFRESH_SKEW_S = 120`, so an in-flight
  request never races a token about to die).
- `get_api_domain()` — the datacenter-specific API host Zoho returns
  alongside the token (e.g. `https://www.zohoapis.com`); the eventual
  SmartBrowz REST calls need to go to this domain, not a hardcoded one.
- `invalidate()` — for a caller that gets a 401 despite a cached token
  looking unexpired (clock skew, a token revoked out-of-band in the
  console).
- Missing credentials, a non-200 from Zoho, or Zoho's documented quirk
  where an *expired/revoked* refresh_token can come back as **HTTP 200
  with an `error` field in the body** instead of a proper 4xx — all
  raise `SmartBrowzAuthError` with a clear message, never silently
  produce a token-shaped string that isn't one. This matters directly for
  10.6: "provider outage must return a clear render failure and must
  never cause AI to fabricate a document" — the same principle applies
  one layer down, at the token itself.

**On scope, honestly:** Zoho's own docs describe the pattern
`ZohoCatalyst.<module>.<operation>` (confirmed working for other Catalyst
modules, e.g. `ZohoCatalyst.tables.rows.CREATE`) but say the exact scope
names available for a given module — including SmartBrowz — are shown in
the **Catalyst API Console's own scope picker** when generating a Self
Client grant token, not published as a fixed list anywhere public. Not
guessed at or hardcoded anywhere in this code: scope is fixed to whatever
the refresh_token was originally granted for and is never re-sent on
refresh, so nothing here needs to know the literal scope string. When
generating the grant token, pick whatever the console shows for
"generate PDF/screenshot" and "manage templates" — the two capabilities
Phase 10's eventual render adapter will need — and put the resulting
refresh_token in `SMARTBROWZ_REFRESH_TOKEN`.

New config (all `REQUIRED_BY_PHASE_10`, all still unset):
`SMARTBROWZ_ACCOUNTS_URL` (datacenter-specific — must match wherever the
Catalyst project actually lives, e.g. `accounts.zoho.com` vs
`accounts.zoho.eu`; the wrong one rejects the refresh_token outright),
`SMARTBROWZ_CLIENT_ID`, `SMARTBROWZ_CLIENT_SECRET`,
`SMARTBROWZ_REFRESH_TOKEN`.

**What this patch does NOT do:** call any actual SmartBrowz PDF-generation
endpoint. That adapter is separate work, still blocked on the same thing
Phase 10's quote-CRUD patch already flagged — deferred so token-refresh
correctness and the eventual render adapter aren't both being gotten
right at the same time.

New test file `tests/unit/test_smartbrowz_auth.py` — 8 tests against a
real `httpx.MockTransport` for both the Data-tier cache calls and the
simulated Zoho endpoint (matching `test_data_client.py`'s own reasoning:
a hand-written fake would bypass the exact HTTP plumbing being tested).
Covers: refresh-when-nothing-cached, reuse-cached-without-re-calling-
Zoho, `invalidate()` forces a fresh refresh, missing credentials raise
clearly, a non-200 from Zoho raises clearly, the HTTP-200-with-error-body
quirk raises clearly, and the cached TTL is always shorter than the real
expiry.

#### Customer-name disambiguation now offers a numbered selection

Requested directly: if "สร้างดีลให้สมชาย" matches several customers named
สมชาย, the reply used to just list them as text and ask the user to "be
more specific" — no way to simply pick one. Fixed: an ambiguous match now
stores a `pending_intent` (`entity="customer_disambiguation"`) carrying
the *original* action, fields, and up to 9 candidates, and shows a
numbered list. A bare number reply is matched deterministically (same
reasoning as the deal-stage-command and technician-invite triggers — a
lone digit carries no AI-parseable meaning of its own, so it's handled
before the AI parser ever runs) and completes whichever original action
was pending — updating or promoting a customer, or creating a deal that
named one.

`_apply_customer_action` and `_apply_deal_create` factor the actual
"do the work" logic out of `_handle_customer_intent`/`_handle_deal_intent`
so both the normal single-match path and the resumed-after-disambiguation
path share the same code — the disambiguation resume was written to call
directly into the already-resolved row, never repeating the name lookup
against a customer that's already been chosen.

7 new tests: the list actually shows and sets pending state; picking a
number completes an update, a promote, and a deal-create; an
out-of-range number asks again without completing anything or clearing
the pending state; resuming without the right permission is refused; a
non-numeric reply with disambiguation pending still falls through to the
normal AI-parsed flow rather than getting stuck demanding a number.

### 10. Phase 10 — Quote CRUD + document template engine schema (`6eb29f4`)

Confirmed deployed and tested by the owner on real LINE traffic:
"สร้างใบเสนอราคาจากดีล D-2026-XXXX" creates a real quote (`Q-2026-NNNN`,
per-tenant), and the deal-stage-command collision found while building
this ("เสนอราคา" the stage-transition keyword vs. "ใบเสนอราคา" the quote
noun — see below for the full story) was confirmed fixed: the genuine
stage-transition phrase still works, and quote creation no longer gets
silently misrouted into it.

Had a real migration (`0009_phase10_quotes_templates`) — the first new
one since Phase 9's.

### Scope decision, made explicitly rather than silently

Phase 10 in the master spec is really two very different pieces of work:

1. **Quote CRUD** — a quote record tied to a deal, with its own status
   lifecycle. Fully buildable and testable right now with what already
   exists in this project.
2. **DOCX-upload → AI-assisted field mapping → compiled HTML template →
   SmartBrowz PDF rendering** (spec 10.4-10.6) — needs real Zoho Catalyst
   SmartBrowz credentials to build against and validate meaningfully.
   `docs/RUNTIME_CONFIG_CONTRACT.md` still lists every `SMARTBROWZ_*`
   variable as `REQUIRED_BY_PHASE_10`, not yet configured anywhere. Writing
   an adapter against a real external API with no way to call it and see
   what comes back would be exactly the kind of untested code this
   project's own validation discipline exists to prevent.

**This patch only builds #1.** The document-template *schema* (all 4
tables spec 10.3 asks for — `quotes`, `document_templates`,
`document_template_versions`, `generated_documents`) is built now, since
future phases (Warranty, Service Report, PDPA Export, Invoice) all reuse
this same generic engine and a second migration later would be wasteful —
but the actual DOCX/AI/SmartBrowz pipeline behind it is not. Concretely,
what a template version's `intermediate_model`/`mapping_schema`/
`compiled_template_path` columns actually contain is not decided by this
patch; only that a draft version can be created, previewed, published
(immutably), and superseded by a new draft, whatever fills those JSONB
columns once the pipeline exists.

### What this patch builds

1. **Data tier** (`data/chann_data/repositories/phase10.py`):
   - `QuoteRepository` — `create()` (validates the deal exists in-tenant,
     generates a per-tenant `quote_id` like `Q-2026-0001` — see
     `Quote`'s docstring in `models.py` for why this is per-tenant, unlike
     `Deal.deal_id`), `get`/`list_for_license`, `transition_status`
     (`draft → sent → accepted/rejected/expired`, no reopen concept —
     spec gives quotes no reopen path the way 9.6 gives deals one).
   - `DocumentTemplateRepository` — template CRUD, and the version
     workflow spec 10.4 describes: `create_draft_version` (version
     auto-increments per template), `mark_previewed` ("preview does not
     publish" — its own distinct state), `publish_version` (explicit
     approval; refuses on an already-published/archived version — the
     *only* way this row can ever exist a second time is a brand new N+1
     draft, never an in-place edit), `archive_version`.
   - `GeneratedDocumentRepository` — records a render's audit trail
     (template version + data snapshot + SHA-256). Does not perform a
     render — see scope decision above.
2. **14 Data-tier endpoints**, `DataClient` methods for all of them.
3. **Chat**: "สร้างใบเสนอราคาจากดีล D-2026-0001" creates a quote from an
   existing deal (looked up by its `deal_id` code, same pattern as deal
   stage transitions). `entity="quote"` already existed in
   `ACTION_PERMISSIONS` since Phase 6/7 scaffolding; this patch adds its
   AI-prompt field shape and a real dispatch handler.

### A real bug found and fixed while building this

Wiring quote creation into chat surfaced a genuine collision: `เสนอราคา`
("propose a price" — the deal-stage keyword for moving a deal to
"proposed") is also the literal root of `ใบเสนอราคา` ("a quote" — this
entity's own noun). "สร้างใบเสนอราคาจากดีล D-2026-0001" contains BOTH a
valid deal code and that substring, and was being silently intercepted by
the deal-stage-command matcher as "move this deal to proposed" instead of
ever reaching quote creation — no error, just silently the wrong thing.
Fixed by excluding the "proposed" keyword match specifically when
`ใบเสนอราคา` (the noun) appears anywhere in the message; the deal-stage
command still works correctly for genuine stage-transition phrases like
"ดีล D-2026-0001 เสนอราคาแล้ว". Two regression tests lock this down: one
proving the collision is gone (holding *both* `quote.create` and
`deal.update` permissions, to prove which handler actually ran), one
proving the real stage-transition phrase still works.

### Validated

On a clean clone: applies cleanly (3-way), migration runs empty-to-head
successfully, **290 tests pass**, 0 skipped (real Postgres),
`check-model-kwargs.py` OK, both tiers boot (data 95 routes, app 21
paths). Covers the subset of spec 10.7's mandatory tests that don't need
SmartBrowz: `test_quote_create` (increments per tenant, illegal-status-
transition refused), `test_template_versioning` (preview ≠ publish,
publish requires explicit approval, published version immutable, editing
creates N+1, old generated document still references the old version
after a new one exists), `test_multi_tenant_quote` (quote/template
isolation, cross-tenant deal reference refused cleanly).

### What's still needed before Phase 10 is actually complete

- DOCX upload + parsing + GCS storage
- AI-assisted field/mapping/layout proposal (spec 10.5's Intermediate
  Template Model)
- HTML compilation from the intermediate model
- The SmartBrowz adapter itself (`html_convert` mode per
  `docs/SMARTBROWZ_DOCUMENT_ENGINE.md`) — needs the owner to provision
  `SMARTBROWZ_CATALYST_PROJECT_ID`/`SMARTBROWZ_CATALYST_ORG_ID`/whatever
  auth path Catalyst requires, then verify it's actually reachable from
  the deployed Cloud Run environment (10.6 explicitly requires this
  verification before claiming readiness — GCP's own egress/auth quirks
  are exactly the kind of thing that looks fine in isolation and then
  doesn't work from Cloud Run)
- `GCS_BUCKET_NAME` provisioning for original DOCX / compiled template /
  generated PDF storage
- The Presentation-tier upload/mapping-review/preview/publish UI (10.2) —
  this project has stayed chat-first through every phase so far, and
  quote creation via chat covers the "generate a quote" runtime path, but
  *authoring* a template by chat alone is a much worse fit than a real
  upload+review UI

### 9. Missing-field label translation + storefront confirm-before-search UX (`5becf26`)

Two UX gaps, confirmed deployed and tested by the owner. Missing-field
prompts (e.g. asking for a customer's last name/phone) used to leak raw
English field-name keys verbatim into Thai sentences — fixed with a
`MISSING_FIELD_LABELS` translation table covering every field name the
slot-filling mechanism can currently ask about. Separately, a bare product
name in Customer OA (e.g. "พัดลม", no "ค้นหา" prefix) used to fall through
to the registration flow's shop-name search instead of finding anything —
fixed by trying a silent storefront search first, but (per the owner's own
framing: a bare word is genuinely ambiguous — buy one, or ask about a
repair ticket already filed for one?) asking for confirmation before
showing results rather than assuming intent. An explicit "ค้นหา [term]" is
already unambiguous and still skips straight to results.


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

* **A LINE channel secret was printed in full in a chat transcript** while
  inspecting Cloud Run environment variables. Rotate it. The `sed` filter
  used at the time only masked `sk-`-style keys and did not cover it.
* **Service Reports have no PDF.** Everything up to `attach_document`
  exists; nothing renders the file.
* **`Q-2026-0001` and any other quote issued before migration `0017`**
  has no `generated_document_id`: the audit constraint rolled the link
  back. Those quotes will always read "not issued". Re-issue them or
  leave them; nothing is corrupt, the document simply is not linked.
* **No test runner on the Presentation tier.** Its logic is pinned by
  boundary tests in `tests/boundary/` that read the TypeScript as text —
  crude, but they have caught two real regressions.
* **Rich menus are configured per OA by a script**, not by Terraform, so
  they must be re-run after any change to the tile sets. They need
  `fonts-thai-tlwg`, which Cloud Shell drops on restart; the script
  refuses rather than rendering boxes.
* **Phase 16 was pulled forward** ahead of 14 and 15 at the owner's
  request. 16.5 (PDPA) is untouched.
* **CI/CD is still manual.** WIF vs SA key vs Cloud Build undecided.

## Patterns worth reusing (all proven in this codebase)

### Suspect the boundary before the logic

Three of this run's worst bugs were correct code failing at a seam, and
all three were invisible in the obvious place:

* an audit constraint rolling back the work it recorded,
* a proxy parsing a PDF as JSON,
* LINE refusing a `blob:` URL.

In each case the tier everyone was reading had succeeded. When something
works in isolation and fails in the product, read the layer that does NOT
appear in the logs.

### A silent success is worse than a crash

"Status: sent" with no document, "sent: 1" with no LINE message,
`tenants: 0` from a sweep that should have found several. Each looked
fine and was not. Where a step can half-succeed, assert the visible
consequence, not the return value.

### Deterministic paths must yield, not refuse

A hand-written trigger that cannot answer should fall through to the AI,
not reject. Refusing removed the model's ability to read a name out of a
longer sentence — twice, both caught by tests that already existed.

### Keep the fake honest

Every time `FakeDataClient` diverged from the real client, a bug hid
behind it: a missing `get_profile`, a `create_quote` that skipped copying
lines, a team with no `id`. When a fake needs a new method to make a test
pass, check what the real one returns rather than inventing a shape.

### Ordering columns need a tiebreak that is not a timestamp

Postgres `now()` is fixed for a whole transaction, so rows inserted in one
message share `created_at` exactly and sort arbitrarily. Both
`deal_products` and `quote_products` carry an explicit `position`.


- **When a browser/LIFF flow misbehaves, read the access log before
  guessing.** `gcloud logging read` against
  `httpRequest.requestUrl` on the Cloud Run revision shows the exact URL
  and query string of every request a webview makes. This answered in
  one command what four rounds of reading LIFF documentation and
  patching blind could not — see "The LIFF dashboard" above for the full
  story. Reach for this the first time a redirect loop, blank page, or
  auth flow looks wrong, not the fourth.
- **A fake client that doesn't match the real schema hides real bugs,
  even when you wrote the fake yourself with this exact warning in
  mind.** `FakeDataClient.create_customer` was given an invented
  `customer_id` field to make a failing test pass, before checking
  whether the real `customers` table had one at all — it didn't. The
  test went green; the "view details" button in production sent the
  literal string `"None"`. Caught only because the owner used the
  feature and reported it. When a fake needs a new field to make a test
  pass, check the real schema FIRST.
- **Distinguish "nothing was given" from "something was given and it's
  wrong."** Collapsing both into one error path is an easy way to turn a
  typo into misleading guidance. `_resolve_target_or_context`
  originally returned `None` for both "no code, no context" and "code
  given, doesn't resolve" — a mistyped customer code produced "please
  specify which record" instead of "not found," which reads as the
  system not having understood the command at all. Split into a
  dedicated exception (`_TargetNotFound`) once a test caught it.
- **A rendered-image bug and a Flex-bubble-size bug both look identical
  in code review and only show up when you actually look at the
  output.** The rich menu font issue and the 10KB Flex bubble overflow
  were each invisible from reading the generation code; one needed the
  PNG opened and inspected, the other needed the actual JSON
  byte-measured. Generate-and-inspect, not generate-and-assume, for
  anything that renders.

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
- **When a vendor SDK swallows the error, reproduce it in a throwaway
  Cloud Run Job built from the live service's exact image digest**, with
  the same VPC connector and egress, then monkeypatch the library's own
  network boundary (`requests.Session.request`) to print the outgoing
  URL. Three separate SmartBrowz defects all produced one identical,
  useless error message (`UNPARSABLE_RESPONSE`); none was diagnosable by
  reading it. Working outward from "is TCP/TLS fine?" to "is plain
  `requests` fine?" to "what URL is actually being sent?" isolated each
  one. Delete the diagnostic job afterwards — and never put real secrets
  in one you intend to leave lying around.
- **A library's env-var name is not your config's env-var name.** Two
  bugs in a row here were the same shape: a correctly-set project
  variable (`SMARTBROWZ_ACCOUNTS_URL`, `catalyst_api_domain`) that the
  vendor SDK simply never reads, because it reads
  `X_ZOHO_CATALYST_ACCOUNTS_URL` / `X_ZOHO_CATALYST_CONSOLE_URL`
  instead. `grep` the installed package for `environ`/`getenv` before
  assuming your config reaches it.
- **`gcloud run deploy --source` bypasses this project's release
  discipline.** It builds into an auto-created
  `cloud-run-source-deploy` repo and leaves `image_digests` in
  `terraform.tfvars` pointing at the old image, so the next
  `terraform apply` silently reverts the deploy. Build into
  `chann1-dev`, update the digest, and go through Terraform.
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
