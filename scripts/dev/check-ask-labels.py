"""Every field the system can ask a person for must have words for it.

`ask_for_missing` joins MISSING_FIELD_LABELS entries. A field name that is
not in that table used to be printed exactly as it appears in the AI's
JSON — "กรุณาระบุservice_address", "กรุณาระบุdue_time",
"กรุณาระบุteam_name" — each one reported by the owner from live use, each
one fixed by hand, each time leaving the next one to be found the same
way. The table is hand-kept; nothing checked it. This does.

Scope: field names the CODE itself puts into `missing` (a literal
`missing=[...]` or `"missing": [...]` in the application tier). What the
model invents at runtime cannot be checked here — ask_for_missing folds an
unknown key into "รายละเอียดที่เหลือ" instead of printing it.
"""
import ast
import re
import sys
from pathlib import Path

APP = Path("application/chann_app")
CHAT = APP / "services" / "chat.py"

source = CHAT.read_text(encoding="utf-8")

# The labels table, read as a literal so this never imports the app.
tree = ast.parse(source)
labels: set[str] = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "MISSING_FIELD_LABELS" for t in node.targets
    ):
        for key in node.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                labels.add(key.value)

if not labels:
    print("PROBLEMS:\n  MISSING_FIELD_LABELS could not be read from chat.py")
    sys.exit(1)

# Every field name the code itself can leave outstanding.
asked: dict[str, list[str]] = {}
pattern = re.compile(r'(?:missing=|"missing":\s*)\[([^\]]*)\]')
for path in sorted(APP.rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.split("\n"), start=1):
        for match in pattern.finditer(line):
            for raw in match.group(1).split(","):
                field = raw.strip().strip('"').strip("'")
                if field and re.fullmatch(r"[a-z][a-z0-9_]*", field):
                    asked.setdefault(field, []).append(f"{path}:{line_no}")

problems = [
    f"{field} has no entry in MISSING_FIELD_LABELS — asked at {sites[0]}"
    for field, sites in sorted(asked.items())
    if field not in labels
]

# Both locales, or one language shows a blank where a field name should be.
for field in sorted(labels):
    node = next(
        (v for n in ast.walk(tree) if isinstance(n, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "MISSING_FIELD_LABELS" for t in n.targets)
         for k, v in zip(n.value.keys, n.value.values)
         if isinstance(k, ast.Constant) and k.value == field),
        None,
    )
    if node is None:
        continue
    locales = {k.value for k in node.keys if isinstance(k, ast.Constant)}
    if not {"th", "en"} <= locales:
        problems.append(f"{field} is missing a locale: has {sorted(locales)}")

print(f"checked {len(asked)} asked fields against {len(labels)} labels")
if problems:
    print("\nPROBLEMS:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("every field the system asks for has words in both languages")
