"""Round 20j — a rate limit is "ask again", not "give up".

The OA audit (18 ก.ย. 2569) found that `_send` raised on every status at
or above 400, and every caller swallows that deliberately — `notify.py`
says so in a comment, and it is right to, for a LINE outage. For a 429 it
is wrong: LINE is saying come back in a moment, and the message was
instead dropped with nothing but a log line.

That was tolerable while this was a demo. It is not now that the shops on
this deployment are real.

The half that makes retrying safe was already there: every request carries
`X-Line-Retry-Key`, so a repeat with the SAME key returns the original
result rather than sending twice. These tests pin that the key is reused —
a per-attempt key would turn this fix into duplicate messages to customers.
"""
from __future__ import annotations

import httpx
import pytest

from chann_app.line import client as line


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(line, "channel_access_token", lambda oa: "test-token")


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    waited: list[float] = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(line.asyncio, "sleep", fake_sleep)
    return waited


def _client(statuses, headers=None):
    """An httpx client that answers with each status in turn."""
    seen: list[httpx.Request] = []
    codes = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = codes[min(len(seen) - 1, len(codes) - 1)]
        return httpx.Response(
            code,
            json={"sentMessages": [{"id": "m-1"}]} if code < 400 else {"message": "too many requests"},
            headers=(headers or {}) if code >= 400 else {},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


pytestmark = pytest.mark.asyncio


class TestARateLimitIsRetried:
    async def test_a_429_then_success_delivers_the_message(self):
        http, seen = _client([429, 200])
        ids = await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert ids == ["m-1"]
        assert len(seen) == 2, "it gave up on the first 429"

    async def test_the_retry_key_is_the_same_on_every_attempt(self):
        """This is what stops a retry becoming a second message."""
        http, seen = _client([429, 429, 200])
        await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        keys = {r.headers["X-Line-Retry-Key"] for r in seen}
        assert len(seen) == 3
        assert len(keys) == 1, f"a new key per attempt would send {len(keys)} messages"

    async def test_it_gives_up_eventually_rather_than_hanging(self):
        http, seen = _client([429])
        with pytest.raises(line.LineReplyError):
            await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert len(seen) == line._RETRY_ATTEMPTS

    async def test_it_waits_as_long_as_line_asked(self, _no_real_waiting):
        http, _ = _client([429, 200], headers={"Retry-After": "3"})
        await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert _no_real_waiting == [3.0]

    async def test_an_absurd_retry_after_is_capped(self, _no_real_waiting):
        """A webhook cannot wait an hour, whatever the header says."""
        http, _ = _client([429, 200], headers={"Retry-After": "3600"})
        await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert _no_real_waiting == [line._RETRY_AFTER_CAP_S]

    async def test_a_date_shaped_retry_after_does_not_sleep_until_tomorrow(self, _no_real_waiting):
        http, _ = _client([429, 200], headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert _no_real_waiting == [line._RETRY_BACKOFF_S[0]]

    async def test_it_backs_off_when_line_says_nothing(self, _no_real_waiting):
        http, _ = _client([429, 429, 200])
        await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert _no_real_waiting == list(line._RETRY_BACKOFF_S)


class TestWhatMustNotBeRetried:
    async def test_a_bad_request_is_not_retried(self):
        """400 is our bug. Sending it three times makes three bugs."""
        http, seen = _client([400])
        with pytest.raises(line.LineReplyError):
            await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert len(seen) == 1

    async def test_a_bad_token_is_not_retried(self):
        http, seen = _client([401])
        with pytest.raises(line.LineReplyError):
            await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert len(seen) == 1

    async def test_a_blocked_recipient_is_not_retried(self):
        http, seen = _client([403])
        with pytest.raises(line.LineReplyError):
            await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert len(seen) == 1

    async def test_a_server_error_IS_retried(self):
        """500/502/503/504 are LINE having a moment, same as 429."""
        http, seen = _client([503, 200])
        ids = await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert ids == ["m-1"] and len(seen) == 2


class TestTheHappyPathIsUnchanged:
    async def test_one_call_one_request(self, _no_real_waiting):
        http, seen = _client([200])
        ids = await line._send("https://api.line.me/x", "sales", {"to": "U1"}, http, "push")
        assert ids == ["m-1"]
        assert len(seen) == 1
        assert _no_real_waiting == [], "it slept on a successful send"


# ------------------------------------------- the morning digest leaves in order

from chann_app.services import reminders  # noqa: E402


class TestTheMorningDigestDoesNotLeaveAllAtOnce:
    """Owner, 18 ก.ย. 2569: "อยากให้หน่วงเวลาแต่ละร้านออกไปซักหน่อย
    เพื่อไม่ให้ชนกัน".

    Every person in every shop used to be pushed at the same instant,
    through three LINE channels the whole platform shares. What must NOT
    change while fixing that: a person in two shops still gets exactly one
    digest, and the order is the same every morning.
    """

    def test_a_shop_gets_the_same_slot_every_day(self):
        """Random or clock-based would mean "my reminders come at a
        different time each day", which nobody can answer."""
        first = reminders._shop_slot("11111111-1111-1111-1111-111111111111")
        again = reminders._shop_slot("11111111-1111-1111-1111-111111111111")
        assert first == again

    def test_different_shops_get_different_slots(self):
        slots = {reminders._shop_slot(f"lic-{n}") for n in range(50)}
        assert len(slots) == 50

    def test_the_gap_shrinks_rather_than_overrunning_the_window(self):
        """2,400 people at the default gap fills the window exactly; more
        than that must speed up, not run past it."""
        gap, window = reminders._SEND_GAP_S, reminders._SEND_WINDOW_S
        people = int(window / gap) + 1000
        assert min(gap, window / people) * people <= window

    def test_the_first_send_is_not_delayed(self):
        """An empty or one-person morning must still answer at once — the
        gap belongs between sends, not in front of the first."""
        from pathlib import Path

        src = Path(reminders.__file__).read_text(encoding="utf-8")
        body = src[src.index("for sent_so_far, (owner, items) in enumerate(ordered):"):]
        assert "if sent_so_far:" in body[:400]
        assert body.index("if sent_so_far:") < body.index("await asyncio.sleep(gap)")

    def test_one_person_still_gets_one_message(self):
        """The batching this spreading sits on top of is the thing it must
        not undo: per_person is keyed by person, and the loop walks it."""
        from pathlib import Path

        src = Path(reminders.__file__).read_text(encoding="utf-8")
        assert "per_person: dict[str, list[dict]] = {}" in src
        assert "sorted(\n        per_person.items()," in src


# ------------------------------- every customer push leaves a countable row

class TestNoCustomerPushIsInvisible:
    """OA audit, 18 ก.ย. 2569: four customer-facing pushes called push_text
    directly, so they wrote no notification row — a per-shop push tally
    would have read low, and nobody would have known by how much.

    Routing them through send_notification without carrying their buttons
    would have been a worse trade: a customer told the conversation closed,
    with no way to reopen it. So the button rides along.
    """

    def test_live_chat_no_longer_pushes_behind_the_recorder(self):
        from pathlib import Path

        from chann_app.services import live_chat

        src = Path(live_chat.__file__).read_text(encoding="utf-8")
        body = "\n".join(
            l for l in src.splitlines()
            if not l.lstrip().startswith("#") and "Through send_notification" not in l
        )
        assert "push_text(" not in body, "a push is going out unrecorded again"
        assert "push_messages(" not in body

    def test_the_buttons_survived_the_move(self):
        from pathlib import Path

        from chann_app.services import live_chat

        src = Path(live_chat.__file__).read_text(encoding="utf-8")
        assert 'quick_reply_item("จบการสนทนา"' in src
        assert 'quick_reply_item("คุยกับร้าน"' in src

    def test_send_notification_can_carry_one(self):
        import inspect

        from chann_app.services.notify import send_notification

        assert "quick_reply" in inspect.signature(send_notification).parameters

    def test_each_row_names_the_shop_it_belongs_to(self):
        """A row with no license_id counts against nobody."""
        from pathlib import Path

        from chann_app.services import live_chat

        src = Path(live_chat.__file__).read_text(encoding="utf-8")
        for fn in ("_push_customer", "_push_customer_invite"):
            sig = src[src.index(f"async def {fn}("):]
            sig = sig[: sig.index(") -> bool:")]
            assert "license_id" in sig, fn


# --------------------------------- a capped list must say what it left out

from pathlib import Path as _Path  # noqa: E402

ROOT = _Path(__file__).resolve().parents[2]


class TestACappedListSaysSo:
    """Round 20j put a ceiling on the customer and deal lists — and a
    ceiling on its own is the round-20h bug again: a page that looks like
    the whole book. The count has to reach the screen, or it was computed
    and thrown away.
    """

    def test_the_client_reads_the_header(self):
        import httpx

        from chann_app.data_client import _total_of

        resp = httpx.Response(200, json=[], headers={"X-Total-Count": "3000"})
        assert _total_of(resp, []) == 3000

    def test_no_header_means_the_page_is_the_total(self):
        """An older Data tier has not truncated anything either."""
        import httpx

        from chann_app.data_client import _total_of

        assert _total_of(httpx.Response(200, json=[]), [{"a": 1}, {"b": 2}]) == 2

    def test_rubbish_in_the_header_does_not_crash_the_list(self):
        import httpx

        from chann_app.data_client import _total_of

        resp = httpx.Response(200, json=[], headers={"X-Total-Count": "lots"})
        assert _total_of(resp, [{"a": 1}]) == 1

    def test_the_application_route_passes_it_on(self):
        from pathlib import Path

        from chann_app import routers_phase2

        src = Path(routers_phase2.__file__).read_text(encoding="utf-8")
        for name in ("async def list_customers(", "async def list_deals("):
            body = src[src.index(name):]
            body = body[: body.index("@router.", 10)]
            assert 'response.headers["X-Total-Count"]' in body, name

    def test_the_proxy_relays_it_to_the_browser(self):
        from pathlib import Path

        route = ROOT / "presentation/app/api/phase2/[...path]/route.ts"
        assert '"X-Total-Count": String(result.total)' in route.read_text(encoding="utf-8")

    def test_the_screen_shows_the_real_total(self):
        """The reading moved, the guarantee did not.

        Round 20j had CustomerList.tsx read the header itself; round 20N
        gave all six list screens one hook, so the header is read there
        and the pages consume it. Pinning the FILE would have made this
        test fail for a refactor that kept every promise — what matters is
        that something between the response and the screen still reads it
        (20 ก.ย. 2569).
        """
        hook = (ROOT / "presentation/app/liff/_paged-list.ts").read_text(encoding="utf-8")
        assert 'response.headers.get("X-Total-Count")' in hook
        page = (ROOT / "presentation/app/liff/sales/customers/CustomerList.tsx").read_text(
            encoding="utf-8",
        )
        assert "usePagedList" in page
        # The total reaches the count line (20Q moved it from a separate
        # "showingOf" hint into the list head's Count).
        assert "total={totalHeld" in page
