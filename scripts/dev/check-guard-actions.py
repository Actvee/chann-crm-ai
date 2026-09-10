"""Every write on the rule road passes the intent guard, and every guard
call names an action the guard knows.

Two failures shipped together and hid each other (10 ก.ย. 2569):

  * the guard was opt-in per call site, so a new handler was unguarded by
    default — measured, 28 of 47 corpus failures were a rule deciding
    before the model, and 23 of them wrote to the database; and
  * `intent_to_act` returned ACT for an action with no vocabulary, so a
    call site could LOOK guarded and do nothing at all. `company_update`
    and `customer_bulk` had entries in both tables and no call site; other
    branches called nothing.

The second now raises at the call. This script catches the FIRST kind of
drift — an action with wording nobody calls, a call naming an action with
no wording, vocabulary that would fail open — which is what let
`company_update` and `customer_bulk` sit in both tables with no call site
at all.

What it does NOT catch, and no static check here can: a NEW handler that
writes and never calls the guard. Nothing in the tables mentions it, so
there is nothing to be inconsistent with. That case is pinned by
tests/unit/test_unguarded_writes.py, which pairs every guarded branch —
the declining sentence must write nothing, the plain command must still
write — and by the corpus in tests/unit/chat_corpus.py.
"""
import ast
import re
import sys
from pathlib import Path

CHAT = Path("application/chann_app/services/chat.py")
GUARD = Path("application/chann_app/services/intent_guard.py")

chat_src = CHAT.read_text(encoding="utf-8")
guard_src = GUARD.read_text(encoding="utf-8")


def _dict_keys(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        targets = getattr(node, "targets", []) or ([node.target] if hasattr(node, "target") else [])
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
            isinstance(t, ast.Name) and t.id == name for t in targets
        ):
            value = node.value
            if isinstance(value, ast.Dict):
                return {
                    k.value for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    return set()


action_words = _dict_keys(guard_src, "ACTION_WORDS")
wording = _dict_keys(chat_src, "_GUARD_ACTIONS")
problems = []

if not action_words or not wording:
    print("PROBLEMS:\n  could not read ACTION_WORDS or _GUARD_ACTIONS")
    sys.exit(1)

# 1. Every action named at a GUARD call site must be renderable. Found via
#    the syntax tree, not a regex: `action=` is an ordinary keyword all
#    over this module and only the guard's own calls are in scope.
GUARD_CALLS = ("_intent_guard_reply", "_guarded_in_context")
called: set[str] = set()
supplies_triggers: set[str] = set()
for node in ast.walk(ast.parse(chat_src)):
    if not isinstance(node, ast.Call):
        continue
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name not in GUARD_CALLS:
        continue
    kwargs = {k.arg: k.value for k in node.keywords if k.arg}
    action = kwargs.get("action")
    if isinstance(action, ast.Constant) and isinstance(action.value, str):
        called.add(action.value)
        if "triggers" in kwargs:
            supplies_triggers.add(action.value)
# A guard call inside a loop names its action in a (name, triggers) tuple
# rather than at the call — e.g. the three job decisions, which share one
# check. Precise enough to be safe: the second element must be a TRIGGERS
# constant.
called |= set(re.findall(r'\("([a-z_]+)", [A-Z_]+TRIGGERS\)', chat_src))

# The AI road dispatches by table rather than by a literal at the call.
for name in ("_AI_GUARDED",):
    for node in ast.walk(ast.parse(chat_src)):
        targets = getattr(node, "targets", []) or ([node.target] if hasattr(node, "target") else [])
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
            isinstance(t, ast.Name) and t.id == name for t in targets
        ) and isinstance(node.value, ast.Dict):
            called |= {
                v.value for v in node.value.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            }
for action in sorted(called - wording):
    problems.append(f"{action} is used at a call site but has no _GUARD_ACTIONS wording")

# 2. Every action with wording must have words to look for, or the guard
#    silently passes everything.
for action in sorted(wording - action_words):
    if action in supplies_triggers:
        continue  # its call site passes the handler's own trigger tuple
    problems.append(f"{action} has wording but no ACTION_WORDS entry — the guard would fail open")

# 3. Vocabulary nobody calls is a promise the product does not keep.
for action in sorted(action_words - called):
    if action in ("record_delete", "pending_flow"):
        continue  # dispatched by table lookup (_AI_GUARDED / the delete fallback)
    if action in supplies_triggers:
        continue
    problems.append(f"{action} has vocabulary but no call site — nothing is guarded by it")

print(f"checked {len(called)} guard call sites against {len(action_words)} action vocabularies")
if problems:
    print("\nPROBLEMS:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("every guard call names a known action, and every action is called")
