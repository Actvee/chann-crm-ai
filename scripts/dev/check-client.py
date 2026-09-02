import re, sys
from pathlib import Path
sys.path.insert(0, 'data')
from chann_data.main import app

routes = []
for r in app.routes:
    p = getattr(r, "path", "")
    if p.startswith("/internal/v1/"):
        methods = getattr(r, "methods", set()) or set()
        routes.append((p, methods, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$")))

src = Path("application/chann_app/data_client.py").read_text()
calls = []
for m in re.finditer(
    r'self\._client\.(get|post|patch|put|delete)\(\s*\n?\s*f?"([^"]*)"(?:\s*\n?\s*f?"([^"]*)")?',
    src,
):
    verb = m.group(1).upper()
    url = (m.group(2) or "") + (m.group(3) or "")
    url = url.replace("{self._base}", "")
    url = re.sub(r"\{[^}]*\}", "X", url)
    calls.append((verb, url))

bad = []
for verb, url in calls:
    probe = url.replace("X", "placeholder")
    hit = [p for p, methods, rx in routes if rx.match(probe) and verb in methods]
    if not hit:
        any_path = [p for p, _, rx in routes if rx.match(probe)]
        bad.append((verb, url, "wrong method" if any_path else "no such route"))

print(f"checked {len(calls)} client calls against {len(routes)} data routes")
if bad:
    print("\nMISMATCH:")
    for verb, url, why in bad:
        print(f"  {verb:6} {url}   -> {why}")
else:
    print("every client call maps to a Data Tier route with the right method")
