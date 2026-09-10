# probe.py — input and output format

`/tmp/diag/shared/probe.py` plays one message, or a whole conversation, through
the real chat engine (`application/chann_app/services/chat.py::handle_chat_message`)
against `tests/unit/test_phase6_chat.py::FakeDataClient` and a stubbed model,
and records **everything that happened** — not just the reply.

Nothing it does touches the repository (all patching is monkeypatching from the
script), and nothing reaches the network (the data tier is the test fake, the
model is an `httpx.MockTransport`, LINE's `push_text` is replaced by a recorder).

```
/tmp/dv/bin/python /tmp/diag/shared/probe.py cases.json --out out.jsonl [--summary]
cat cases.json | /tmp/dv/bin/python /tmp/diag/shared/probe.py > out.jsonl
/tmp/dv/bin/python /tmp/diag/shared/probe.py --selftest --summary   # the ten proof cases
```

Flags: `--repo PATH` (default `/home/thanawinmax2/stage-fix/audit`),
`--timeout SECONDS` per turn (default 30), `--out PATH`, `--summary`
(human digest on stderr), `--selftest`, `--prompt-chars N` (truncate the
captured system prompt; 0 = keep it whole).

**Corpus size.** Each model call carries the full ~12 kB system prompt, so a
few hundred cases run to tens of MB. `--prompt-chars 200` cuts the ten-case
output from 150 kB to 57 kB; `system_prompt_sha` (sha256[:12] of the *whole*
prompt) is always recorded either way, so "was the prompt identical across
these cases?" is still answerable after trimming.

---

## Input

A JSON **list** of cases, or a **JSONL** file (one case per line; `#` comments
allowed), on stdin or as the first argument.

```jsonc
{
  "id": "neg-cancel-appointment",     // string, echoed back; anything unique
  "oa": "sales",                      // "sales" | "technician" | "customer"
  "role": "sales",                    // identity's primary_role; defaults from oa
  "permissions": "sales",             // preset name OR an explicit list of keys
  "language": "th",                   // "th" | "en"
  "seed": 1,                          // recorded; seeds random (see Determinism)
  "messages": ["ไม่ต้องยกเลิกนัด C-2026-0001"],
  "expect": { "no_mutation": true }
}
```

`messages[]` entries are either a plain string, or an object
`{"text": "...", "ai": <model answer for this turn>}`. All messages of one case
run **in order, against one FakeDataClient and one ResolvedContext**, so
multi-turn cases and follow-ups work.

Optional extra keys:

| key | meaning |
|---|---|
| `ai` | case-wide scripted model answer(s). A dict → sent as the JSON content; a string → sent raw; a list → consumed one per model call, last entry repeats. `{"__status__": 500}` forces an HTTP failure. Default: `{"action":"suggest","entity":null,"fields":{},"missing":[]}`. |
| `fixture` | seed data: a preset name (`"sales"`, `"technician"`, `"customer"`, `"empty"`) or `{"preset": "...", "_customers": [...], "_tickets": [...]}` — any `_attr` of FakeDataClient. Defaults to the preset named by `oa`. |
| `pending_intent` | pre-load a half-finished flow (what `get_pending_intent` returns). |
| `is_owner`, `member_id` | passed through to the fake's `authorization_context`. |
| `why` | free text, ignored by the runner; keeps the intent of a case with the case. |

**Permission presets**: `sales` (the full 31-key sales set),
`sales_no_member_manage`, `technician`, `customer`, `none`.

**Fixture preset `sales`** (the default scene): customer `C-2026-0001`
สมชาย ใจดี / 0812345678, deal `D-2026-0001`, one **pending** follow-up `FU-1`
on that customer, product `AC12`, members `member-1` (sales, the caller) and
`m-tech` (technician สมศักดิ์), ticket `T-2026-0001`.

**`expect`** is optional and every check is non-fatal — a failed check is data,
not a stop. Supported keys, all independent:

| key | check |
|---|---|
| `no_mutation` | `true` → no write-kind call and no data-side state diff, across all turns. `false` asserts the opposite (use it for controls). |
| `mutations` | exact sorted set of write call names |
| `model_consulted` | whether any turn reached the model |
| `reply_contains` / `reply_not_contains` | string or list of strings, matched over all turns' replies |
| `answered_by` | substring of the function that built the final reply |
| `handler` | a name that must appear in the final turn's handler chain |
| `notifications` | exact sorted set of notification call names |

An `expect` that is not an object (e.g. the string `"greet"`) is preserved as
`expect_raw` and no expectation checks run.

---

## Output

**One JSON line per case**, in input order. Every field is always present, even
when empty. A case that blows up still emits its line, with `error` set.

### Case level

```jsonc
{
  "schema": "chann-probe/1",
  "id": "...", "oa": "...", "role": "...", "language": "th", "seed": 1,
  "permissions": ["customer.read", ...],   // what was actually granted
  "fixture": "sales",
  "messages": ["...", "..."],              // the texts as sent
  "expect": {...}, "expect_raw": null,
  "warnings": [],                          // e.g. unknown preset name -> substitution
  "turns": [ /* see below */ ],
  "totals": {"turns":2,"ai_calls":2,"writes":0,"conv_writes":2,
             "notifications":0,"mutated":false,"latency_ms":253.06},
  "checks": [{"name":"no_mutation","ok":true,"detail":"writes=[] data_diff=[]"}],
  "checks_ok": true,
  "error": null, "error_trace": ""
}
```

### Turn level (`turns[]`)

```jsonc
{
  "n": 1,
  "message": "ยกเลิกนัด C-2026-0001",

  // ---- the reply -------------------------------------------------------
  "reply_text": "ยกเลิกการเตือนของ C-2026-0001 แล้ว 1 รายการ",
  "reply_lines": 1, "reply_chars": 41,
  "quick_replies": [["ตั้งเตือนใหม่","เตือน C-2026-0001 พรุ่งนี้"], ...],
  "reply_fields": { /* every other ChatReply dataclass field: images,
                      entity_type, entity_id, intent, quick_reply_url,
                      list_card, ... — dumped generically, so it survives
                      changes to the dataclass */ },

  // ---- which branch answered -------------------------------------------
  "answered_by": {"function":"_handle_reminder_cancel","line":2056,"file":"chat.py"},
  "handler_chain": ["handle_chat_message","_route_chat_message", ... ,"_handle_reminder_cancel"],
  "reply_sites": [ /* every ChatReply constructed during the turn, in order */ ],
  "events": [[0.31,"enter","_route_chat_message"], [12.4,"reply_built","_handle_reminder_cancel:2056"], ...],

  // ---- was the model consulted, and what passed between ----------------
  "model_consulted": false,
  "ai_calls": [{
     "n":1, "url":"https://openrouter.ai/api/v1/chat/completions",
     "model":"probe/model", "temperature":0.0, "max_tokens":1024,
     "reasoning":{"enabled":false},
     "system_prompt":"You are an intent parser ...",   // FULL prompt, verbatim
     "system_prompt_chars":12243,                     // length BEFORE any truncation
     "system_prompt_sha":"d4b8394b88c6",              // sha256[:12] of the whole prompt
     "system_prompt_truncated":false,
     "user_message":"ขอรหัสเชิญ",
     "response_status":200,
     "response_content":"{\"action\": \"suggest\", ...}"
  }],

  // ---- every data mutation that resulted --------------------------------
  "calls":          [{"name":"list_customers","kind":"read","args":"[...]","name_inferred":false}, ...],
  "observed_calls": [{"name":"authorization_context","kind":"read","args":"(...)"}, ...],
  "reads":          ["authorization_context","get_pending_intent","list_customers","list_follow_ups"],
  "writes":         [{"name":"set_follow_up_status","args":"[...]"}],
  "conv_writes":    [{"name":"set_last_entity_ref","args":"[...]"}],
  "notifications":  [],
  "line_pushes":    [],
  "unknown_calls":  [],
  "unrecorded_calls": [],
  "state_diff": {
     "data": [{"path":"_follow_ups[0].status","before":"pending","after":"cancelled","op":"changed"}],
     "conv": [{"path":"_last_entity_ref","before":null,"after":{...},"op":"added"}]
  },
  "mutated": true,

  // ---- cost and failure --------------------------------------------------
  "latency_ms": 25.131,
  "exception": null,        // and "exception_trace" when one was raised
  "timed_out": false
}
```

---

## How the mutation record is built (three independent angles)

1. **`calls`** — the fake's own `FakeDataClient.recorded` list, sliced for this
   turn. `record_message_entity` appends a tuple with **no method name**
   (`tests/unit/test_phase6_chat.py:291`); the probe recognises it by shape and
   sets `"name_inferred": true` rather than guessing silently.
2. **`observed_calls`** — an instance-level wrapper the probe installs over
   every public method of the fake, so a write method that *forgets* to record
   is still seen. Re-entrant internal calls are not double-counted.
   `writes` is the **union**: entries the wrapper saw but `recorded` did not are
   tagged `"only_seen_by_wrapper": true` and their names also appear in
   `unrecorded_calls`. (Verified by fault injection —
   `/tmp/diag/harness/verify_backstop.py` silences `set_follow_up_status`'s
   bookkeeping and the probe still reports the write and the state change.)
3. **`state_diff`** — a deepcopy snapshot of every storage attribute of the fake
   taken before and after the turn, diffed to leaf level. This catches a write
   whose call name nobody recognises, and is the authority for "did data change".

### Call kinds

`kind` is one of `read`, `conv`, `notify`, `write`, plus `read?` / `write?`
(prefix guess for a name not in the table) and `unknown`. The table covers all
116 public methods of `FakeDataClient` exactly — no gaps, no extras (checked
programmatically), so `unknown` appearing at all means the fake has grown a new
method and the table needs a line.

* `read` — a lookup.
* `conv` — **conversation memory**, not shop data: `set_pending_intent`,
  `clear_pending_intent`, `set_last_customer_ref`, `set_last_entity_ref`,
  `set_active_tenant`, `set_display_preferences`, `record_message_entity`.
  Kept separate on purpose: a pending-intent write is *not* a cancellation, so
  `no_mutation` stays meaningful.
* `notify` — `create_notification`: an outbound message to a person. Not a data
  mutation, but a side effect an unintended branch must not produce either.
  LINE pushes are captured separately in `line_pushes`.
* `write` — a business-data mutation. `mutated` is `writes or state_diff.data`.

`state_diff.conv` holds diffs of `_pending`, `_last_customer_ref`,
`_last_entity_ref`, `_active_tenant`, `_prefs`, `_mapping`; everything else is
`state_diff.data`.

---

## How "which branch answered" is obtained

Both mechanisms are monkeypatches installed **by probe.py**; the repository is
never modified.

* `chat.ChatReply.__init__` is wrapped so every reply is stamped with the
  function, line and file that constructed it (`sys._getframe(1)`). The stamp
  travels on the object, so `answered_by` is the origin of *the reply that was
  actually returned*, not merely the last one built. `reply_sites` lists every
  reply constructed during the turn, in order — useful when a branch builds a
  reply and a later one overrides it.
* Every module-level coroutine in `chat.py` whose name contains `handle`, or
  starts with `_maybe_`, plus `_route_chat_message` and `parse_intent`, is
  wrapped to append to `handler_chain` and to the `events` timeline.

`events` entries are `[ms_since_turn_start, kind, detail]` with kinds
`enter`, `exit`, `reply_built`, `line_push` — enough to see whether a reply was
built *before* the model was consulted.

---

## Latency

`latency_ms` is wall-clock around `handle_chat_message` for that turn, including
the probe's own data-client wrapper. Measured overhead
(`/tmp/diag/harness/overhead.py`, 48 calls each, trimmed):

```
uninstrumented                     median 13.88 ms
instrumented handle_chat_message   median 12.62 ms   (branch stamps + handler wrappers: in the noise)
latency_ms as reported             median 16.60 ms   (adds the per-call client wrapper, ~2-3 ms)
```

The runner performs one throwaway route call before the first case so lazy
imports do not land on case 1. **These numbers measure routing against an
in-memory fake** — they are the cost of the decision path, not of production
I/O, and the model call is a MockTransport (an `ai-down` case spends ~550 ms in
`client.py`'s real `RETRY_BACKOFF_S` sleep, which is genuine engine behaviour).

## Determinism

`seed` is recorded and applied via `random.seed()`. Nothing on the routed path
was observed to consume `random` or `secrets` (the fake's invite code is the
fixed string `ABC234XY7Z`), so runs are already reproducible — with one
exception: `chat.py` calls `local_today()`, so cases whose wording depends on
the calendar ("พรุ่งนี้", "วันนี้") shift with the date the run happens on.
Record the run date alongside any corpus of results.

## Robustness (verified — `/tmp/diag/harness/bad-cases.json`)

One bad case never kills the run. A case that is not an object, has no
messages, carries a number instead of a string, names an unknown preset, ships a
broken fixture, scripts the model with non-JSON, or hands `expect` a string —
all nine emit a well-formed line with every field present. Per-turn work is
wrapped so an engine exception lands in `turns[].exception` (+ `exception_trace`)
and a hang lands in `timed_out` after `--timeout` seconds.

## Limitations to state when quoting this tool

* **The model is a stub.** The probe shows what the pipeline *does* with a given
  model answer, and captures verbatim what the pipeline *asked*. It cannot say
  what a real model would have replied. That is the point: it separates
  "a rule decided before the model" / "the prompt was too thin" / "no handler
  exists" (A, B, D) from "the model misread it" (C), which needs a real model.
* **The data tier is the test fake**, not Postgres. Business rules that live in
  the Data Tier only (constraints, triggers, per-license allocation) are
  approximated by whatever `FakeDataClient` mirrors.
* `args` strings are `repr()` truncated to 400 characters; `state_diff` is
  capped at 60 leaf entries per side; `events` at 400; `reply_sites` at 40.
