"""Does this sentence ask for the action, or does it only mention it?

Everything in the chat had been deciding what to *do* from the words a
sentence happens to contain. That is why, on 9–10 Sep 2026, an external
review could cancel an appointment by typing "ไม่ต้องยกเลิกนัด C-2026-0001",
create a real quotation by asking "สร้างใบเสนอราคา D-2026-0001 ไปหรือยัง",
check a job in with "เช็คอินต้องทำอย่างไร", and open a repair ticket with
"ทดสอบระบบ คำว่า แอร์เสีย". Five different handlers, one mistake: the verb
was found, so the verb was performed.

Adding another phrase to each handler's list would fix each sentence and
none of the others, so the decision is made once, here, before any handler
that writes. :func:`intent_to_act` reads the *shape* of the sentence:

* a negation of the action              — "ไม่ต้อง…", "อย่า…", "ยังไม่…", "ไม่ใช่ว่า…"
* a question about the action           — "…ไปแล้วหรือยัง", "ทำไมต้อง…", "…ต้องทำอย่างไร"
* a conditional or a future             — "ถ้า…จะ…", "พรุ่งนี้ค่อย…"
* a report of somebody else's action    — "ลูกค้าบอกว่า…", "ช่างบอกว่า…"
* an example or a test                  — "ทดสอบระบบ คำว่า …", quoted text
* a statement that the problem is over  — "…ซ่อมแล้ว ใช้ได้ปกติ", "หายแล้ว"
* abandoning the exchange in progress   — "หยุดก่อน", "พักไว้ก่อน", "เดี๋ยวค่อยทำ"

and none of these is allowed to write. Everything else does, which is the
half that matters just as much: "ยกเลิกนัด C-2026-0001", "เช็คอิน",
"สร้างใบเสนอราคา D-2026-0001" and "แอร์ไม่เย็น" are orders, and Thai
politeness wraps an order in a question — "ช่วยเช็คอินให้หน่อยได้ไหมครับ"
is an instruction, and refusing it sent a technician the user guide (owner,
9 Sep 2026). A leading ช่วย/รบกวน/กรุณา therefore outranks the question
mark, but never outranks a negation.

Where the sentence is genuinely balanced — the verb, the record, and
"ได้ไหม" with no polite head — the answer is :data:`ASK`, so the chat can
name the record and ask, rather than guess in either direction.

The verdict carries a reason so the caller can word the reply; it never
words the reply itself, because what "ยังไม่ได้ทำ" should say depends on
the action, and the action's own vocabulary lives with its handler.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

# The three answers. ACT is the only one that may write.
ACT = "act"
HOLD = "hold"
ASK = "ask"


@dataclass(frozen=True)
class Verdict:
    """What the sentence asks for, and why we read it that way.

    ``reason`` is one of: ``negated``, ``conditional``, ``later``,
    ``reported``, ``example``, ``resolved``, ``abandoned``, ``status``,
    ``howto``, ``question`` — or "" when the verdict is ACT.
    """

    outcome: str
    reason: str = ""

    @property
    def acts(self) -> bool:
        return self.outcome == ACT

    @property
    def asks(self) -> bool:
        return self.outcome == ASK

    def __bool__(self) -> bool:  # `if intent_to_act(...)` reads as "may act"
        return self.acts


_ACTS = Verdict(ACT)


# The words that ARE the action, per named action, on top of whatever
# trigger tuple the caller hands in. The caller's triggers are the
# authority; these fill the gaps a trigger table leaves — a customer never
# types "เปิด ticket", they type "แอร์เสีย", and a pending slot-filling
# flow has no trigger table at all.
ACTION_WORDS: dict[str, tuple[str, ...]] = {
    "appointment_create": ("นัด", "เตือน", "ตั้งนัด", "ตั้งเตือน", "appointment", "remind"),
    "appointment_move": (
        "เลื่อนนัด", "เลื่อนเตือน", "เลื่อน", "เปลี่ยนเวลา", "เปลี่ยนวัน", "เปลี่ยนวันนัด",
        "แก้เวลา", "แก้วันนัด", "reschedule", "นัด", "เตือน",
    ),
    "appointment_cancel": (
        "ยกเลิกนัด", "ยกเลิกเตือน", "ยกเลิกการเตือน", "ยกเลิกการนัด", "ลบนัด", "ลบเตือน",
        "เอานัดออก", "ยกเลิก", "cancel", "นัด", "เตือน",
    ),
    "appointment_delete": ("ลบนัด", "ลบเตือน", "เอานัดออก", "ลบ", "delete", "นัด", "เตือน"),
    "quote_create": (
        "สร้างใบเสนอราคา", "ออกใบเสนอราคา", "ทำใบเสนอราคา", "เปิดใบเสนอราคา", "ขอใบเสนอราคา",
        "ใบเสนอราคา", "quote", "quotation",
    ),
    "check_in": ("เช็คอิน", "เช็กอิน", "ถึงหน้างาน", "ถึงแล้ว", "ถึงไซต์", "ถึง", "checkin", "check in", "arrived"),
    "check_out": ("เช็คเอาท์", "เช็คเอาต์", "ปิดงาน", "จบงาน", "ส่งรายงาน", "checkout", "check out", "เสร็จ"),
    # What a customer's sentence has to be about before it opens a repair
    # job. "ไม่เย็น" is deliberately absent: it is a symptom, not a verb, so
    # "แอร์ไม่เย็น" must never read as a negated action.
    "ticket_open": (
        "แจ้งซ่อม", "เปิดงานซ่อม", "เปิดงาน", "ซ่อม", "ส่งช่าง", "เรียกช่าง", "ขอช่าง", "นัดช่าง",
        "ให้ช่างมา", "ช่างมา", "ช่าง", "เสีย", "พัง", "ชำรุด", "มีปัญหา", "ใช้ไม่ได้", "ใช้งานไม่ได้",
        "repair", "technician", "fix", "broken",
    ),
    # Lines on a deal or a quotation: "เพิ่มพัดลมอีก 3 ตัว".
    "line_item": (
        "เพิ่ม", "ใส่", "ลด", "ลบ", "เอาออก", "แก้จำนวน", "เปลี่ยนจำนวน", "แก้ราคา", "เปลี่ยนราคา",
        "add", "remove", "delete",
    ),
    # A create/edit flow already waiting for one more answer.
    "pending_flow": (
        "เพิ่ม", "สร้าง", "บันทึก", "ทำ", "เปิด", "ลงข้อมูล", "กรอก", "ใส่", "add", "create", "save",
    ),
    # The catch-all for a removal the model asked for on an entity with no
    # wording of its own.
    "record_delete": ("ลบ", "ยกเลิก", "เอาออก", "นำออก", "delete", "remove", "cancel"),
}


# Politeness that wraps an order in a question. It has to START a clause —
# a sentence that merely contains ช่วย inside a word ("ใครช่วยได้บ้าง") is a
# question — but the clause need not be the first: "แอร์ไม่เย็นเลยครับ
# รบกวนช่วยส่งช่างมาดูให้หน่อยได้ไหมครับ" states the fault, then asks for
# the visit, and is one of the sixteen customer-fault scenarios that has
# to keep opening a job.
_REQUEST_HEAD_RE = re.compile(
    r"^(?:ช่วย|รบกวน|กรุณา|วานช่วย|วาน|ขอความกรุณา|ขอให้|ขอนัด|ขอ|อยากให้|อยากได้|อยากนัด|อยาก|"
    r"ต้องการ|please|pls|iwant|ineed|idlike)"
)

# Punctuation the compact form drops. The hyphen stays so a record code
# survives; nothing here reads a code, but a mangled one reads as noise.
_PUNCT_RE = re.compile(r"[\s!?.,~ๆ。()\[\]\"'“”‘’:;]+")

# Negation, longest first so "ยังไม่ได้" is not read as "ยังไม่" + "ได้".
# Bare "ไม่" is last and is only trusted when the action word follows it
# immediately, because "ไม่เย็น" and "ไม่ทำงาน" are faults, not refusals.
# "เลิก" is deliberately absent: it lives inside "ยกเลิก", which is a
# command, and reading "ยกเลิกนัด" as "เลิก" + "นัด" refused the very
# order the guard exists to protect.
_NEGATIONS = (
    "ไม่ใช่ว่า", "ไม่ใช่", "ยังไม่ได้", "ยังไม่ต้อง", "ยังไม่", "ไม่ต้อง", "ไม่ได้", "ไม่เอา",
    "อย่าเพิ่ง", "อย่า", "ห้าม", "ไม่",
    # English, spelled without spaces: the compact form has none.
    "donot", "dont", "didnot", "didnt", "havenot", "havent", "notyet", "noneedto", "noneed",
)
_BARE_NEGATION = "ไม่"
# How far past the negation the action may sit and still be its object.
_NEGATION_WINDOW = 10
# "ไม่ได้ล้างมานาน" is neglect, not refusal — the sentence is a complaint
# that nothing has been done, which is a reason to send somebody.
_NEGLECT_RE = re.compile(r"^.{0,16}?(?:มานาน|นานแล้ว|มาหลาย|หลายปี|หลายเดือน|มาตั้งแต่|มาเป็นปี)")
# The action, then the refusal: "เช็คอินไม่ได้", "ปิดงานไม่ได้".
_TRAILING_NEGATION = ("ไม่ได้", "ไม่ทัน", "ไม่ไหว")

# No bare English "if": in the compact form it is a substring of "notify".
_CONDITIONAL_HEADS = ("ถ้า", "หาก", "เผื่อ", "สมมติ", "สมมุติ", "กรณีที่", "ในกรณี", "แล้วแต่ว่า")
# A time word plus a deferral: "พรุ่งนี้ค่อยเช็คอิน", "เดี๋ยวจะยกเลิก".
_LATER_RE = re.compile(
    r"(?:พรุ่งนี้|มะรืน|วันหลัง|ทีหลัง|วันหน้า|คราวหน้า|ต่อไป|เดี๋ยว|ไว้|อีกที|ภายหลัง|later|tomorrow)"
    r"(?:ก่อน|นะ)?(?:ค่อย|จะ)"
)
_LATER_TAIL_RE = re.compile(r"(?:ค่อย(?:ทำ|ว่ากัน|แจ้ง|บอก|มา)|ไว้ก่อน|เอาไว้ก่อน|รอก่อน|เดี๋ยวก่อน)")

# Somebody else's words, reported.
_REPORTED_RE = re.compile(
    r"(?:ลูกค้า|ช่าง|เขา|แอดมิน|หัวหน้า|ทีม|เพื่อน|น้อง|พี่|เจ้าของ|customer|the tech)?"
    r"(?:บอกว่า|บอกให้|ถามว่า|เล่าว่า|saidthat|saysthat|toldme)"
)

# An example, a quotation, a rehearsal.
_EXAMPLE_RE = re.compile(
    r"คำว่า|พิมพ์ว่า|เขียนว่า|ส่งคำว่า|ยกตัวอย่าง|เป็นตัวอย่าง|ตัวอย่างเช่น|ตัวอย่าง|ลองพิมพ์|ลองดูว่า|"
    r"forexample|justtesting|testmessage|sampletext"
)
# "ทดสอบ" only when the sentence opens with it: "แอร์ทดสอบ" is a product.
_TEST_HEAD_RE = re.compile(r"^(?:ขอ)?ทดสอบ")
_QUOTED_RE = re.compile(r"[\"“”'‘’«]([^\"“”'‘’«»]{2,60})[\"“”'‘’»]")

# The problem is over. Only meaningful where the action exists to fix a
# problem, so it is gated by _PROBLEM_ACTIONS below.
_RESOLVED_RE = re.compile(
    r"ซ่อมแล้ว|ซ่อมเสร็จ|หายแล้ว|ใช้ได้ปกติ|ใช้งานได้ปกติ|ใช้ได้แล้ว|ใช้งานได้แล้ว|ปกติแล้ว|ปกติดี|"
    r"ไม่เสียแล้ว|ไม่มีปัญหาแล้ว|เรียบร้อยแล้ว|ดีขึ้นแล้ว|กลับมาปกติ|เย็นแล้ว|fixedalready|workingnow"
)
_PROBLEM_ACTIONS = frozenset({"ticket_open"})

# The whole message is "stop"/"not now" — an exchange being abandoned.
_ABANDON_RE = re.compile(
    r"^(?:ขอ|จะ|เดี๋ยว)?(?:ยกเลิก|หยุด|พักไว้|พักก่อน|พัก|พอก่อน|พอแค่นี้|พอ|ไม่เอา|ไม่ทำ|เลิก|ช่างมัน|"
    r"stop|pause|cancel|holdon|wait|forgetit|nevermind)"
    r"(?:ก่อน|แล้ว|นะ|ครับ|ค่ะ|คะ|คับ|ที|ละ|ล่ะ|เลย|กัน|please|ๆ)*$"
)
# A bare "พรุ่งนี้" is an ANSWER (to "which day?"), never an abandonment,
# so only the words that mean "not now" are anchored here.
_LATER_ONLY_RE = re.compile(
    r"^(?:เดี๋ยว|ไว้|ทีหลัง|วันหลัง|คราวหน้า)"
    r"(?:ค่อย|จะ)?(?:ทำ|ว่ากัน|เอา|มาใหม่|คุยกัน|บอก|แจ้ง|ดู)?"
    r"(?:ก่อน|แล้ว|นะ|ครับ|ค่ะ|คะ|คับ|ที|ละ|ล่ะ|เลย|กัน)*$"
)

# "…ไปแล้วหรือยัง": asking whether it has been done.
_STATUS_RE = re.compile(
    r"(?:ไปแล้ว|แล้ว)?(?:หรือยัง|รึยัง|หรือเปล่า|รึเปล่า|หรือไม่|ใช่ไหม|ใช่มั้ย|ใช่ป่าว|ยังคะ|ยังครับ)|"
    r"ไปแล้วยัง|ยังอยู่ไหม|ยังอยู่มั้ย"
)
_STATUS_TAIL = ("หรือยัง", "รึยัง", "ยัง", "ใช่ไหม", "ใช่มั้ย", "ใช่ป่าว", "หรือเปล่า", "รึเปล่า")

# "ทำไมต้อง…", "…ต้องทำอย่างไร", "แค่ถามวิธี…": asking about the action.
_HOWTO_RE = re.compile(
    r"ทำไม|ทำอย่างไร|ทำยังไง|อย่างไร|อย่างอะไร|ยังไง|วิธี|ต้องทำอะไร|ต้องทำยังไง|จะเกิดอะไรขึ้น|"
    r"แค่ถาม|ขอถาม|ขอทราบ|สอบถาม|อยากทราบ|อยากรู้|เผื่อถาม|howdoi|howto|howcani|whathappens"
)

# A plain question, when nothing more specific fits.
_QUESTION_WORDS = (
    "ไหม", "มั้ย", "หรอ", "เหรอ", "ป่าว", "เปล่า", "กี่โมง", "เมื่อไหร่", "เมื่อไร", "เท่าไหร่", "เท่าไร",
    "กี่บาท", "กี่วัน", "กี่ชั่วโมง", "ที่ไหน", "ตรงไหน", "อันไหน", "วันไหน", "ตอนไหน", "ใครบ้าง", "อะไรบ้าง",
    "what", "when", "where", "who", "which", "cani", "doyou", "isit",
)
# A question mark is NOT in that list on purpose: "ช่วยยกเลิกนัดให้หน่อยได้ไหม?"
# is an order with a question mark on the end, and reading the mark as an
# enquiry is the over-blocking the 9 Sep fix had to undo.
# The possibility question that is genuinely two things at once.
_MAYBE_RE = re.compile(r"ได้ไหม|ได้มั้ย|ได้ป่าว|ได้เปล่า|ได้หรือเปล่า|ไหวไหม|ได้รึเปล่า")


def _compact(canonical: str) -> str:
    return _PUNCT_RE.sub("", canonical or "")


def _polite_order(canon: str, compact: str) -> bool:
    """A polite request opens the sentence, or one of its clauses."""
    return bool(_REQUEST_HEAD_RE.match(compact)) or any(
        _REQUEST_HEAD_RE.match(part) for part in (canon or "").split()
    )


def _words_for(action: str, triggers: Sequence[str]) -> list[str]:
    raw: Iterable[str] = tuple(triggers or ()) + ACTION_WORDS.get(action, ())
    words = {t.replace(" ", "").lower() for t in raw if t and t.strip()}
    return sorted(words, key=len, reverse=True)


def _first_action_at(compact: str, words: Sequence[str]) -> int | None:
    """Where the action is named, or None when it is not named at all."""
    found = [compact.find(w) for w in words]
    hits = [i for i in found if i >= 0]
    return min(hits) if hits else None


def _negated(compact: str, words: Sequence[str]) -> bool:
    for neg in _NEGATIONS:
        start = 0
        while True:
            at = compact.find(neg, start)
            if at < 0:
                break
            start = at + 1
            rest = compact[at + len(neg):]
            if _NEGLECT_RE.match(rest):
                continue
            window = _NEGATION_WINDOW if neg != _BARE_NEGATION else 0
            if any(rest.startswith(w) for w in words):
                return True
            if window and any(0 < rest.find(w) <= window for w in words if w in rest):
                return True
    for word in words:
        at = compact.find(word)
        if at >= 0 and compact[at + len(word):].startswith(_TRAILING_NEGATION):
            return True
    return False


def _only_inside_quotes(canonical: str, words: Sequence[str]) -> bool:
    """Every mention of the action sits inside quotation marks."""
    spans = [m.span(1) for m in _QUOTED_RE.finditer(canonical)]
    if not spans:
        return False
    seen = False
    for word in words:
        start = 0
        while True:
            at = canonical.find(word, start)
            if at < 0:
                break
            seen = True
            start = at + 1
            if not any(lo <= at < hi for lo, hi in spans):
                return False
    return seen


def intent_to_act(
    message: str,
    *,
    action: str,
    triggers: Sequence[str] = (),
    canonical: str | None = None,
) -> Verdict:
    """Does this sentence ask for `action` to be performed?

    `action` names the decision being made (see :data:`ACTION_WORDS`) and
    is used only to find the action's own words; `triggers` is the
    handler's own trigger tuple, which always wins where the two differ.
    `canonical` lets a caller that has already folded the spelling pass it
    in; otherwise chat's canonical form is used, so every matcher in the
    system reads the same text.
    """
    if canonical is None:
        from .chat import _canonical  # lazy: chat imports this module

        canonical = _canonical(message)
    canon = canonical or ""
    compact = _compact(canon)
    if not compact:
        return _ACTS

    words = _words_for(action, triggers)
    at = _first_action_at(compact, words)
    named = at if at is not None else len(compact)
    # A handler's own trigger, standing at the front of the sentence, IS
    # the command however it is spelled — and some of them are spelled as
    # negations on purpose: "ไม่ต้องเตือนเรื่องสมชายแล้ว" is how people
    # cancel a reminder, and reading its "ไม่ต้อง" as a refusal would
    # refuse the cancellation itself.
    imperative = any(
        compact.startswith(t.replace(" ", "").lower())
        for t in (triggers or ()) if t and t.strip()
    )

    # The exchange itself is being dropped: "หยุดก่อน", "เดี๋ยวค่อยทำ".
    if _ABANDON_RE.match(compact) or _LATER_ONLY_RE.match(compact):
        return Verdict(HOLD, "abandoned")

    # Quoting, testing, illustrating.
    if _TEST_HEAD_RE.match(canon) or _EXAMPLE_RE.search(compact) or _only_inside_quotes(canon, words):
        return Verdict(HOLD, "example")

    # Somebody else did it, or said they did.
    reported = _REPORTED_RE.search(compact)
    if reported and reported.start() <= named:
        return Verdict(HOLD, "reported")

    # The reason for the action has gone away.
    if action in _PROBLEM_ACTIONS and _RESOLVED_RE.search(compact):
        return Verdict(HOLD, "resolved")

    # From here on the sentence has to actually name the action: without
    # it, "ถ้า" and "ไม่" are about something else entirely, and holding
    # on them would refuse ordinary work.
    if at is None:
        return _ACTS

    if not imperative and _negated(compact, words):
        return Verdict(HOLD, "negated")

    conditional = min(
        (compact.find(h.strip()) for h in _CONDITIONAL_HEADS if compact.find(h.strip()) >= 0),
        default=-1,
    )
    if 0 <= conditional < named:
        return Verdict(HOLD, "conditional")
    later = _LATER_RE.search(compact)
    if (later and later.start() <= named) or _LATER_TAIL_RE.search(compact):
        return Verdict(HOLD, "later")

    # "ทำไมต้อง…" and "…ไปแล้วหรือยัง" are questions however politely they
    # are asked, so these two come before politeness rather than after it.
    if _HOWTO_RE.search(compact):
        return Verdict(HOLD, "howto")
    if _STATUS_RE.search(compact) or compact.endswith(_STATUS_TAIL):
        return Verdict(HOLD, "status")

    # Thai politeness is shaped like a question; a leading ช่วย/รบกวน/ขอ is
    # still an order. It never outranks anything above it.
    if _polite_order(canon, compact):
        return _ACTS
    if _MAYBE_RE.search(compact):
        # "นัด C-2026-0001 ยกเลิกได้ไหม" is an order and a question in equal
        # measure. Neither guess is safe, so the chat asks.
        return Verdict(ASK, "maybe")
    if any(w in compact for w in _QUESTION_WORDS):
        return Verdict(HOLD, "question")
    return _ACTS
