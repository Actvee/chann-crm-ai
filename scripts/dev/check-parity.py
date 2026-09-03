"""Chat and dashboard must be able to do the same things.

The owner's rule, stated 2 Sep after finding notes writable only from
chat and appointments editable only from neither: "สิ่งที่ทำได้ในแชท
ต้องทำได้ใน UI และที่ทำได้ใน UI ต้องทำในแชทได้ด้วย".

Every gap this has found so far was invisible from either side alone —
each surface looked complete on its own. So the check is mechanical:
take the capabilities chat routes (ACTION_PERMISSIONS, the canonical
list) and the HTTP calls the dashboard makes, map both onto
(entity, action), and print what only one side can do.

This is a heuristic, deliberately. It reads URLs and methods, so it
cannot see that a button is disabled by a status rule, and it maps by
convention rather than by proof. Findings are a prompt to look, not a
verdict — the same footing as every other script in this folder.
Anything genuinely one-sided on purpose goes in ACCEPTED below with the
reason, so the list stays at zero and a new gap is visible immediately.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, "application")
from chann_app.services.chat import ACTION_PERMISSIONS  # noqa: E402

# Deliberately one-sided, with the reason. Keeping these here rather than
# silently skipping them means the next person sees the decision.
ACCEPTED = {
    ("member", "create"): (
        "both — technician-teams/{id}/members is team membership, which chat does as "
        "\"เพิ่ม <ชื่อ> เข้าทีม <ทีม>\" (registered as update/team)"
    ),
    ("team", "delete"): (
        "both — SalesTeams.tsx sends DELETE technician-teams/{id}; the checker reads the "
        "id segment as the entity, and chat's \"ลบทีมช่าง <ทีม>\" is the same call"
    ),
    ("member", "delete"): (
        "both — \"เอา <ชื่อ> ออกจากทีม <ทีม>\" in chat is the same removal (update/team)"
    ),
    ("warranty", "claim"): (
        "both — chat's ลงทะเบียนสินค้า on the customer OA calls the same "
        "warranties/claim the customer home posts to (owner rule, 3 Sep: the shop "
        "records the unit, the customer claims it by serial)"
    ),
    ("audit_log", "read"): "chat only — a compliance trail is read in the dashboard's own screen",
    ("member", "read"): "both, via the roles/members screen",
    ("member", "update"): "both, via the roles/members screen",
    ("report", "read"): "chat only — the dashboard has its own reports pages",
    ("role", "create"): "dashboard has the roles screen; chat routes the same intents",
    ("role", "read"): "dashboard has the roles screen",
    ("role", "update"): "dashboard has the roles screen",
    ("sales_group", "create"): "dashboard has the groups screen",
    ("sales_group", "read"): "dashboard has the groups screen",
    ("sales_group", "update"): "dashboard has the groups screen",
    ("team", "create"): "dashboard has the teams screen",
    ("team", "read"): "dashboard has the teams screen",
    ("team", "update"): "dashboard has the teams screen",
    ("setting", "read"): "dashboard has the settings screen",
    ("setting", "update"): "dashboard has the settings screen",
    ("customer", "archive"): "chat only by design — archiving from a list tap is too easy to do by accident",
    ("deal", "archive"): "chat only by design — same reason as customer archive",
    ("warranty", "update"): "chat only — a customer registers and reads from the home screen; corrections are staff work in chat",
    ("service_report", "create"): "created by checking in; both surfaces do that",
    ("followup", "cancel"): "the dashboard cancels via PATCH status — same operation, different verb",
    ("line_item", "read"): "chat shows a quote's lines inside the quote detail, not as its own command",
    ("role", "delete"): "dashboard only — deleting a role is an admin screen job, with the members list in view",
    ("setting", "create"): "the settings screen upserts; chat updates the same keys",
    # Phase 14: a customer answers the survey from the quick reply chat
    # pushes and from the home-screen card; there is no staff permission
    # behind it, so it is not an (action, entity) in ACTION_PERMISSIONS.
    ("survey", "read"): "customer home card; chat pushes the survey itself",
    ("survey", "update"): "customer answers in chat (quick reply) and on the home card",
    # PUT approval-workflows replaces the whole flow (the Data Tier retires
    # the old row); there is no separate create, and chat's ตั้งการอนุมัติ
    # is the same replace — registered as ("update", "approval").
    ("approval", "create"): "PUT replaces the flow; both surfaces do that as 'update'",
}

# Real gaps, planned rather than accepted. Listed separately so the
# accepted list stays a statement of intent and this stays a backlog —
# collapsing the two would let a genuine gap hide behind a reason.
KNOWN_GAPS = {
    ("ticket", "assign"): "assigning is chat-only; the dashboard lists tickets read-only",
    ("ticket", "close"): "closing is chat-only, same reason",
    ("ticket", "update"): "editing a ticket is chat-only, same reason",
    ("product", "delete"): "the catalogue screen upserts but cannot remove; no Application route for it either",
}

# URL fragment -> entity. Longest match wins, so "quotes/X/products"
# resolves to line_item rather than quote.
URL_ENTITIES = [
    ("approval-workflows", "approval"),
    ("service-reports", "service_report"),
    ("approvals", "approval"),
    ("surveys", "survey"),
    ("follow-ups", "followup"),
    ("audit", "audit_log"),
    ("sales-groups", "sales_group"),
    ("warranties", "warranty"),
    ("products", "product"),
    ("customers", "customer"),
    ("settings", "setting"),
    ("members", "member"),
    ("tickets", "ticket"),
    ("reports", "report"),
    ("quotes", "quote"),
    ("teams", "team"),
    ("roles", "role"),
    ("deals", "deal"),
    ("notes", "note"),
]

METHOD_ACTIONS = {"GET": "read", "POST": "create", "PATCH": "update",
                  "PUT": "update", "DELETE": "delete"}

# A trailing verb in the path IS the action: POST .../tickets/X/claim is
# claiming a ticket, not creating one. Reading the method alone got every
# one of these wrong on the first run of this script.
VERB_SEGMENTS = {
    "claim": "claim", "close": "close", "assign": "assign",
    "promote": "promote", "archive": "archive", "void": "update",
    "issue": "update", "status": "update", "check-in": "check_in",
    "check-out": "check_out", "link": "read", "preview": "read",
    "publish": "update", "reopen": "update",
    # Phase 14: acting on an approval step; answering a survey is the
    # customer updating their own row.
    "approve": "approve", "reject": "reject", "answer": "update",
    "pending": "read",
    # Phase 13.4: POST .../document issues (or returns) the report PDF.
    "document": "issue",
}


def _clean(url: str) -> str:
    """A template URL reduced to path segments.

    `${encodeURIComponent(x.trim())}` is why the first version misread the
    product screen: the brace never closes inside the captured text, so
    the placeholder survived into the segment list and made a catalogue
    URL look like a nested line-item one.
    """
    url = re.sub(r"\$\{[^}]*\}", "X", url)
    url = re.sub(r"\$\{.*", "X", url)
    return url.split("?")[0]


def _entity_and_action(url: str, method: str) -> tuple[str | None, str]:
    segments = [s for s in _clean(url).split("/") if s and s != "X"]
    verb = VERB_SEGMENTS.get(segments[-1]) if segments else None
    if verb:
        segments = segments[:-1]
    action = verb or METHOD_ACTIONS[method]

    entity = None
    for segment in reversed(segments):
        for fragment, name in URL_ENTITIES:
            if segment == fragment:
                entity = name
                break
        if entity:
            break
    # products nested under a deal or a quote are that record's line
    # items; products on their own are the catalogue.
    if entity == "product" and ("deals" in segments or "quotes" in segments):
        entity = "line_item"
    # Checking in and out happens on a ticket URL but IS the service
    # report — the same operation under two nouns, and reading the URL
    # alone reports it as a gap on both sides at once.
    if action in ("check_in", "check_out"):
        entity = "service_report"
    return entity, action


def dashboard_capabilities() -> set[tuple[str, str]]:
    """(entity, action) pairs the dashboard exercises over HTTP."""
    found: set[tuple[str, str]] = set()
    for path in Path("presentation/app").rglob("*.tsx"):
        text = path.read_text()
        # The context window is a LOOKAHEAD, not a consuming group: two
        # fetches in one Promise.all sit closer than 400 chars apart, and
        # the first match's tail was swallowing the second call whole —
        # /warranties/mine simply vanished from the scan.
        for match in re.finditer(
            r"/api/phase2/([A-Za-z0-9_\-/${}.()\[\]]*)(?=(.{0,400}))", text, re.S,
        ):
            tail = match.group(2)
            explicit = re.search(r'method:\s*"(GET|POST|PATCH|PUT|DELETE)"', tail)
            if explicit:
                methods = [explicit.group(1)]
            elif re.search(r"method:\s*[a-zA-Z]", tail):
                # A computed method (editing ? "PATCH" : "POST") — both.
                methods = ["PATCH", "POST"]
            else:
                methods = ["GET"]
            for method in methods:
                entity, action = _entity_and_action(match.group(1), method)
                if not entity:
                    continue
                found.add((entity, action))
                if method == "PUT":
                    # PUT on this API is upsert, which is both.
                    found.add((entity, "create"))
    return found


chat = {(entity, action) for (action, entity) in ACTION_PERMISSIONS}
dash = dashboard_capabilities()

chat_only = sorted(
    pair for pair in chat - dash if pair not in ACCEPTED and pair not in KNOWN_GAPS
)
# The dashboard doing something chat cannot is the other half of the rule.
dash_only = sorted(
    pair for pair in dash - chat if pair not in ACCEPTED and pair not in KNOWN_GAPS
)

print(f"chat routes {len(chat)} capabilities; the dashboard exercises {len(dash)}")
print(f"{len(ACCEPTED)} pairs accepted as one-sided on purpose")
print(f"{len(KNOWN_GAPS)} known gaps on the backlog:")
for (entity, action), why in sorted(KNOWN_GAPS.items()):
    print(f"  {entity}.{action:9} — {why}")

if chat_only or dash_only:
    if chat_only:
        print("\nIN CHAT BUT NOT IN THE DASHBOARD:")
        for entity, action in chat_only:
            print(f"  {entity}.{action}")
    if dash_only:
        print("\nIN THE DASHBOARD BUT NOT IN CHAT:")
        for entity, action in dash_only:
            print(f"  {entity}.{action}")
    print("\nEither build the missing side or add it to ACCEPTED with a reason.")
else:
    print("every capability is reachable from both surfaces")
