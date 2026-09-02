import re
from pathlib import Path

def declared(path):
    text = Path(path).read_text(encoding="utf-8")
    found, stack = set(), []
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"^(\w+):\s*\{", s)
        if m:
            stack.append(m.group(1)); continue
        if s.startswith("}"):
            if stack: stack.pop()
            continue
        m = re.match(r'^(\w+):\s*["\'`]', s)
        if m:
            found.add(".".join(stack + [m.group(1)]))
    return found

keys = declared("presentation/lib/i18n/th.ts")

used = set()
for f in Path("presentation/app").rglob("*.tsx"):
    for m in re.finditer(r"\bt\.([a-zA-Z0-9_.]+)", f.read_text(encoding="utf-8")):
        used.add(re.sub(r"\.(replace|split|join|toLowerCase|toUpperCase|trim|length|map|filter)$", "", m.group(1).rstrip(".")))

# A key referenced in code but never declared renders as undefined.
missing = sorted(
    k for k in used
    if k not in keys
    and not any(d.startswith(k + ".") for d in keys)   # a whole group
)
print(f"{len(keys)} declared, {len(used)} referenced")
if missing:
    print("\nREFERENCED IN CODE BUT NOT DECLARED:")
    for k in missing:
        print("  t." + k)
else:
    print("every referenced key exists")
