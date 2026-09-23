# External API (round 21B) — design

**Owner's ask (23 ก.ย. 2569):** "ทำ API เลย" · then: "การ authori เอาแค่ให้เจ้าของร้าน
generate api code ให้คนภายนอกสำหรับใช้ Api ก็พอแล้ว".

**Answers that shaped this:** primary consumer = the customer's accounting/ERP
system · v1 is read + write · the shop owner makes keys on the dashboard ·
outbound webhooks are the next round, not this one.

## 1. What this is

A curated, versioned REST surface at `/api/ext/v1` on the Application tier,
authenticated by a per-shop API key the shop owner generates and shows to the
outside party once. It is a *separate contract* from the dashboard's own
`/api/v1/...` routes: small, stable, documented, and limited to the business
resources an ERP needs. Inside, it calls the same services and `DataClient`
the dashboard and chat use, so every rule those already enforce (tenant
scoping, status transitions, "a deal needs products", suspended = read-only)
holds for the API without being restated.

Not in this round (recorded so nobody thinks they were forgotten): outbound
webhooks · `Idempotency-Key` on POST · key expiry dates · per-package quotas ·
letting API keys drive the dashboard's own routes (approach A) — the resolver
is written so that can be added without rework.

## 2. Authorization — one key, acts as the shop

Per the owner, there is no permission picker. A key is created by the shop
**owner** (`is_owner`, not merely `setting.manage`) and acts with the shop's
full staff permission set. In practice the reachable surface is bounded by
which endpoints §4 exposes, not by permission keys.

Implementation keeps `principal.require("<key>")` in every ext route anyway,
with the principal built as:

```
TenantPrincipal(
  license_id=key.license_id,
  chann_uid=f"api:{key.id}",      # audit actor; never a LINE identity
  role="api", is_owner=False,
  permission_keys=STAFF_FULL_SET,  # the admin-role catalog, resolved at request time
  audience="api",
  license_status=<shop status>,    # refuse_if_suspended(method) applies as for LIFF
)
```

so a future "scoped key" is a stored list replacing `STAFF_FULL_SET` and
nothing else changes. `audience="api"` is added to the places that branch on
audience (`is_customer` stays false; field-scoping helpers treat it as staff).

## 3. Data tier

### Table `api_keys` (migration `0037_api_keys`)

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| license_id | uuid fk licenses, index | |
| name | varchar(120) | what the owner calls it ("ระบบบัญชี Express") |
| key_prefix | varchar(16) | first 12 chars, for display: `chann_live_ab12…` |
| key_hash | char(64) unique | SHA-256 hex of the full key; the lookup index |
| created_by_chann_uid | varchar(32) | the owner who made it |
| last_used_at | timestamptz null | stamped by the touch call, at most once per minute |
| revoked_at | timestamptz null | soft revoke; a revoked key is never reused |
| created_at / updated_at | TimestampMixin | |

Key format: `chann_live_` + 32 chars from `secrets.token_urlsafe` restricted to
`[A-Za-z0-9]`. High entropy → SHA-256 is the right hash (argon2 is for
low-entropy passwords and would make every request slow). The plaintext exists
only in the create response.

### Internal routes (`/internal/v1`, shared-secret as today)

- `POST /licenses/{license_id}/api-keys` `{name, created_by_chann_uid}` →
  creates, returns the plaintext key **once** plus the row. Audit
  `api_key create`.
- `GET /licenses/{license_id}/api-keys` → rows without hashes.
- `POST /licenses/{license_id}/api-keys/{id}/revoke` → sets `revoked_at`, audit
  `api_key delete` (the same verb shape as invites; `check-parity` reads `revoke` as delete). 404 if another shop's; 403 `owner_only` when `X-Actor-Id` is not an active owner member.
- `POST /api-keys/resolve` `{key_hash}` → `{key, license_status, remaining}`
  or 404. One call does three things: finds the key, runs the rate-limit
  counter in Redis (`INCR api_rl:{key_id}:{minute}` with 120 s TTL; limit
  600/min → `remaining` may be negative), and stamps `last_used_at` when it is
  older than 60 s. Redis down → no limit applied, logged, never a 500 (the
  cache is supporting infrastructure, per the architecture rule).

### `updated_since` on list repositories

