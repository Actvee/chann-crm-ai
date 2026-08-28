# Session Handoff — 26 Aug 2026

Written because the conversation that did most of Phases 3-7 hit its context
limit. This is what the next AI session needs to pick up cleanly — read this
before `docs/CHANN_CRM_AI_MASTER_SPEC.md`, not instead of it.

---

## 🔴 DO THIS FIRST — secret rotation, repo is public again

`envs/dev/terraform.tfvars.bak-<timestamp>` files were committed to this repo
7 times in August 2026 (a deploy-script bug — the `.gitignore` rule for
`terraform.tfvars` didn't match the `.bak-*` suffix). Each is a **full copy**
of every secret this project uses:

- `admin_secret`
- `database_password`
- LINE channel secret + access token, all 3 OAs (Sales/Customer/Technician)
- `jwt_secret`
- `openrouter_api_key`

The repo was made **private on 20 Aug 2026** specifically because of this, and
rotation was deferred at the time. **The repo is public again as of 26 Aug
2026.** The leaked values are still sitting in git history — going private
never removed them, and going public again means anyone can read that
history right now.

**Rotate before anything else, in this order:**
1. `openrouter_api_key` — only one with direct ongoing cost exposure if abused
2. LINE channel secrets/tokens (all 3 OAs) — via the LINE Developers Console
3. `database_password` — `gcloud sql users set-password chann_crm_ai_app --instance=chann1-dev-pg --prompt-for-password`, then update `terraform.tfvars`, plan, apply
4. `admin_secret` and `jwt_secret` — new random values in `terraform.tfvars`, plan, apply (both tiers share these)

After rotating, decide whether to scrub git history (`git filter-repo` or
BFG) — the leaked values will be *invalid* after rotation, but they'll still
be visible in history unless scrubbed. Scrubbing rewrites all commit hashes,
so coordinate with anyone else who has the repo cloned.

`*.tfvars.bak-*` is now in `.gitignore` — this specific mistake won't repeat,
but the historical commits still contain the old files.

---

## Where things stand

**Deployed and live on DEV** (in order): Phase 1 (Identity) → Phase 2
(Permissions) → Phase 3 (Audit Log) → Phase 4 (AI Infra) → Phase 5 (i18n) →
Phase 6 (Chat) → Phase 6.5 (Tenant Registration) → **Phase 7 (Master Data)** →
**suggest_what_you_can_do grouping fix**. HEAD on `main` is `11fda6b`.

**Not started:** Phases 8-20. Phase 8 (Profiles) is next in spec order and has
no known blockers.

**Runtime proof that matters:** Phase 4's OpenRouter integration and Phase
6's chat engine are both confirmed working against real LINE traffic, not
just unit tests — slot-filling, the permission gate, and Phase 6.5
registration have all been exercised live and behave correctly as of this
handoff.

## Read this before touching anything

- `CONTRIBUTING.md` — how to set up locally (`docker compose up`, no GCP
  needed), and the phase-claiming convention so two people/agents don't edit
  the same files at once.
- `CLAUDE.md` §14.1 — guardrails specifically for AI agents working on this
  repo (never run `terraform apply` unattended, always `git add -A` + verify
  `git status` is clean, verify config with `grep -n` not `grep -c`, etc).
  Every rule there came from a real incident in this project — worth reading
  in full, not skimming.
- `docs/CHANN_CRM_AI_MASTER_SPEC.md` — the actual spec. ~4100 lines.
- `docs/ENVIRONMENT_RESOURCE_MAP.yaml` — stage/prod are currently **parked**
  for cost (Cloud SQL stopped, Redis/VPC-connector deleted) since only dev is
  in active use. Recreate steps are in that file if either is ever needed.

## Known issues / open threads

- **Rich Menu test (Phase 19, not yet built) will need to avoid hardcoded
  role names** — flagged during the original spec audit, Principle #10
  requires permission-key checks only.
- `view_reports` is intentionally one broad permission key rather than split
  by report type — deferred to Phase 17 (Billing/Reports) where reports
  actually get designed.
- Phase 9 (whenever it's built) must pass each entity's real field schema
  into the AI intent prompt, or the model will invent plausible-looking
  fields that don't exist (observed live with a customer entity).
- CI/CD via Workload Identity Federation is blocked — the GCP account doing
  deploys has no Owner/IAM-admin role. Manual build+push+deploy is the only
  path until someone with Owner sets it up.

## Patterns worth reusing (all proven working in this codebase)

- **`scripts/check-model-kwargs.py`** — statically checks every SQLAlchemy
  model constructor call against real mapped columns, no database needed.
  Wired into `phase1-source-verify.sh` as `CHECK=MODEL_KWARGS`. Catches the
  "I assumed this column exists" class of bug before it reaches integration
  tests.
- **Anchor-based idempotent Python fixers** (see `phase7-r2-fix-*.py` in past
  session artifacts) for files that repeatedly drift between an AI's local
  copy and the deployed tree — `application/chann_app/services/chat.py` and
  `tests/unit/test_phase6_chat.py` specifically. Match on exact code
  fragments (not line numbers), `ast.parse()` before writing, refuse loudly
  on a mismatch rather than half-applying. Build the anchors from a file the
  user actually pastes back from the live deployment, not from a local
  simulation — a simulation-based version of this exact approach failed
  outright once because the simulated tree didn't match reality.
- **`git apply --check` / `--reverse --check`** to detect "already applied"
  instead of checking whether a file merely exists — file-existence isn't a
  version check and silently skipped a corrected patch at least twice in
  this project's history.
- **`GODEBUG=netdns=go`** prefix fixes Cloud Shell's intermittent IPv6
  routing failures with `gcloud`/`terraform` (symptom: `cannot assign
  requested address`, and terraform fails a *different* data source each
  run). `sysctl` and `/etc/gai.conf` tweaks alone are not sufficient.

## For an AI picking this up cold

Read, in order: this file → `CLAUDE.md` → `CONTRIBUTING.md` → the Phase
section of `docs/CHANN_CRM_AI_MASTER_SPEC.md` you're about to work on. Run
`./scripts/phase1-source-verify.sh` before considering anything "done" — it
builds its own clean venv and is the only trustworthy verification in this
project; ad-hoc `pytest` against ambient packages has produced false
failures before. Don't claim a phase is deployed without checking `/health`
on the actual Cloud Run service and confirming `git_commit` matches what you
just pushed.
