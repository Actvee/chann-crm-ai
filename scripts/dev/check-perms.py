import re, sys
from pathlib import Path
sys.path.insert(0, "data")
from chann_data.permissions import PERMISSION_KEYS

used = set()
for f in [
    Path("application/chann_app/services/chat.py"),
    Path("application/chann_app/routers_phase2.py"),
]:
    text = f.read_text()
    used |= set(re.findall(r'principal\.require\("([a-z_.]+)"\)', text))
    for group in re.findall(r'principal\.require_any\(([^)]*)\)', text):
        used |= set(re.findall(r'"([a-z_.]+)"', group))
    used |= set(re.findall(r'"([a-z_]+\.[a-z_]+)" (?:not )?in set\(permission_keys\)', text))
    used |= set(re.findall(r'permissions\.has\("([a-z_.]+)"\)', text))

for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    used |= set(re.findall(r'permissions\.has\("([a-z_.]+)"\)', text))
    # The sales pages' gate helper (permission AND the shop not suspended).
    used |= set(re.findall(r'\bcan\("([a-z_.]+)"\)', text))

unknown = sorted(k for k in used if k not in PERMISSION_KEYS)
print(f"checked {len(used)} permission keys in use")
if unknown:
    print("\nNOT IN THE CATALOGUE:")
    for k in unknown:
        print(f"  {k}")
else:
    print("every permission key in use exists")
