# The agent test channel

**Read this whole file before you write a scenario or run a command.** It is
written for an AI or a person who has never opened this repository, has no
credentials, and cannot log in to LINE. Everything described here runs on one
machine, offline, and changes nothing outside this working copy.

Read it alongside **[`docs/SYSTEM_GUIDE.md`](../../docs/SYSTEM_GUIDE.md)** — the
single master document for the whole product (features per OA, permissions,
registration rules, and what only *looks* like a bug); the ready-to-paste
prompt for a testing AI is its section 10, copied here as
[`PROMPT.md`](PROMPT.md).

---

## 1. What the system is

**Chann CRM AI is a CRM that a Thai appliance shop operates from LINE chat.**
A salesperson types "เปิดดีลให้สมชาย" into a LINE conversation and a deal is
created; a technician types "เช็คอิน" when they arrive at a job; a customer
types "แอร์ไม่เย็น" and a service ticket is opened for them. There is also a
web dashboard, and the project's hard rule is *parity*: anything doable in
chat must be doable in the dashboard, and the reverse. Almost all replies are
Thai; English is a supported second language.

**It is four tiers, and they only talk downwards.** Presentation (Next.js
dashboard) calls Application; Application (FastAPI — the LINE webhook, the
intent parsing, all the business rules) calls Data; Data (FastAPI +
SQLAlchemy) is the only tier that touches PostgreSQL and Redis. The chat brain
lives in `application/chann_app/services/chat.py`. Most messages are answered
by a deterministic layer there — trigger phrases, entity codes, conversational
context — and only free prose is handed to a language model, which fills in an
`{action, entity, fields, missing}` intent that the same code then executes.
The most expensive bugs this project has had lived in the *seam* between two
tiers and were invisible from inside either one, which is why the `db` backend
below exists.

