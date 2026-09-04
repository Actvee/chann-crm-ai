"""Registration through chat — Phase 6.5 (Master Spec 6.5.6).

The entry point for someone the system has never seen. Deliberately does NOT
go through the AI intent parser: registration is a short, closed set of
choices, and burning a model call — plus risking a hallucinated action — to
recognise "1" or an invite code would be worse on latency, cost, and
reliability all at once. The AI takes over once the user is a member.

State lives in the message text rather than a session table. A registration
conversation is two or three turns and LINE gives no reliable session anyway;
inventing server-side state here would mean expiry rules, cleanup, and a new
failure mode for a flow this short.
"""
from __future__ import annotations

import logging
import re

from ..data_client import DataClient
from .identity import ResolvedContext, TenantResolution
from . import pdpa as pdpa_service

log = logging.getLogger(__name__)

# An invite code is 10 chars from a confusable-free alphabet; a company code is
# 8. Matching on shape lets someone paste a code with no command word, which
# is what people actually do.
INVITE_CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{10}$")
CUSTOMER_PENDING_TTL_S = 3600

# Loose on purpose: serial formats vary wildly by manufacturer, and being
# strict would reject real ones. An unknown serial simply finds nothing.
SERIAL_RE = re.compile(r"\b([A-Z0-9][A-Z0-9\-]{5,31})\b", re.IGNORECASE)

COMPANY_CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$")

WELCOME = {
    "th": (
        "ยินดีต้อนรับสู่ Chann CRM\n"
        "คุณยังไม่ได้ผูกกับบริษัทใด เลือกได้ 2 ทาง:\n\n"
        "1) พิมพ์ \"เปิดบริษัทใหม่ <ชื่อบริษัท>\" เพื่อสร้างบริษัทของคุณเอง\n"
        "2) ถ้ามีรหัสเชิญจากเพื่อนร่วมงาน พิมพ์รหัสนั้นได้เลย"
    ),
    "en": (
        "Welcome to Chann CRM\n"
        "This account is not linked to a company yet. Two options:\n\n"
        '1) Type "create company <name>" to start your own\n'
        "2) If a colleague gave you an invite code, just type the code"
    ),
}

# Technician OA has no "create a company" option at all — a technician does
# not start a company through this channel, they join one that already
# exists. Holding a Sales-side membership at Company X does not count either
# (see identity.resolve_context / MemberRepository.memberships_of): only a
# license_members row with role="technician" does, and the only way to get
# one is this invite code, requested by someone on the Sales OA who holds
# member.manage (see TECHNICIAN_INVITE_TRIGGERS in chat.py).
WELCOME_TECHNICIAN = {
    "th": (
        "ยินดีต้อนรับ\n"
        "บัญชีนี้ยังไม่ได้ผูกกับบริษัทไหนในฐานะช่าง\n"
        "ขอรหัสเชิญจากบริษัทที่คุณจะไปทำงานด้วยก่อน แล้วพิมพ์รหัสนั้นที่นี่"
    ),
    "en": (
        "Welcome\n"
        "This account is not yet linked to any company as a technician.\n"
        "Ask the company you'll be working with for an invite code, then "
        "type that code here."
    ),
}

ASK_COMPANY_NAME = {
    "th": 'กรุณาระบุชื่อบริษัท เช่น "เปิดบริษัทใหม่ ร้านสมชายการช่าง"',
    "en": 'Please include the company name, e.g. "create company Somchai Repairs"',
}

# The Customer OA's first words. Owner rule (3 Sep): a customer registers
# the product BEFORE reporting a fault — that is how the shop learns which
# customer and which machine — so the welcome says exactly that, in the
# order the person will do it, and names the escape hatch (shop name or
# code) for someone whose shop never registered the serial.
#
# This constant was referenced before it existed: typing "วิธีใช้" while
# unlinked raised NameError and the person got "ระบบไม่พร้อมใช้งาน".
WELCOME_CUSTOMER = {
    "th": (
        "ยินดีต้อนรับครับ\n\n"
        "ก่อนแจ้งซ่อม ขอลงทะเบียนสินค้าก่อน 1 ครั้ง เพื่อให้ร้านรู้ว่าเป็นเครื่องไหนของใคร:\n"
        "· พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) ถ้าร้านลงทะเบียนไว้ให้แล้ว\n"
        "· หรือพิมพ์ชื่อร้าน / รหัสร้าน 8 หลักที่ร้านให้มา\n\n"
        "หลังจากนั้นพิมพ์อาการที่เสียมาได้เลย"
    ),
    "en": (
        "Welcome.\n\n"
        "Before reporting a fault, register your product once so the shop knows "
        "which machine and whose it is:\n"
        "· type the serial number (S/N on the sticker) if the shop registered it\n"
        "· or type the shop's name, or its 8-character shop code\n\n"
        "After that, just describe the fault."
    ),
}

