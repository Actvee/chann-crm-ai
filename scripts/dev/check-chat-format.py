"""Every _t(X, lang).format(...) must supply every {placeholder} in X.

A missing one raises KeyError at runtime — which is exactly what happened
with DEAL_PRODUCT_ADDED and {deal_id}, caught only by running it.
"""
import ast
import re
from pathlib import Path
import sys

sys.path.insert(0, "application")
import chann_app.services.chat as chat
import chann_app.services.registration as reg

problems = []
checked = {}
for module in (chat, reg):
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    templates = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    holes = set()
                    for value in node.value.values:
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            holes |= set(re.findall(r"\{(\w+)\}", value.value))
                    if holes:
                        templates[target.id] = holes

    # _t(NAME, language).format(a=..., b=...)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"):
            continue
        inner = node.func.value
        if not (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_t"):
            continue
        if not inner.args or not isinstance(inner.args[0], ast.Name):
            continue
        name = inner.args[0].id
        if name not in templates:
            continue
        supplied = {kw.arg for kw in node.keywords if kw.arg}
        missing = templates[name] - supplied
        if missing:
            problems.append(
                f"{module.__name__}:{node.lineno}  {name} needs {sorted(missing)}"
            )

    checked[module.__name__] = len(templates)

print("checked:", checked)
if problems:
    print("\nFORMAT CALLS MISSING A PLACEHOLDER:")
    for p in problems:
        print("  " + p)
else:
    print("every format call supplies what its template needs")
