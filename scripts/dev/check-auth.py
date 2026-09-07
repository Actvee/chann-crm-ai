"""Every tenant-scoped Application route must check the tenant AND a
permission. A route that resolves a principal but never calls .require()
is readable by any member of any role."""
import ast
from pathlib import Path

# Routes guarded by something other than a permission key, with the reason
# (mirrors TestEveryTenantRouteIsGuarded.EXEMPT in tests/boundary).
EXEMPT = {
    "/licenses/{license_id}/me/permissions": "returns the caller's own keys",
    "/licenses/{license_id}/ownership-transfers": "checks principal.is_owner (request) / filters to the parties (list)",
    "/licenses/{license_id}/ownership-transfers/{transfer_id}/accept": "the data tier verifies the caller is the recipient",
    "/platform/licenses/{license_id}/break-glass/transfer-owner": "platform admin route with its own check",
}

source = Path("application/chann_app/routers_phase2.py").read_text(encoding="utf-8")
tree = ast.parse(source)

problems = []
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    paths = [
        d.args[0].value
        for d in node.decorator_list
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.value.__class__.__name__ == "Name"
        and getattr(d.func.value, "id", "") == "router"
        and d.args and isinstance(d.args[0], ast.Constant)
    ]
    if not paths or paths[0] in EXEMPT:
        continue
    body = ast.dump(node)
    scoped = "{license_id}" in paths[0]
    has_tenant = "_require_same_tenant" in body
    # require_any: one of several keys (review C9, 6 Sep 2026).
    has_require = any(
        marker in body
        for marker in ("'attr': 'require'", "attr='require'", "'attr': 'require_any'", "attr='require_any'")
    )

    if scoped and not has_tenant:
        problems.append(f"{paths[0]}  ({node.name}) — no tenant check")
    if scoped and not has_require:
        problems.append(f"{paths[0]}  ({node.name}) — no permission check")

print(f"scanned {sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))} functions")
if problems:
    print("\nUNGUARDED:")
    for p in sorted(set(problems)):
        print("  " + p)
else:
    print("every tenant-scoped route checks the tenant and a permission")
