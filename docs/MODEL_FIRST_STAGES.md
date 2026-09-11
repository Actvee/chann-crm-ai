# The model-first conversion, stage by stage

> Companion to `MODEL_FIRST.md`, which states the rule. This is the order the work
> ships in. Produced by a mapping pass over the real code, then broken by two
> adversarial reviewers who ran the edits rather than reading them — their
> corrections are folded in below, and what they killed is at the bottom so it does
> not get proposed again.

Every stage is measured with a per-sentence A/B and ships on its own.
`scripts/dev/measure-road-share.py` records where each utterance is decided;
`scripts/dev/ask-model.py` says what the deployed model makes of a sentence.

| # | stage | tier | migration | state |
|---|---|---|---|---|
| 0 | Reconcile the corpus and instrument the road before anything is narrowed (no behaviour change) | Tooling only (scripts/dev); no tier code tou | no |  |
| 1 | Put the license into the identity of both conversational refs, at every hop (key, route, schema, | Data (cache + routes + schemas) and Applicat | no | **shipped** (fed077c + this round) |
| 2 | Stamp the tenant on pending_intent, and close the two leaks a scoped key does not reach (revocat | Data (schema + invalidation) and Application | no |  |
| 3 | Prompt-surface hygiene: tell the model only what this OA can do, and stop shipping customer rows | Application (chat + services/ai/intent) and  | no |  |
| 4 | Send the record in focus to the model: per-OA, per-shop, no id, no PII, no code the model may co | Application (services/ai/intent + chat) | no |  |
| 5 | Add the three refusals that do not exist yet, BEFORE any branch is narrowed | Application (chat + intent_guard call sites) | no |  |
| 6 | Build the context fallback road (pure addition; nothing narrowed yet) | Application (chat) | no |  |
| 7 | Narrow the read-only deictics (smallest first: no writes, no decline power to preserve) | Application (chat router) | no |  |
| 8 | Narrow the two branches that only ever OFFER (a confirmation, not a write, is what protects the  | Application (chat router) | no |  |
| 9 | Narrow the write branches, one per ship, largest last | Application (chat router) | no |  |
| 10 | The bulk-customer branch: give it a confirmation (the goal's target, reached by the honest lever | Application (chat) | no |  |

## What the reviewers killed — do not re-propose without new evidence

* **Stage 7 as written** re-opens the 8 ก.ย. owner bug: `ขอข้อมูลดีลล่าสุด` contains
  `ข้อมูลดีล`, a deal-detail trigger that dispatches above it, and the model road for
  a bare `read`/`deal` lists every record in the tenant — including other customers'
  phone numbers.
* **Stage 9c** has no model-road path at all: the prompt's deal block documents only
  `action="create"`, so nothing the model can return reaches `transition_deal_stage`.
* **Stage 9a** moves zero corpus utterances and produces zero findings — its only
  measurable effect is a broken combination with 9d.
* **Stage 9d** makes the model's `qty` a delta whenever the message contains `อีก`,
  while the prompt teaches `qty` as absolute: a silent off-by-N on a money-bearing line.
* **Stage 8's `_sales_interest_item` move**: the deal-create field shape carries no
  product or quantity, so the model's natural answer cannot be executed.

## Corrections that must be applied when each stage is written

* Every branch that moves onto the context road must check `_oa_allows(ctx.oa, key)`,
  not just membership in `permission_keys`. The one that did not — `_appointment_net` —
  wrote a real reminder on an OA whose boundary forbids it.
* Stage 2's ref invalidation belongs at the revocation call sites (`set_member_status`,
  `set_member_role`), NOT inside `_member_cache_keys`: that helper runs for every member
  of the license, so a role edit would wipe everyone's conversation context.
* Stage 4 must re-resolve a cached display name against this license's current rows
  before it enters the prompt, or send the code and no name.
* Stage 6's context road is unreachable on the dominant failure mode unless it is also
  consulted on the "gate passed, no handler matched" path.

## The number this is all for

Measured over 1,165 corpus utterances through the real router:

```
customer     model  12 · rule 332   ( 3% read)
sales        model 147 · rule 390   (27% read)
technician   model  51 · rule 233   (18% read)
```

The Customer OA is the worst and the one real customers use. Its whole free-text road
is one catch-all that treats anything unrecognised as a fault report.
