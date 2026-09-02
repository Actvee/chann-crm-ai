import re, sys
sys.path.insert(0, "application")
import chann_app.services.chat as chat

groups = {}
for name in dir(chat):
    if name.endswith(("_TRIGGERS", "_PHRASES")) and isinstance(getattr(chat, name), tuple):
        groups[name] = [t for t in getattr(chat, name) if isinstance(t, str)]

# Order the dispatcher checks them in, read from the source.
source = open("application/chann_app/services/chat.py").read()
order = []
for m in re.finditer(r"\b([A-Z_]+_(?:TRIGGERS|PHRASES))\b", source[source.index("async def handle_chat_message"):]):
    if m.group(1) not in order:
        order.append(m.group(1))

pos = {name: i for i, name in enumerate(order)}
collisions = []
for a, ta in groups.items():
    for b, tb in groups.items():
        if a >= b:
            continue
        for x in ta:
            for y in tb:
                if x == y or len(x) < 3 or len(y) < 3:
                    continue
                if x in y or y in x:
                    shorter, longer = (x, y) if len(x) < len(y) else (y, x)
                    s_group = a if shorter in ta else b
                    l_group = a if longer in ta else b
                    # A collision only bites when the SHORTER one is checked first.
                    if pos.get(s_group, 999) < pos.get(l_group, 999):
                        collisions.append((shorter, s_group, longer, l_group))

print(f"checked {sum(len(v) for v in groups.values())} triggers across {len(groups)} groups")
if collisions:
    print("\nSHORTER TRIGGER CHECKED BEFORE A LONGER ONE CONTAINING IT:")
    for s, sg, l, lg in sorted(set(collisions)):
        print(f'  "{s}" ({sg})  swallows  "{l}" ({lg})')
else:
    print("no shorter trigger is checked before a longer one containing it")
