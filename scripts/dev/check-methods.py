import re, sys
from pathlib import Path
sys.path.insert(0, "application")
from chann_app.data_client import DataClient

real = {n for n in dir(DataClient) if not n.startswith("_")}

used = set()
for f in Path("application/chann_app").rglob("*.py"):
    if f.name == "data_client.py":
        continue
    used |= set(re.findall(r"\bclient\.([a-z_][a-z0-9_]*)\(", f.read_text()))

missing = sorted(m for m in used if m not in real)
print(f"{len(used)} client methods called across the Application Tier")
if missing:
    print("\nCALLED BUT NOT DEFINED ON DataClient:")
    for m in missing:
        print(f"  {m}")
else:
    print("every client method called actually exists")

# And the reverse, for the fake used in tests.
fake_src = Path("tests/unit/test_phase6_chat.py").read_text()
fake = set(re.findall(r"^    async def ([a-z_][a-z0-9_]*)\(", fake_src, re.M))
gap = sorted(m for m in used if m in real and m not in fake)
print(f"\n{len(gap)} client methods the chat fake does NOT implement")
print("  (fine unless chat.py reaches one of them in a tested path)")
for m in gap[:12]:
    print(f"  {m}")
