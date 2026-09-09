# Real-model acceptance

`run.py` always stays offline, including when a real key exists in the environment.
`evaluate-model.py` is a **separate, opt-in, parser-only** evaluation. It calls the
shipped `parse_intent`, prompt builder and OpenRouter client. It does not create
CRM records or send LINE messages. The corpus contains synthetic Thai/English
messages, negation, questions, typo-like phrasing, incomplete information, pending
slot context, role restrictions and prompt injection probes.

```bash
python scripts/agent-test/evaluate-model.py --output /tmp/model-dry-run.json
# In the approved runtime where OPENROUTER_API_KEY and OPENROUTER_MODEL
# are already configured; never paste keys in the prompt, source or report:
python scripts/agent-test/evaluate-model.py --run --limit 5 --repeat 1 --output /tmp/model-smoke.json
python scripts/agent-test/evaluate-model.py --run --repeat 3 --output /tmp/model-acceptance.json
```

Default: dry run, **not** an acceptance pass. There are 49 cases, 147 evaluations
at three repeats, at most 294 HTTP attempts with the current two-attempt client.
The client retains its configured provider policy, temperature 0, thinking off,
1024 output-token limit and 10-second per-attempt timeout. HTTP attempts and
provider-returned token usage/cost are recorded. Missing cost is unknown. No price
estimate is hard-coded. Execution is sequential and may take several minutes.

Exit codes: 0 for valid dry run or all evaluated assertions passing; 1 for any
model mismatch/unavailability; 2 for invalid corpus/configuration. Reports carry
the model, Git HEAD, application diff hash, corpus hash, prompt hashes, each
expected/actual intent, latency and varying outputs across repetitions.

Review semantic expectations before calling a new model: `model-cases.json` is a
challenge suite, not evidence of achieved accuracy. Especially review ambiguous
names, permission-denied responses and wrong-slot continuation. A variation in
irrelevant JSON or suggestions can flag output variation without changing the
business outcome. Do not weaken an expectation merely to improve a score.

Acceptance requires **both** this parser evaluation and a separate end-to-end
DEV run with the same model/configuration, synthetic tenant/identities, correct OA
and role, real Application → Data → DB read-back, and authenticated UI. The parser
context has role, permissions and pending slots; it has no separate OA argument.
This script does not prove OA routing, authorization enforcement, DB transactions,
LINE delivery, scheduling, ad-hoc reports/reasoning model, PDF rendering or UI.

For each failed model case: keep the original message, preserve expected business
behavior, add a deterministic downstream regression, fix the narrow handler or
prompt, then repeat the unchanged corpus. Require no unintended writes for
negations/questions/cross-role requests and no new regression against the prior
release. Report actual counts by category and p50/p95; do not call the offline
scenario pass rate model accuracy.