# Reply to a fault typed before any shop is linked. The old text said
# "บัญชีนี้ดูแลหลายร้าน" — true of the OA, meaningless to the person — and
# then looped, because "ร้าน dev company" never matched a shop named
# "Dev Company" (3 Sep, live).
CUSTOMER_UNLINKED_HELD = {
    "th": (
        "รับเรื่องแล้วครับ: \"{text}\"\n\n"
        "ผมยังไม่ทราบว่าคุณเป็นลูกค้าร้านไหน "
        "พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้าน / รหัสร้าน มาได้เลย "
        "แล้วผมจะแจ้งซ่อมเรื่องนี้ให้ทันที"
    ),
    "en": (
        "Got it: \"{text}\"\n\n"
        "I do not know which shop you are a customer of yet. Type the serial "
        "number (S/N on the sticker) or the shop's name or code, and I will file "
        "this fault straight away."
    ),
}
SERIAL_UNKNOWN_HERE = {
    "th": (
        "ไม่พบหมายเลข {serial} ในระบบครับ อาจเป็นเพราะร้านยังไม่ได้ลงทะเบียนเครื่องนี้\n"
        "พิมพ์ชื่อร้านหรือรหัสร้าน 8 หลักมาได้เลย ผมจะผูกให้แล้วลงทะเบียนเครื่องต่อ"
    ),
    "en": (
        "Serial {serial} is not in the system — the shop may not have registered "
        "it yet. Type the shop's name or 8-character code and I will link you and "
        "register the machine."
    ),
}
LINKED_NEXT_CUSTOMER = {
    "th": "\n\nพิมพ์หมายเลขเครื่อง (S/N) เพื่อลงทะเบียนสินค้า แล้วแจ้งซ่อมได้เลย หรือพิมพ์ \"งานของฉัน\" เพื่อดูสถานะ",
    "en": '\n\nType the serial (S/N) to register your product, then describe any fault. Type "my jobs" to check one.',
}

# Words to strip before searching shops by name: people write the label
# in front of the name ("ร้าน dev company"), the table stores the name.
_SHOP_PREFIXES = ("ร้าน", "บริษัท", "บจก.", "บจก", "หจก.", "หจก", "บ.", "shop", "company", "co.")
# The legal tail people copy off a receipt; the shop's own name is
# what is searched.
_SHOP_SUFFIXES = (
    "จำกัด (มหาชน)", "จำกัด(มหาชน)", "จำกัด", "co., ltd.", "co.,ltd.", "co., ltd",
    "co.,ltd", "co. ltd", "ltd.", "ltd", "limited", "inc.", "inc", "plc",
)


def shop_query(text: str) -> str:
    """The searchable part of "ร้าน dev company": prefixes and punctuation off,
    inner whitespace collapsed."""
    q = (text or "").strip().strip(" \t:：-—\"'“”")
    lowered = q.lower()
    changed = True
    while changed and lowered:
        changed = False
        for prefix in _SHOP_PREFIXES:
            if lowered.startswith(prefix) and len(lowered) > len(prefix):
                q = q[len(prefix):].lstrip(" \t.")
                lowered = q.lower()
                changed = True
        for suffix in _SHOP_SUFFIXES:
            if lowered.endswith(suffix) and len(lowered) > len(suffix):
                q = q[: len(q) - len(suffix)].rstrip(" \t.,")
                lowered = q.lower()
                changed = True
    return " ".join(q.split())

CREATED = {
    "th": (
        "สร้างบริษัท \"{name}\" เรียบร้อย\n"
        "คุณเป็นเจ้าของบริษัทนี้แล้ว\n\n"
        "รหัสร้านของคุณคือ {code}\n"
        "ให้ลูกค้าพิมพ์รหัสนี้เพื่อผูกกับร้านคุณ\n\n"
        "ทดลองใช้ฟรี 30 วัน"
    ),
    "en": (
        'Company "{name}" created — you are its owner.\n\n'
        "Your shop code is {code}\n"
        "Customers type this code to link to your shop.\n\n"
        "30-day free trial."
    ),
}

