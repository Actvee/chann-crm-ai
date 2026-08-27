# Session Handoff — 27 Aug 2026

Written because the conversation doing Phases 3-8 hit its context limit twice.
This is what the next AI session needs to pick up cleanly — read this before
`docs/CHANN_CRM_AI_MASTER_SPEC.md`, not instead of it.

Supersedes the 26 Aug 2026 version of this file.

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
Registration) → 7 (Master Data) → 8 (Profiles).

`origin/main` HEAD is **`67b1bb3` — `feat(phase8): profiles`**.

**Not started:** Phases 9-20. Phase 9 (CRM entities: customer, deal, note,
follow-up) is next in spec order.

---

## Uncommitted work waiting to be deployed

Three fixes were built and validated but **never landed** — two prior
sessions ended mid-deploy. They ship as one patch:
`phase8-fix-oa-scoping.patch` + `phase8-fix-oa-scoping-deploy.sh`.

Validated against `67b1bb3` on a clean clone: applies cleanly, **175 tests
pass** (baseline 162), `check-model-kwargs.py` OK, pyflakes clean, both tiers
boot (data 64 routes, app 21 paths).

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
- **Phase 9 must pass each entity's real field schema into the AI intent
  prompt**, or the model invents plausible-looking fields that don't exist
  (observed live with a customer entity).
- **Phase 9 also inherits the ambiguity that fix #1 side-steps:** once
  customer records are searchable, "แก้เบอร์เป็น..." on Sales OA needs an
  explicit disambiguation path. `may_edit_on_behalf` /
  `check_profile_edit` already exist in the Data tier and are fully
  authorized — they're just not reachable from free-text chat yet, because
  resolving "แก้ลูกค้าชื่อสมชาย" to a real `chann_uid` needs the Phase 9
  customer directory.
- `view_reports` is intentionally one broad permission key — deferred to
  Phase 17 where reports actually get designed.
- **Rich Menu test (Phase 19) must avoid hardcoded role names** —
  Principle #10 requires permission-key checks only.
- Stage/prod are **parked** for cost (Cloud SQL stopped, Redis/VPC-connector
  deleted). Recreate steps in `docs/ENVIRONMENT_RESOURCE_MAP.yaml`.

---

## Patterns worth reusing (all proven in this codebase)

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
