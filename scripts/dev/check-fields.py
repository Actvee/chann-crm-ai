import re, sys
from pathlib import Path
sys.path.insert(0, "data")
import chann_data.schemas as S
from pydantic import BaseModel

# Every field name any *Out schema exposes.
known = set()
for name in dir(S):
    obj = getattr(S, name)
    if isinstance(obj, type) and issubclass(obj, BaseModel) and name.endswith("Out"):
        known |= set(obj.model_fields)

# Field names the dashboard's TS types declare for API data.
suspect = []
for f in Path("presentation/app").rglob("*.tsx"):
    text = f.read_text()
    for block in re.finditer(r"type\s+(\w+)\s*=\s*\{(.*?)\n\};", text, re.S):
        tname, body = block.group(1), block.group(2)
        if tname in {"Context", "Detail"}:
            continue
        for fm in re.finditer(r"^\s{2}(\w+)\??:", body, re.M):
            field = fm.group(1)
            if field not in known:
                suspect.append((f.name, tname, field))

print(f"{len(known)} schema field names known")
if suspect:
    print("\nDECLARED IN TS BUT IN NO *Out SCHEMA:")
    for where, tname, field in suspect:
        print(f"  {where:28} {tname}.{field}")
else:
    print("every declared field exists in a schema")