JOINED = {
    "th": "เข้าร่วม \"{name}\" เรียบร้อย สิทธิ์ของคุณคือ {role}",
    "en": 'Joined "{name}" — your role is {role}',
}

# What to do next, appended at the moment someone is guaranteed to be
# reading. "เข้าร่วมเรียบร้อย" followed by silence leaves a technician
# holding a phone with no idea what to type — which is how a chat-first
# product loses the person it has just onboarded.
JOINED_NEXT = {
    "technician": {
        "th": (
            "\n\nเริ่มใช้งานได้เลย:\n"
            "· \"งานของฉัน\" — ดูงานที่ได้รับมอบหมาย\n"
            "· \"รายการงาน\" — ดูงานที่เปิดรับ\n"
            "· \"เช็คอิน\" — เมื่อถึงหน้างาน\n"
            "· \"ปิดงาน\" — เมื่อทำเสร็จ (ระบบจะถามรายละเอียดให้)\n\n"
            "พิมพ์ \"วิธีใช้\" ได้ตลอดเวลา"
        ),
        "en": (
            "\n\nTo get started:\n"
            '· "my jobs" — what you have been given\n'
            '· "tickets" — what is open to take\n'
            '· "check in" — when you arrive\n'
            '· "check out" — when you are done\n\n'
            'Type "help" any time.'
        ),
    },
    "default": {
        "th": "\n\nพิมพ์ \"วิธีใช้\" เพื่อดูคำสั่งที่ใช้ได้",
        "en": '\n\nType "help" to see what you can do.',
    },
}

LINKED = {
    "th": "ผูกกับร้าน \"{name}\" เรียบร้อย ครั้งต่อไปไม่ต้องพิมพ์รหัสอีก",
    "en": 'Linked to "{name}". You will not need the code again.',
}

BAD_CODE = {
    "th": "ไม่พบรหัสนี้ กรุณาตรวจสอบอีกครั้ง",
    "en": "That code was not found — please check it",
}

ALREADY_HAVE_COMPANY = {
    "th": "บัญชีนี้สร้างบริษัทไปแล้ว หนึ่งบัญชี LINE สร้างได้บริษัทเดียว",
    "en": "This account already created a company — one per LINE account",
}


def _t(table: dict[str, str], language: str) -> str:
    return table.get(language) or table["th"]


# Ordered longest-first so "เปิดบริษัทใหม่ ร้าน ก" strips the full trigger and
# not just "เปิดบริษัท", which would leave "ใหม่" as part of the company name.
#
# "เปิดบริษัท" and "สมัคร" were added after a live test: the menu says
# "เปิดบริษัทใหม่", and the user naturally typed the shorter "เปิดบริษัท",
# which matched nothing and fell through to the menu again. Triggers should
# cover what people actually type, not only the exact wording we printed.
CREATE_TRIGGERS = (
    "เปิดบริษัทใหม่",
    "สร้างบริษัทใหม่",
    "ลงทะเบียนบริษัท",
    "เปิดบริษัท",
    "สร้างบริษัท",
    "สมัครบริษัท",
    "create new company",
    "create company",
    "new company",
    "register company",
)


def parse_create_company(message: str) -> str | None:
    """Return the company name if this is a create request, else None.

    Returns "" (falsy but not None) when the trigger is present without a
    name, so the caller can tell "not a create request" from "a create
    request missing its name" and ask for the name rather than showing the
    whole menu again.
    """
    text = (message or "").strip()
    lowered = text.lower()
    # Longest match wins, so a shorter trigger that is a prefix of a longer one
    # cannot swallow the difference into the company name.
    for trigger in sorted(CREATE_TRIGGERS, key=len, reverse=True):
        if lowered.startswith(trigger.lower()):
            return text[len(trigger):].strip(" \t:：-—")
    return None


