# review v3 — the probes, kept as evidence

These two files are the probes from the v3 review
(`Chann-CRM-AI-Review-v3/review-v3/`), kept so anyone can re-run the
review's own assertions against the fixed tree rather than taking a
summary's word for it:

```
python -m pytest scripts/agent-test/scenarios/review-v3 -q
```

They are **not** the gate. The maintained regression tests for the same
findings live where the suite actually runs them:

| finding | where it is tested now |
| --- | --- |
| T01 — a redelivered LINE event | `tests/unit/test_review_v3_hardening.py`, `tests/integration/test_integrity_h2.py` |
| T02 — an uploaded template is filtered | `tests/unit/test_review_v3_hardening.py`, `tests/unit/test_document_templates_docx.py` |
| T03 — CSS escapes | `tests/unit/test_review_v3_hardening.py` |
| T04 — DOCX expansion budget | `tests/unit/test_review_v3_hardening.py` |
| T05 — the documented command | `tests/unit/test_agent_test_channel.py`, `tests/unit/conftest.py` |
| B08 — the design conversation | `tests/unit/test_template_design.py` |
| S01 — two active templates | `tests/integration/test_template_activation_race.py` |

One deliberate change from what the review delivered: `WebhookStore` in
`test_technical_review.py` implements the claim/finish protocol the
Application tier now calls, instead of the original three-line "have I
seen this id?". Every assertion is the review's, unchanged.

`scripts/agent-test/run.py` does not see this directory — `discover()`
reads one level of `scenarios/` and only `.yaml`/`.yml`/`.json`, so the
scenario count is unaffected.