**There are three LINE Official Accounts, and which one a message arrives on
decides everything.** `sales` (the Sales/CS OA, for shop staff), `technician`
(for field technicians), `customer` (for the shop's own customers). One LINE
account is one person on all three, but a *registration* is per OA: the owner
of a shop is a stranger to the Technician OA until they redeem a technician
invite code, and redeeming one adds a second membership row rather than
changing the first. Permissions come from that per-OA row. A capability
offered on the wrong OA is a bug, and so is a reply that shows a customer
something only staff should see.

---

## 2. Why this channel exists, and what you may touch

Driving the deployed DEV environment through LINE needs the LINE channel
secrets (every webhook request is HMAC-verified in
`application/chann_app/line/webhook.py`) and GCP credentials. **You will not be
given either, and you must not ask for them, look for them, or work around
them.** No route may be added that skips signature verification, and no
test-only authentication path may be added to any shipped service. The
channels in this document are entirely local; that is the whole point of them.

**You may:**
- add and edit files under `scripts/agent-test/scenarios/`;
- run every command in this file;
- read any source file in the repository;
- report a bug you find, with the scenario that reproduces it.

**You must not:**
- edit `application/chann_app/services/chat.py` or any other shipped source in
  order to make a scenario pass — if a scenario fails, either the scenario is
  wrong or you have found a bug, and both are reported, not patched away;
- run `terraform`, `gcloud`, `docker push`, or any deployment script;
- read, write, or transmit `terraform.tfvars`, `.env`, or anything that looks
  like a credential;
- make a network call. Nothing here needs one: the language model is answered
  by an in-process stub, and both FastAPI apps are mounted in-process. If a
  run tries to reach the internet, that is itself a bug worth reporting.

---

## 3. Running it

```bash
# every scenario, fake backend (fast, no database) — start here
python scripts/agent-test/run.py

# what is available
python scripts/agent-test/run.py --list

# one scenario, showing every step and not just the failures
python scripts/agent-test/run.py --only sales-day --verbose

# a scenario file that is not in the scenarios directory
python scripts/agent-test/run.py /tmp/my-new-case.yaml

# machine-readable, for a program to read
python scripts/agent-test/run.py --json > result.json

# against the real Data tier and a real PostgreSQL
export TEST_DATABASE_URL=postgresql+psycopg://chann:chann@127.0.0.1:5432/chann_crm_ai_test
python scripts/agent-test/run.py --backend db
```

**Which database the `db` backend uses.** It starts by dropping the schema, so
it must never share a database with anything else. It therefore takes the
server, port and credentials from `TEST_DATABASE_URL` but **replaces the
database name with one derived from this working copy's directory** —
`chann_agent_test_<directory>` — and creates that database on first use. The
shared `chann_crm_ai_test` is never touched. Set
`AGENT_TEST_DATABASE_URL` to override the whole URL if you want it somewhere
specific. The line the run prints on failure always names the database it was
using.

The same hazard applies to the integration suite: `tests/integration` begins
with `DROP SCHEMA public CASCADE`, so **two checkouts of this repository must
not point `TEST_DATABASE_URL` at the same database**. Give each one its own:

```bash
psql "postgresql://chann:chann@127.0.0.1:5432/postgres" \
  -c "CREATE DATABASE chann_my_test OWNER chann"
export TEST_DATABASE_URL=postgresql+psycopg://chann:chann@127.0.0.1:5432/chann_my_test
```

A run that fails with tables that "do not exist", or a suite that goes from
green to a hundred errors for no reason you changed, is almost always this.

Use the repository's virtualenv if `python` is not already it — in this
project it is `/tmp/dv/bin/python`. Run every command from the repository
root.

Flags: `--backend fake|db`, `--only NAME` (repeatable), `--list`, `--json`,
`--verbose`, `--keep-going` (run the steps after a failure instead of skipping
them; they usually depend on the failed one, so the default is to skip).

**Exit codes**

| code | meaning |
|---|---|
| `0` | every step of every selected scenario passed |
| `1` | at least one step failed — a real result, read the report |
| `2` | the run never started: a scenario file the runner refuses, an unknown `--only` name, or a backend that is not available (usually `TEST_DATABASE_URL` unset) |

**The `--json` report**

```jsonc
{
  "backend": "fake",
  "ok": false,                       // false if any step failed
  "scenarios": [{
    "name": "sales-day",
    "file": "scripts/agent-test/scenarios/sales-day.yaml",
    "ok": false,
    "seconds": 0.31,
    "steps": [{
      "n": 6,                        // 1-based step number, matches the file
      "kind": "send",                // send | seed | reset | http | expect
      "input": "รายชื่อลูกค้า",        // what the step did
      "ok": false,
      "reply": "…the full reply text…",
      "quick_replies": [["ดูลูกค้า", "รายชื่อลูกค้า"]],
      "images": [],
      "intent": {"action": "read", "entity": "customer"},
      "used_ai": false,
      "problems": ["expected the reply to contain 'สมชาย'\nclosest line: …"],
      "notes": []                    // things the backend did differently
    }]
  }],
  "summary": {"scenarios": 3, "passed": 2, "failed": 1,
              "steps": 40, "steps_failed": 1},
  "skipped": [{"name": "per-oa-registration", "needs_backend": "db"}]
}
```

`problems` is empty exactly when `ok` is true. Each entry names what was
wanted and what came back; text assertions add the closest line of the actual
reply so you can see whether the answer was *wrong* or merely *worded
differently*. `reply` is always the complete text, so you can write the next
assertion from the report alone.

---

## 4. The two backends

| | `fake` (default) | `db` |
|---|---|---|
| Data tier | the in-memory fake the unit suite uses | the real one, in-process over its real HTTP surface |
| Database | none | its own PostgreSQL database, dropped and migrated from empty on every run |
| Speed | a scenario in milliseconds | about a second, plus one migration per run |
| Proves | routing, wording, permissions, OA scoping, conversational flow | all of that **plus** the seam between the tiers: real routes, real validation, real ids, real registration |
| `http` steps | not available | available |
| `seed.raw`, `seed.members` | available | refused — use real registration |
| `permissions:` on a step | honoured | ignored (the member's real role decides); the runner says so once per scenario in `notes` |

A scenario declares `backend: fake`, `backend: db`, or `backend: any`
(default). The runner skips scenarios that do not match the backend you asked
for and lists them under `skipped` — this is not a failure and does not affect
the exit code. **Write `backend: any` whenever you can**: a scenario that
passes on both is the strongest kind, because a disagreement between them is
almost always a real tier-seam bug.

---

## 5. Writing a scenario

A scenario is one YAML (or JSON) file: a name, some defaults, and a list of
steps executed in order. Put it in `scripts/agent-test/scenarios/` to have it
run by default and covered by the test suite. `scenario.schema.json` next to
this file is the same contract in JSON Schema form.

Here is a complete one, with every kind of step:

```yaml
name: quote-for-a-new-lead              # lowercase, dashes; --only matches this
description: a lead becomes a customer, a deal, and a quote
backend: any                            # fake | db | any
actor:                                  # defaults for every `send` below
  oa: sales                             # sales | technician | customer
  role: sales
  language: th                          # th | en
  permissions: sales                    # a named set, or an explicit list

steps:
  # 1. Give the shop the data the scenario needs.
  - seed:
      products:
        - product_id: AC12
          product_name: "แอร์ 12000 BTU"
          unit_price: "15900.00"
      customers:
        - ref: somchai                  # `ref` makes this row referable later
          first_name: "สมชาย"
          last_name: "ใจดี"
          phone: "0812345678"
      deals:
        - ref: deal1
          contact_id: "$somchai"        # "$ref" is that row's id

  # 2. Say something, and assert on the reply.
  - send: "รายชื่อลูกค้า"
    expect:
      contains: ["สมชาย", "C-2026-0001"]
      used_ai: false                    # answered without calling the model
      is_not: [not_found, permission, generic_error]

  # 3. Free prose: say what the model would have answered.
  - send:
      message: "สมชายจะซื้อแอร์ 2 ตัว"
      ai:
        action: create
        entity: deal
        fields: {target_name: "สมชาย"}
        missing: []
    expect:
      intent: {action: create, entity: deal}
      max_lines: 15

  # 4. Try the same thing with a permission missing.
  - send:
      message: "สร้างใบเสนอราคา"
      permissions: [deal.read]
    expect:
      is_not: [generic_error, not_a_feature]   # a refusal is fine; a crash is not

  # 5. Assert again on the reply that is already on the table.
  - expect:
      not_contains: ["due_time", "owner_member_id"]

  # 6. Leave the chat surface (db backend only).
  - http:
      method: GET
      path: "/api/v1/licenses/{license_id}/customers"
      as: {oa: sales, permissions: all}
    expect:
      status: 200
      json_path: {"0.first_name": "สมชาย"}

  # 7. Throw the shop away and start clean.
  - reset: true
```

### Step types

**`send`** — one chat message. A bare string uses every `actor` default; a
mapping overrides them for that message only (`message`, `oa`, `role`,
`language`, `permissions`, `ai`).

`ai` is what the language model *would have answered*. No scenario ever
reaches a real model: an in-process stub answers every call, and when you omit
`ai` it answers `{"action": "suggest"}` — which is how `used_ai: false` proves
a message was handled by the deterministic layer alone. Give `ai` when you are
testing the path *after* parsing, and copy the shape from the example above.

**`seed`** — shop data, created through the same data client the run is using,
so the tier's own validation applies and a shape production would reject
cannot be seeded. Collections: `products`, `customers`, `deals`, `quotes`,
`tickets`, `warranties`, `invites`; plus `members` and `raw` (fake only),
`redeem` (db only), and `call` for anything else:

```yaml
  - seed:
      call:
        - method: create_note           # any method on the data client
          args: {payload: {entity_type: deal, entity_id: "$deal1", body: "จดไว้"}}
```

Any row may carry `ref: <name>`. Afterwards `"$name"` is that row's `id` and
`"$name.field"` is any other field of it — `"$tech_code.invite_code"`, for
instance. This exists so a scenario never has to guess an id the system
allocates.

`raw` writes straight onto the fake client's private state (`tickets:` sets
`_tickets`, and so on). It is the escape hatch for a starting state no client
call produces — a ticket already dispatched and awaiting acceptance, say — and
it is why `technician-job.yaml` is fake-only.

**`reset`** — a fresh fake client, or a brand-new tenant on the `db` backend.
Refs are forgotten. Use it when one file covers two unrelated situations;
prefer two files.

**`http`** — call an Application-tier route, the way the dashboard does
(`db` backend only). `{license_id}` in the path is filled in for you, and
`$ref` works in the path and the body. `as` says who is calling; it defaults to
the shop owner holding every permission.

**`expect`** — see below. On a `send` or `http` step it asserts on that step's
result; on its own it asserts again on the most recent one.

**`note`** — free text carried into the JSON report. Does nothing.

### Every assertion

| assertion | takes | passes when |
|---|---|---|
| `contains` | a string or a list | every string appears in the reply text |
| `not_contains` | a string or a list | none of them appears |
| `regex` | a pattern or a list | each pattern matches (Python `re`, multi-line) |
| `quick_replies_include` | a string or a list | a quick-reply button's label or its payload contains it |
| `has_image` | `true` / `false` | the reply carries image URLs, or does not |
| `intent` | `{action, entity}` | the reply's parsed intent matches (only model-routed replies carry one) |
| `max_lines` | a number | the reply is no longer than that many lines |
| `max_chars` | a number | the reply is no longer than that many characters |
| `is_not` | a class or a list | the reply is none of those failure shapes |
| `used_ai` | `true` / `false` | the model was called, or was not |
| `status` | a number | *(http)* the response status code |
| `json_path` | `{"a.0.b": value}` | *(http)* each dotted path holds that value (compared as strings; digits index arrays) |
| `json_contains` | a string or a list | *(http)* the serialised body contains each string |

All assertions in one `expect` must hold, and **all of them are reported** —
the runner does not stop at the first, so one run tells you everything that
disagreed.

### The failure classes for `is_not`

These are the shapes a reply takes when the system has failed the person. They
are the same list `scripts/dev/simulate-phrasings.py` uses, so a finding here
and a finding there mean the same thing.

| class | the reply said, in effect |
|---|---|
| `not_sure` | "I am not sure what you want" — the message was not understood |
| `permission` | refused for lack of a permission key |
| `not_found` | nothing matched the code or name given |
| `generic_error` | a bare apology; something broke |
| `not_a_feature` | "the system does not do that" |
| `ai_down` | the model was unreachable |

`is_not` is the highest-value assertion in this file and the cheapest to
write. A step whose only claim is `is_not: [not_sure, generic_error]` still
catches the regressions that matter most.

### Advice on writing good ones

- **Assert on meaning, not on wording.** `contains: "D-2026-0001"` survives a
  rewrite of the sentence around it; `contains: "สร้างดีลเรียบร้อยแล้วครับ"` does
  not. Prefer codes, names, numbers, and `is_not`.
- **`not_contains` is how you guard the rules.** Raw field names
  (`due_time`, `service_address`, `owner_member_id`) must never reach a person;
  sales vocabulary (`ใบเสนอราคา`, `ดีล`) must never appear on the Technician or
  Customer OA.
- **One journey per file.** A scenario is a story a real person lives through;
  when you find yourself writing `reset`, write a second file instead.
- **A failing step is a result.** Report it with the file and step number.
  Never change shipped source to make it pass.
- **The runner rejects a file it does not fully understand** — an unknown
  assertion name, an empty `expect`, a `regex` that does not compile — with the
  file, the step number, and usually a suggestion. That is exit code 2, and it
  means the file, not the system, is wrong.

---

## 6. The other local channels

These existed before this one and cover things scenarios do not. All of them
run from the repository root, offline.

### The pytest suites

```bash
/tmp/dv/bin/python -m pytest tests/unit tests/boundary -q \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_fake_credentials_are_rejected_by_the_real_zoho_endpoint" \
  --deselect "tests/unit/test_smartbrowz_pdf_renderer.py::TestSmartBrowzPdfRenderer::test_verify_connection_surfaces_the_same_clear_errors"

# your own database, not one another checkout is also using — see section 3
export TEST_DATABASE_URL=postgresql+psycopg://chann:chann@127.0.0.1:5432/chann_my_test
/tmp/dv/bin/python -m pytest tests/integration -q
```

- `tests/unit` — the largest suite by far. Every chat behaviour against the
  fake Data client (`test_phase6_chat.py` is 6,500 lines of it), plus the data
  client contract, i18n, guides, PDF templates, reminders, permissions.
- `tests/boundary` — the tier rules as executable checks: Presentation may not
  import SQLAlchemy, Application may not touch PostgreSQL, and an
  accessibility pass over the dashboard.
- `tests/integration` — needs PostgreSQL. Migrations from an empty schema, the
  Data tier's repositories, and cross-tier HTTP journeys
  (`test_http_journey.py`, `test_end_to_end_journey.py`) — the pattern the `db`
  backend here is built on.

**Baselines** (this branch, 8 Sep 2026): `tests/unit` + `tests/boundary`
**1522 passed** with the two deselections above (they are the only tests that
talk to a real external endpoint, and they fail without network);
`tests/integration` **358 passed**.

### The simulators

```bash
/tmp/dv/bin/python scripts/dev/simulate-day.py
/tmp/dv/bin/python scripts/dev/simulate-edge-cases.py
/tmp/dv/bin/python scripts/dev/simulate-phrasings.py
```

Scripted runs of the real chat handler with the fake data client and no model
— the same machinery as this channel, hard-coded in Python instead of read
from a file. `simulate-day` walks a shop's day on all three OAs;
`simulate-edge-cases` pushes the awkward paths; `simulate-phrasings` plays 462
different ways of asking for the same things and reports which ones the
deterministic layer understands.

**Baselines**: `simulate-day` and `simulate-edge-cases` must print
**`0 FINDINGS`**. `simulate-phrasings` prints
**`462 cases · 11 not as expected · 0 long replies`** — those 11 are known and
tracked; a twelfth is a regression.

Use a simulator to *discover* how the system answers something, then write a
scenario here to *lock in* the answer.

### The static checks

```bash
/tmp/dv/bin/python scripts/dev/check-parity.py       # chat and dashboard can do the same things
/tmp/dv/bin/python scripts/dev/check-routes.py       # every dashboard call hits a real Application route
/tmp/dv/bin/python scripts/dev/check-client.py       # every data-client call hits a real Data route, right method
/tmp/dv/bin/python scripts/dev/check-methods.py      # every data-client method called actually exists
/tmp/dv/bin/python scripts/dev/check-fields.py       # field names agree across the three tiers
/tmp/dv/bin/python scripts/dev/check-perms.py        # every permission key in use is in the catalogue
/tmp/dv/bin/python scripts/dev/check-auth.py         # no tenant route resolves a principal without checking a permission
/tmp/dv/bin/python scripts/dev/check-triggers.py     # no short chat trigger is tested before a longer one containing it
/tmp/dv/bin/python scripts/dev/check-chat-format.py  # every {placeholder} in a reply template is supplied
/tmp/dv/bin/python scripts/dev/check-placeholders.py # every {placeholder} in a UI string is filled, in every locale
/tmp/dv/bin/python scripts/dev/check-i18n-usage.py   # every referenced translation key is declared
```

They read source, not runtime, so they are fast and catch the whole class of
bug at once instead of one instance. They are heuristics: a finding is a
prompt to look, not a verdict.

**Known baseline noise on this branch** — these lines are expected and are not
regressions: `check-client` reports `GET /health`; `check-routes` reports
`licenses/X/tickets${member`; `check-methods` reports
`create_document_template_version`; `check-triggers` reports `LEAD_DELETE`;
`check-i18n-usage` reports `t.tickets`. Anything *else* new is worth
reporting.

### What no local channel can do

Driving the real LINE apps, and the deployed DEV environment. Both need
secrets you will not have. Everything above is the way in.

---

## 7. What this channel cannot test

Be honest about these in any report you write.

- **The LINE surface itself.** Scenarios call the chat engine directly, so
  they prove the *reply*, never that LINE rendered it: no signature
  verification, no Flex bubble JSON, no rich menu, no push or reply token, no
  image actually delivered. `has_image` says a URL was attached, nothing more.
- **The language model.** Every model call is answered by a stub with whatever
  the scenario wrote. Scenarios prove what the system does with a *given*
  intent; whether the real model produces that intent from that sentence is
  runtime acceptance, not a test.
- **The dashboard.** No browser, no React, no rendering. `http` steps reach
  Application-tier routes, which is where the dashboard's data comes from, but
  a broken button is invisible here. `check-parity.py` and `check-routes.py`
  are the closest local cover.
- **PDFs and external services.** SmartBrowz (the PDF renderer), GCS, and the
  payment provider are not called.
- **Redis, as itself.** The `db` backend stands Redis in with an in-process
  dictionary, because the Data tier keeps conversational state there and with
  no Redis every scenario would restart at every message. Real eviction, real
  TTL expiry under load, and two processes sharing one cache are not covered.
- **Concurrency and time.** Steps run one after another in one process. Races,
  scheduled sweeps, and anything depending on the clock advancing are out of
  reach.
- **Anything deployed.** No scenario touches DEV, Stage or Production, and
  none ever should.

---

## 8. If you add to this channel

Scenario files under `scenarios/` are covered by
`tests/unit/test_agent_test_channel.py`, which runs every one of them and
fails the suite if any step fails. That is deliberate: a scenario that has
rotted is worse than no scenario, because it is read as evidence. If you add a
scenario, run

```bash
/tmp/dv/bin/python -m pytest tests/unit/test_agent_test_channel.py -q
```

before you report that you are done.