def first_contact(oa: str, ctx: ResolvedContext, language: str = "th") -> tuple[str, list[tuple[str, str]]]:
    """What to say on the LINE `follow` event — the moment someone adds an
    OA — as (text, quick replies).

    Before this, a follow event was dropped by the webhook, so adding the
    OA produced nothing at all (3 Sep, all three OAs). Someone already
    linked gets the ordinary greeting; a stranger gets the OA's welcome,
    which for the Customer OA is the register-first instruction.
    """
    if ctx.resolution is not TenantResolution.NONE:
        from .chat import greet  # lazy: chat imports this module

        text = greet(ctx, language)
        quick = {
            "customer": [("แจ้งซ่อม", "แจ้งซ่อม"), ("คุยกับร้าน", "คุยกับร้าน"), ("งานของฉัน", "งานของฉัน"), ("วิธีใช้", "วิธีใช้")],
            "technician": [("งานของฉัน", "งานของฉัน"), ("งานที่เปิดรับ", "งานที่เปิดรับ"), ("วิธีใช้", "วิธีใช้")],
            "sales": [("งานวันนี้", "งานวันนี้"), ("รายชื่อลูกค้า", "รายชื่อลูกค้า"), ("วิธีใช้", "วิธีใช้")],
        }.get(oa, [("วิธีใช้", "วิธีใช้")])
        return text, quick
    if oa == "customer":
        return _t(WELCOME_CUSTOMER, language), [
            ("ลงทะเบียนสินค้า", "ลงทะเบียนสินค้า"), ("วิธีใช้", "วิธีใช้"),
        ]
    if oa == "technician":
        return _t(WELCOME_TECHNICIAN, language), []
    return _t(WELCOME, language), [("เปิดบริษัทใหม่", "เปิดบริษัทใหม่")]


async def handle_registration(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    audience: str = "sales",
    language: str = "th",
):
    """Phase 16.5: nobody registers — links a shop, joins one, creates one —
    before consenting once. The gate asks, holds what they typed, and
    on "ยอมรับ" continues with it; on refusal nothing is stored."""
    gate_reply, carried = await pdpa_service.consent_gate(
        client, chann_uid=ctx.chann_uid, oa=audience, message=message, language=language,
    )
    if gate_reply is not None and carried is None:
        return gate_reply
    reply = await _handle_registration(client, message=carried if gate_reply else message, ctx=ctx, audience=audience, language=language)
    if gate_reply is None:
        return reply
    if isinstance(reply, str):
        return f"{gate_reply}\n\n{reply}" if reply else gate_reply
    reply.text = f"{gate_reply}\n\n{reply.text}" if reply.text else gate_reply
    return reply


