import re
from pathlib import Path

# {n} / {count} / {code} in an i18n string must be filled by a .replace,
# or it prints literally on screen.
def strings(path):
    out = {}
    text = Path(path).read_text(encoding="utf-8")
    stack = []
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"^(\w+):\s*\{", s)
        if m:
            stack.append(m.group(1)); continue
        if s.startswith("}"):
            if stack: stack.pop()
            continue
        m = re.match(r'^(\w+):\s*"((?:[^"\\]|\\.)*)"', s)
        if m:
            out[".".join(stack + [m.group(1)])] = m.group(2)
    return out

th = strings("presentation/lib/i18n/th.ts")
en = strings("presentation/lib/i18n/en.ts")

code = "\n".join(
    f.read_text(encoding="utf-8") for f in Path("presentation/app").rglob("*.tsx")
)

problems = []
for key, value in th.items():
    holes = set(re.findall(r"\{(\w+)\}", value))
    if not holes:
        continue
    # Is the key used with a .replace at all?
    if f"t.{key}" not in code:
        continue
    for hole in holes:
        if f'"{{{hole}}}"' not in code and f"'{{{hole}}}'" not in code:
            problems.append(f"{key}: {{{hole}}} is never replaced")
    # And the two locales must agree, or one language prints a literal.
    en_holes = set(re.findall(r"\{(\w+)\}", en.get(key, "")))
    if key in en and holes != en_holes:
        problems.append(f"{key}: th has {sorted(holes)}, en has {sorted(en_holes)}")

print(f"checked {sum(1 for v in th.values() if '{' in v)} strings with placeholders")
if problems:
    print("\nPROBLEMS:")
    for p in problems:
        print("  " + p)
else:
    print("every placeholder is filled and matches across locales")
