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

# ---------------------------------------------------------------- round 21D
# The plan family (spec §10): a feature./quota./limit. key anywhere in code
# must be one of the ten in chann_data/plans.py, and every permission key
# must be either in a PERMISSION_FEATURE family or named in
# ALWAYS_ON_PERMISSIONS — a new key has to be classified, not forgotten.
sys.path.insert(0, "application")
from chann_data.plans import ENTITLEMENT_KEYS  # noqa: E402
from chann_app.services.entitlements import ALWAYS_ON_PERMISSIONS, feature_of  # noqa: E402

PLAN_KEY = r"((?:feature|quota|limit)\.[a-z_]+)"
plan_used: dict[str, str] = {}
python_files = [*Path("application/chann_app").rglob("*.py"), *Path("data/chann_data").rglob("*.py")]
for f in python_files:
    text = f.read_text()
    for pattern in (rf'require_feature\("{PLAN_KEY}"', rf'entitled\([^,]+,\s*"{PLAN_KEY}"',
                    rf'\.has\("{PLAN_KEY}"\)', rf'_plan_has\("{PLAN_KEY}"\)', rf'_plan_refusal\("{PLAN_KEY}"'):
        for key in re.findall(pattern, text):
            plan_used.setdefault(key, str(f))
for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    for pattern in (rf'feature: "{PLAN_KEY}"', rf'planHas\([^,()]+,\s*"{PLAN_KEY}"\)'):
        for key in re.findall(pattern, text):
            plan_used.setdefault(key, str(f))

problems = [f"  {key}   ({where}) — not one of the ten plan keys"
            for key, where in sorted(plan_used.items()) if key not in ENTITLEMENT_KEYS]
problems += [f"  {key} — in no PERMISSION_FEATURE family and not in ALWAYS_ON_PERMISSIONS"
             for key in sorted(PERMISSION_KEYS) if feature_of(key) is None and key not in ALWAYS_ON_PERMISSIONS]
print(f"checked {len(plan_used)} plan keys in use")
if problems:
    print("\nPLAN KEYS:")
    print("\n".join(problems))
    raise SystemExit(1)
print("every plan key in use is one of the ten · every permission key is classified")