async def _handle_registration(
    client: DataClient,
    *,
    message: str,
    ctx: ResolvedContext,
    audience: str = "sales",
    language: str = "th",
):
    """Handle a message from someone with no tenant.

    Returns reply text — or, on the Customer OA when linking also delivers
    a fault the person typed earlier, the ChatReply the report flow
    produced (it carries the follow-up question and buttons). The webhook
    accepts either.
    """
    text = (message or "").strip()

    # Customer OA: the only thing to do is bind to a shop. Offering "create a
    # company" to an end customer would be nonsense.
    if audience == "customer":
        return await _handle_customer(client, text, ctx, language)

    # Technician OA: same reasoning as Customer OA above — the only thing to
    # do is redeem an invite code obtained from the company beforehand.
    # "Create a company" is a Sales-OA-only concept and never offered here.
    if audience == "technician":
        if INVITE_CODE_RE.match(text.upper()):
            return await _redeem_invite_reply(client, text, ctx, language)
        return _t(WELCOME_TECHNICIAN, language)

    if not text:
        return _t(WELCOME, language)

    name = parse_create_company(text)
    if name is not None:
        if not name:
            return _t(ASK_COMPANY_NAME, language)
        try:
            created = await client.create_license(
                company_name=name,
                created_by_chann_uid=ctx.chann_uid,
                display_name=ctx.display_name,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return _t(ALREADY_HAVE_COMPANY, language)
            log.error("create_license failed: %s", exc)
            raise
        return _t(CREATED, language).format(
            name=created.get("company_name", name),
            code=created.get("company_code", "?"),
        )

    if INVITE_CODE_RE.match(text.upper()):
        return await _redeem_invite_reply(client, text, ctx, language)

    return _t(WELCOME, language)


# The bot's own vocabulary. Held and repeated back, these read as the
# customer having described a problem when they asked a question.
_CUSTOMER_COMMAND_WORDS = frozenset({
    "วิธีใช้", "ช่วยเหลือ", "help", "เมนู", "menu", "เริ่ม", "start",
    "แจ้งซ่อม", "แจ้งปัญหา", "เช็คประกัน", "ลงทะเบียนสินค้า",
    "ดูงาน", "งานของฉัน", "สวัสดี", "สอบถาม",
})


def _is_a_command_not_a_message(text: str) -> bool:
    """Is this a command word on its own?

    On its own, deliberately: "แจ้งซ่อม" is a menu tap, but "แจ้งซ่อม
    แอร์ไม่เย็น" is a real report with the trigger in front of it and
    must still be held.
    """
    return (text or "").strip().lower() in _CUSTOMER_COMMAND_WORDS


async def _link_and_continue(
    client: DataClient, ctx: ResolvedContext, *, company_code: str,
    company_name: str = "", serial: str | None = None, language: str = "th",
    license_id: str | None = None,
):
    """Bind the customer to the shop, then do what they came for.

    If a fault was typed before the link existed it is held in the
    pending slot; after linking, that message goes through the real
    report flow (services/chat) so a ticket exists and the address
    question follows — the old code said "ส่งเรื่องให้ทางร้านแล้ว" and had
    written nothing anywhere.
    """
    try:
        link = await client.link_customer(chann_uid=ctx.chann_uid, company_code=company_code)
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc) or _is_conflict(exc):
            return _t(BAD_CODE, language)
        raise
    license_id = str(link.get("license_id") or license_id or "")
    name = company_name or str(link.get("company_name") or "")
    if not name or not license_id:
        # CustomerLinkOut carries the license id, not the name; the shops
        # endpoint has both. Matched by id when we have one, else by the
        # code we just linked with.
        try:
            shops = await client.my_shops(ctx.chann_uid)
            shop = next(
                (s for s in shops
                 if (license_id and str(s.get("license_id")) == license_id)
                 or str(s.get("license_code") or "").upper() == company_code.upper()),
                None,
            )
            if shop:
                name = name or str(shop.get("company_name") or "")
                license_id = license_id or str(shop.get("license_id") or "")
        except Exception:
            log.exception("could not read the linked shop's name")

    linked = _t(LINKED, language).format(name=name)

    # 16.4: the shop's side — a CRM record at once when the shop opted
    # in, a note to CS otherwise. Best effort: the link already stands.
    if license_id:
        try:
            from .onboarding import after_customer_linked

            await after_customer_linked(
                client, license_id=license_id, chann_uid=ctx.chann_uid,
                display_name=ctx.display_name, language=language,
            )
        except Exception:
            log.exception("could not run the new-customer step for %s", ctx.chann_uid)

    try:
        pending = await client.get_pending_intent(ctx.chann_uid, "customer")
    except Exception:
        pending = None
    held = ((pending or {}).get("fields") or {}).get("message")
    if held and (pending or {}).get("entity") == "pending_customer_message" and license_id:
        from .chat import _handle_customer_report  # lazy: chat imports this module

        # The serial that found the shop is the machine the fault is
        # about; the report flow reads it from the same pending slot.
        try:
            await client.set_pending_intent(
                ctx.chann_uid, "customer",
                action="report", entity="pending_customer_message",
                fields={"message": held[:500], "serial": serial or ""}, missing=[],
                ttl_seconds=CUSTOMER_PENDING_TTL_S,
            )
        except Exception:
            log.exception("could not carry a held message into the report flow")
        linked_ctx = ResolvedContext(
            chann_uid=ctx.chann_uid, primary_role=ctx.primary_role,
            display_name=ctx.display_name, resolution=TenantResolution.SINGLE,
            memberships=[{"license_id": license_id, "company_name": name}], oa="customer",
        )
        reply = await _handle_customer_report(
            client, ctx=linked_ctx, license_id=license_id, message=held, language=language,
        )
        reply.text = linked + "\n\n" + reply.text
        return reply

    if serial:
        return linked + (
            f"\n\nเครื่อง {serial} ผูกกับร้านนี้แล้ว พิมพ์อาการที่เสียมาได้เลย หรือพิมพ์ \"งานของฉัน\" เพื่อดูสถานะ"
            if language != "en"
            else f'\n\n{serial} is on file with this shop — describe any fault. Type "my jobs" to check one.'
        )
    return linked + _t(LINKED_NEXT_CUSTOMER, language)