`CustomerRepository`, `DealRepository`, `InvoiceRepository`,
`TicketRepository` `list_for_license`/`count_for_license` gain
`updated_since: datetime | None` (filter `updated_at >= …`). Internal routes
and `DataClient` forward it. The dashboard does not use it yet; the API does.

## 4. Application tier

### Auth — `chann_app/auth/api_key.py`

`resolve_api_principal(request, client)`: reads `Authorization: Bearer
chann_live_…` (401 `missing_or_malformed_key` otherwise) → SHA-256 → `POST
/api-keys/resolve` → 401 `unknown_or_revoked_key` on 404 → `remaining < 0` →
429 with `Retry-After: <seconds to next minute>` → `refuse_if_suspended(status,
method)` (a suspended shop's key can read, not write, like a LIFF session) →
`TenantPrincipal` as in §2. Response headers on every ext reply:
`X-RateLimit-Limit: 600`, `X-RateLimit-Remaining: n`.

### Sub-app — `chann_app/routers_ext.py`

`ext_app = FastAPI(title="Chann CRM AI — Public API", version="1", docs_url="/docs",
openapi_url="/openapi.json")` mounted at `/api/ext/v1` by `main.py`. Only ext
routes appear in its OpenAPI; the dashboard's schema is untouched. A shared
`Depends(api_principal)` on the router, so no route can forget auth.

Errors are one shape everywhere: `{"error": {"code": "…", "message": "…"}}`
with the same `_REASON_CODES` mapping the dashboard already uses for
`DataTierError`, plus `validation_error` (422), `rate_limited` (429),
`forbidden` (403), `not_found` (404).

### Endpoints (v1)

Every list: `limit` (default 50, max 200), `offset`, body `{"items": [...], "total": n}` **and** the `X-Total-Count` header (an object, not a bare array, so a later field never breaks a parser);
`updated_since` (ISO-8601) where marked ★. Codes are the human ones (`C-2026-0001`,
`D-…`, `Q-…`, `INV-…`, `T-…`); ids are uuids; both are accepted on `{id}`
paths where the service already resolves codes.

| resource | routes | permission |
|---|---|---|
| me | `GET /me` → shop (code, name, status) + key name/prefix | — |
| customers ★ | `GET /customers` (`q`, `stage`), `GET /customers/{id}`, `POST /customers`, `PATCH /customers/{id}` | customer.read / .create / .update |
| deals ★ | `GET /deals` (`stage`, `customer_id`), `GET /deals/{id}` (with product lines), `POST /deals`, `PATCH /deals/{id}` (fields + `stage`, lost needs `lost_reason`) | deal.read / .create / .update |
| quotes | `GET /quotes` (`deal_id`, `status`), `GET /quotes/{id}` (lines, totals), `GET /quotes/{id}/pdf` → `{url, expires_at}` via the existing document link | quote.read |
| invoices ★ | `GET /invoices` (`status`, `deal_id`, `quote_id`, `overdue`), `GET /invoices/{id}` (lines, payments, balance), `POST /invoices` `{deal_id \| quote_id, issue: bool}`, `POST /invoices/{id}/payments` `{amount, method, paid_at?, reference?}`, `GET /invoices/{id}/pdf`, `GET /invoices/{id}/receipt-pdf` | invoice.read / .create / .update |
| tickets ★ | `GET /tickets` (`status`, `customer_id`), `GET /tickets/{id}`, `POST /tickets` `{customer_id, problem, address?, appointment_at?}` | ticket.read / .create |
| warranties | `GET /warranties` (`q`, `customer_id`), `GET /warranties/{id}`, `POST /warranties` `{serial_number, product_id?, customer_id?, purchase_date?}` | warranty.read / .create |
| products | `GET /products` (`q`, `active`), `GET /products/{id}`, `POST /products`, `PATCH /products/{id}` | product.read / .manage |

Deliberately absent: anything platform-level, members/roles/settings, chat
sessions, signatures, PDPA, guides, the customer-audience "mine" routes,
approvals and service reports (a later round if an ERP ever asks).

### Key management for the owner (both surfaces — the parity rule)

Dashboard routes (LIFF, `/api/v1`): `GET|POST /licenses/{id}/api-keys`,
`DELETE /licenses/{id}/api-keys/{key_id}` — owner only (403 `owner_only`
otherwise).

**Screen** — nav entry **จัดการร้าน > API** (`needs: owner`; the rail already
hides entries the person cannot open). Designed with ui-ux-pro-max:
- intro line in plain words: "สร้าง key ให้ระบบภายนอก (บัญชี/ERP) เรียกข้อมูลร้านนี้ได้"
  + link to `/api/ext/v1/docs`;
- list rows: name · `chann_live_ab12…` · created date · last used ("ยังไม่เคยใช้");
- one primary action **สร้าง key** → sheet asks only for a name → on success
  the same sheet shows the full key once, monospace, with **คัดลอก** and the
  sentence "จะไม่แสดงอีก ถ้าหาย ให้สร้างใหม่แล้วเพิกถอนอันเดิม";
- revoke = `data-variant="danger"` per row under `RecordActions`-style
  separation, with `ConfirmDialog` naming the key;
- empty state explains what a key is for.
Strings in `th.ts`/`en.ts` under `dashboard.apiKeys`.

**Chat** (model-first — measured with `ask-model.py` before and after):
- `รายการ API key` → list (name, prefix, last used); `ACTION_PERMISSIONS[("read","api_key")] = "setting.manage"` but the handler additionally requires owner, replying who can;
- `เพิกถอน API key <ชื่อ>` → confirm → revoke; `("delete","api_key")`;
- `สร้าง API key` → the handler does **not** create; it replies with a button
  that opens the API page. A secret must never sit in a LINE thread. Recorded
  in `check-parity.py` `ACCEPTED` as `("api_key","create")` with this reason.
- Duplicate names on revoke → the usual choice buttons; no guessing.

## 5. Documentation

- `/api/ext/v1/docs` (Swagger) and `/api/ext/v1/openapi.json`, public, no
  secrets; every route has a Thai + English summary and example bodies.
- `docs/API.md`: how to get a key, the auth header, pagination, `updated_since`
  sync pattern for an ERP, error shape, rate limit, what is not there yet.
- In-app guide: a step "เชื่อมต่อระบบภายนอก (API)" under จัดการร้าน in
  `services/guides.py` with image slot `sales-api` drawn by
  `render-guide-images.py` (the API page with a key shown once) — and looked
  at. `render-guides.py` re-run.

## 6. Security posture (reduced-security project, stated plainly)

Keys are hashed at rest and shown once · revocation is immediate (every
request resolves the key) · a suspended/deleted shop's key cannot write ·
rate-limited per key · every write is audited as the Data routes already do (`actor_type="user"`) with
`actor_id="api:<key id>"` — the prefix is what tells a key from a person (the CHECK on `audit_log.action` uses existing verbs) ·
the ext surface cannot reach platform, member, role or setting routes · no
CORS (server-to-server) · transport is Cloud Run HTTPS. Still not a
"secure-production" model: no key rotation schedule, no IP allowlist, no
per-key scopes — say so in the release notes.

## 7. Testing

- **unit** — resolver: no header / bad prefix / unknown / revoked → 401;
  suspended shop GET ok, POST 403; `remaining<0` → 429 with `Retry-After`;
  principal carries `audience="api"` and the staff set. Ext routes with a
  `FakeDataClient`: list/get/create per resource, error shape, `X-Total-Count`,
  `updated_since` forwarded. Key-management routes: owner only, plaintext
  present only in the create response. Chat: three sentences through the
  fake model road + owner-only refusal text.
- **boundary** — `routers_ext.py`/`auth/api_key.py` import nothing from
  `chann_data`; ext OpenAPI contains only `/api/ext/v1` paths.
- **integration (Postgres)** — create key → resolve → list customers via the
  key → `updated_since` returns only the row just updated → revoke → 401;
  rate-limit counter with Redis stubbed.
- **agent-test** scenario `api-keys` for the chat road; `simulate-phrasings`
  unchanged; `check-parity`, `check-perms`, `check-routes`, `check-client`,
  `check-i18n-usage` clean; `npm run typecheck && build`.
- **runtime** — after deploy: `curl -H "Authorization: Bearer …" …/api/ext/v1/me`
  from Cloud Shell against DEV with a key made on the dashboard.

## 8. Delivery

Round **21B**, one patch on top of `c0a71a3`, migration 0037 → database
image + `chann-crm-ai-dev-migrate` before the data tier; all three tiers
rebuilt. Work runs as three worktree streams (Data+auth+ext router · dashboard
page+chat+guide · docs+tests) integrated in `~/stage-fix/v20` before the gate.
Checklist section V (21B-1…) and the tester guide get the new rows.
