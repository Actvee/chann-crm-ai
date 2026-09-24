import re, sys
from pathlib import Path
sys.path.insert(0, 'application')
from chann_app.main import app

# Application routes as regexes
routes = []
for r in app.routes:
    p = getattr(r, "path", "")
    if p.startswith("/api/v1/"):
        routes.append((p, re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$")))

called = set()
for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    for m in re.finditer(r"/api/phase2/([A-Za-z0-9_\-/${}.\[\]]*)", text):
        url = m.group(1)
        url = re.sub(r"\$\{[^}]*\}", "X", url).split("?")[0].rstrip("/")
        if url:
            called.add((url, f.name))

missing = []
for url, where in sorted(called):
    probe = "/api/v1/" + url.replace("X", "placeholder")
    if not any(rx.match(probe) for _, rx in routes):
        missing.append((url, where))

print(f"checked {len(called)} distinct dashboard calls")
if missing:
    print("\nNO MATCHING APPLICATION ROUTE:")
    for url, where in missing:
        print(f"  {url}   ({where})")
else:
    print("every dashboard call maps to an Application Tier route")

# Round 21D (spec §10): every ext route is behind api_principal, and
# api_principal itself must refuse on plan — one line that a refactor could
# silently drop and no single route test would notice.
api_key_src = Path("application/chann_app/auth/api_key.py").read_text()
if 'require_feature("feature.external_api")' not in api_key_src:
    print('\nauth/api_key.py: api_principal no longer calls require_feature("feature.external_api")')
    raise SystemExit(1)
print("api_principal refuses on plan")