async def _handle_customer(
    client: DataClient, text: str, ctx: ResolvedContext, language: str
):
    if COMPANY_CODE_RE.match(text.upper()):
        return await _link_and_continue(
            client, ctx, company_code=text.upper(), language=language,
        )

    # A command word from someone not yet linked: answer with how this
    # works, never with "got your message" (the bot mistaking its own
    # vocabulary for a customer's problem).
    if _is_a_command_not_a_message(text):
        return _t(WELCOME_CUSTOMER, language)

    # A serial number identifies the shop without the customer knowing
    # which one it is (16.4). This is the case the shop-code question was
    # always a poor substitute for: someone whose air conditioner has
    # failed has the sticker on the machine, not a code they were never
    # given.
    serial_match = SERIAL_RE.search(text)
    if serial_match:
        serial = serial_match.group(1).upper()
        try:
            result = await client.lookup_serial(serial, actor_chann_uid=ctx.chann_uid)
        except Exception:
            log.exception("cross-tenant serial lookup failed during registration")
            result = {}
        matches = result.get("matches") or []

        if len(matches) == 1:
            # Exactly one shop registered it, so bind straight to them.
            # Asking someone to confirm the only possible answer is a
            # question with no purpose.
            shop = matches[0]
            return await _link_and_continue(
                client, ctx, company_code=str(shop["company_code"]),
                company_name=str(shop.get("company_name") or ""), serial=serial,
                language=language, license_id=str(shop.get("license_id") or ""),
            )

        if len(matches) > 1:
            listed = "\n".join(
                f"• {m['company_name']} — {m['company_code']}" for m in matches[:5]
            )
            header = (
                f"หมายเลข {serial} พบที่หลายร้าน พิมพ์รหัสร้านที่คุณซื้อ:"
                if language != "en"
                else f"Serial {serial} is registered at several shops — type the code:"
            )
            return f"{header}\n{listed}"

        # A bare serial nobody registered: say so, rather than treating
        # the serial as a fault description.
        if _is_bare_token(text):
            return _t(SERIAL_UNKNOWN_HERE, language).format(serial=serial)

    # A shop by name. "ร้าน dev company" is searched as "dev company";
    # exactly one hit links straight away (the same rule as the serial —
    # confirming the only answer is a question with no purpose).
    query = shop_query(text)
    shops: list[dict] = []
    if len(query) >= 2:
        shops = await client.search_shops(query)
        if not shops and query != text and len(text) >= 2:
            shops = await client.search_shops(text)
    if len(shops) == 1:
        shop = shops[0]
        return await _link_and_continue(
            client, ctx, company_code=str(shop["company_code"]),
            company_name=str(shop.get("company_name") or ""), language=language,
        )
    if shops:
        lines = "\n".join(
            f"• {s['company_name']} — {s['company_code']}" for s in shops[:5]
        )
        header = (
            "พบร้านเหล่านี้ พิมพ์รหัสร้านเพื่อผูก:"
            if language != "en"
            else "Found these shops — type the code to link:"
        )
        return f"{header}\n{lines}"

    # Nothing matched. What the person typed is almost certainly not a
    # shop name — it is what they actually wanted to say, usually a fault.
    # It is kept (and repeated back, so they can see it was not lost) and
    # filed the moment a shop is linked; the question is asked in terms
    # they can answer: the sticker on the machine, or the shop's name.
    if text:
        # Best-effort: holding the message is a courtesy, and a cache
        # failure must not stop a person who is trying to report a fault
        # from getting an answer at all.
        try:
            await client.set_pending_intent(
                ctx.chann_uid, "customer",
                action="report", entity="pending_customer_message",
                fields={"message": text[:500]}, missing=["company"],
                ttl_seconds=CUSTOMER_PENDING_TTL_S,
            )
        except Exception:
            log.exception("could not hold a customer's message while unlinked")
        return _t(CUSTOMER_UNLINKED_HELD, language).format(text=text[:80])

    return _t(WELCOME_CUSTOMER, language)


def _is_bare_token(text: str) -> bool:
    token = (text or "").strip()
    return " " not in token and not re.search(r"[฀-๿]", token)


async def _redeem_invite_reply(
    client: DataClient, text: str, ctx: ResolvedContext, language: str
) -> str:
    """Shared by the Sales-OA and Technician-OA invite-code paths — the code
    itself carries the role being granted, so redemption does not need to
    know or care which OA it arrived on."""
    try:
        member = await client.redeem_invite(
            invite_code=text.upper(),
            chann_uid=ctx.chann_uid,
            display_name=ctx.display_name,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc) or _is_conflict(exc):
            return _t(BAD_CODE, language)
        raise
    role = str(member.get("role", ""))
    return _t(JOINED, language).format(
        name=member.get("company_name", ""), role=role,
    ) + _t(JOINED_NEXT.get(role, JOINED_NEXT["default"]), language)


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 409 or "409" in str(exc)


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or "404" in str(exc)


def is_unregistered(ctx: ResolvedContext) -> bool:
    return ctx.resolution is TenantResolution.NONE
