import re
from pathlib import Path

def declared(path):
    text = Path(path).read_text(encoding="utf-8")
    found, stack = set(), []
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"^(\w+):\s*\{", s)
        if m:
            # A one-line object — status: { done: "…", pending: "…" }, —
            # opens and closes here. Pushing a level for it desynced the
            # stack for every key after it in the file, so a third of the
            # dictionary was reported as undeclared and two streams had to
            # work around the noise (9 Sep 2026). Record its members and
            # stay at this level.
            if s.count("{") == 1 and s.count("}") >= 1:
                prefix = stack + [m.group(1)]
                for inner in re.finditer(r'(\w+):\s*["\'`]', s):
                    found.add(".".join(prefix + [inner.group(1)]))
                continue
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
# The admin console has its own dictionary (lib/admin-copy.ts) and uses `t`
# as the loop variable over tenants, so every t.status / t.members there is a
# tenant field, not a missing translation. Scanning it produced fourteen
# permanent false positives that trained everyone to ignore this report.
for f in Path("presentation/app").rglob("*.tsx"):
    if "app/admin/" in f.as_posix():
        continue
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
