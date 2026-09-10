#!/usr/bin/env python3
"""probe.py — one message, one context, everything that happened.

Replies alone hide unintended writes. For every turn this records:

  * the reply text (and every other field of the ChatReply)
  * every data mutation that resulted, from three independent angles:
      - tests/unit/test_phase6_chat.py's FakeDataClient.recorded list
      - an instance-level wrapper around every public method of that fake,
        so a method that FORGETS to record is still seen
      - a deep snapshot of the fake's own state, taken before and after and
        diffed, so a write nobody expected is visible even if the call name
        is unfamiliar
  * whether the model was consulted, what it was asked (full system prompt +
    user message) and what it answered — captured at the HTTP layer with an
    httpx MockTransport, the AiProbe pattern from scripts/dev/simulate-phrasings.py
  * which branch answered — chann_app.services.chat.ChatReply.__init__ is
    monkeypatched from HERE (the repo is never touched) to stamp every reply
    with the function and line that built it, plus the chain of _handle_* /
    _maybe_handle_* functions entered
  * wall-clock latency of the call

Input : a JSON list of cases on stdin, or a path to a .json / .jsonl file.
Output: one JSON line per case on stdout. See probe-format.md.

Usage:
    /tmp/dv/bin/python /tmp/diag/shared/probe.py cases.json > out.jsonl
    cat cases.json | /tmp/dv/bin/python /tmp/diag/shared/probe.py
    /tmp/dv/bin/python /tmp/diag/shared/probe.py --selftest      # 10 built-in cases
    ... --summary        extra human-readable digest on stderr
    ... --timeout 30     per-case wall-clock budget (default 30s)
    ... --repo PATH      repository root (default /home/thanawinmax2/stage-fix/audit)

Nothing here writes to the repository and nothing here reaches the network:
the data tier is the test fake, the model is a MockTransport, and LINE's
push_text is replaced by a recorder.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as _dt
import hashlib
import inspect
import json
import os
import random
import sys
import time
import traceback
from decimal import Decimal

SCHEMA = "chann-probe/1"
DEFAULT_REPO = "/home/thanawinmax2/stage-fix/audit"


# --------------------------------------------------------------------------
# 1. Import the application and the test fake, without touching the repo.
# --------------------------------------------------------------------------

def bootstrap(repo: str):
    """Put the app and the unit-test package on sys.path and import them."""
    repo = os.path.abspath(repo)
    for p in (os.path.join(repo, "application"),
              os.path.join(repo, "data"),
              os.path.join(repo, "tests", "unit")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import httpx  # noqa: F401
    import test_phase6_chat as T                                  # the fake lives here
    from chann_app.config import settings
    from chann_app.services import chat as chat_mod
    from chann_app.services import notify as notify_mod
    from chann_app.services import live_chat as live_chat_mod
    # 6.9's autouse fixture does this; we are not under pytest.
    if not (settings.openrouter_api_key or "").strip():
        settings.openrouter_api_key = "probe-key"
    if not (settings.openrouter_model or "").strip():
        settings.openrouter_model = "probe/model"
    return T, chat_mod, notify_mod, live_chat_mod


# --------------------------------------------------------------------------
# 2. Is a data-tier call a read, a mutation, a piece of conversation memory,
#    or an outbound message? Explicit table over every public method of
#    tests/unit/test_phase6_chat.py::FakeDataClient, so the classification is
#    auditable rather than a prefix guess. Anything absent falls back to the
#    prefix rules below and is flagged `unknown` in the output.
# --------------------------------------------------------------------------

READ_CALLS = {
    "approval_steps_for_entity", "authorization_context", "due_follow_ups",
    "get_active_tenant", "get_approval_workflow", "get_assignment_rules",
    "get_company_profile", "get_consent", "get_customer", "get_deal",
    "get_display_preferences", "get_generated_document", "get_last_customer_ref",
    "get_last_entity_ref", "get_member", "get_message_entity", "get_pending_intent",
    "get_profile", "get_service_report", "get_ticket", "identity_signature",
    "line_target_of", "list_chat_sessions", "list_customers", "list_deals",
    "list_document_template_versions", "list_document_templates", "list_follow_ups",
    "list_license_settings", "list_members", "list_notes", "list_products",
    "list_quote_products", "list_quotes", "list_roles", "list_service_reports",
    "list_team_members", "list_technician_teams", "list_ticket_photos",
    "list_tickets", "list_warranties", "lookup_serial", "my_shops",
    "pending_approval_steps", "pending_survey_for_ticket", "permission_catalog",
    "pipeline_summary", "preview_document_template_version", "storefront_browse",
    "storefront_search",
}

# Conversation memory. Real rows, but about the CHAT, not about the shop's
# records: a pending-intent write is not a cancellation. Kept apart so
# "no mutation" means "no business data changed".
CONV_CALLS = {
    "clear_pending_intent", "set_pending_intent", "set_last_customer_ref",
    "set_last_entity_ref", "set_active_tenant", "set_display_preferences",
    "record_message_entity",
}

# Outbound message to a human. Not a data mutation, but a side effect that
# an unintended branch must not produce either.
NOTIFY_CALLS = {"create_notification"}

WRITE_CALLS = {
    "act_on_approval_step", "add_deal_product", "add_quote_product", "add_team_member",
    "add_ticket_photo", "answer_survey", "assign_ticket", "attach_report_document",
    "check_in_ticket", "check_out_ticket", "claim_ticket", "claim_warranty",
    "create_customer", "create_deal", "create_document_template",
    "create_document_template_version", "create_follow_up", "create_invite",
    "create_note", "create_pdpa_request", "create_quote", "create_technician_team",
    "create_ticket", "delete_follow_up", "delete_note", "delete_technician_team",
    "execute_assignment", "mark_survey_sent", "open_approval_steps",
    "process_pdpa_request", "promote_customer", "publish_document_template_version",
    "put_consent", "put_license_setting", "register_warranty", "reject_ticket",
    "remove_deal_product", "remove_quote_product", "remove_team_member",
    "replace_approval_workflow", "set_follow_up_status", "set_identity_signature",
    "set_quote_status", "set_quote_terms", "set_ticket_status",
    "storefront_record_interest", "transition_deal_stage", "update_company_profile",
    "update_customer", "update_deal", "update_deal_product", "update_follow_up",
    "update_note", "update_profile", "update_quote_product", "update_ticket",
    "upsert_assignment_rule", "upsert_product",
}

_WRITE_PREFIXES = (
    "create_", "update_", "set_", "put_", "add_", "remove_", "delete_", "assign_",
    "claim_", "reject_", "check_in", "check_out", "register_", "promote_",
    "transition_", "execute_", "replace_", "upsert_", "open_", "act_on_",
    "answer_", "mark_", "publish_", "attach_", "record_", "process_",
)
_READ_PREFIXES = ("get_", "list_", "find_", "lookup_", "search_", "describe_",
                  "pending_", "due_", "storefront_search", "storefront_browse")


def classify_call(name: str) -> str:
    if name in READ_CALLS:
        return "read"
    if name in CONV_CALLS:
        return "conv"
    if name in NOTIFY_CALLS:
        return "notify"
    if name in WRITE_CALLS:
        return "write"
    if name.startswith(_READ_PREFIXES):
        return "read?"
    if name.startswith(_WRITE_PREFIXES):
        return "write?"
    return "unknown"


# Attributes of the fake that hold conversation memory rather than shop data.
CONV_ATTRS = {"_pending", "_last_customer_ref", "_last_entity_ref",
              "_active_tenant", "_prefs", "_mapping"}
# Attributes that are fixture/config, not state the engine can change.
SKIP_ATTRS = {"recorded", "_raises", "_role", "_permission_keys",
              "_storefront_results", "_probe_wrapped", "_probe_depth"}


# --------------------------------------------------------------------------
# 3. Small helpers: bounded reprs and a JSON-safe view of anything.
# --------------------------------------------------------------------------

def short(value, limit: int = 400) -> str:
    try:
        s = repr(value)
    except Exception:                                             # noqa: BLE001
        s = "<unrepr-able>"
    return s if len(s) <= limit else s[: limit - 3] + "..."


def jsonable(value, depth: int = 0):
    """A JSON-safe view. Tuple dict keys (the fake uses them) become strings."""
    if depth > 8:
        return short(value, 200)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime, _dt.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v, depth + 1) for v in value]
    return short(value, 200)


def stable(value) -> str:
    try:
        return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True)
    except Exception:                                             # noqa: BLE001
        return short(value, 2000)


# --------------------------------------------------------------------------
# 4. State snapshot + diff over the fake's own storage.
# --------------------------------------------------------------------------

_MISSING = object()


def snapshot(client) -> dict:
    out = {}
    for key, val in list(vars(client).items()):
        if key in SKIP_ATTRS or key.startswith("_probe"):
            continue
        if callable(val) and not isinstance(val, (list, dict, tuple, set)):
            continue                                              # a wrapped method
        try:
            out[key] = copy.deepcopy(val)
        except Exception:                                         # noqa: BLE001
            out[key] = short(val, 500)
    return out


def _walk(path, before, after, sink, cap):
    if len(sink) >= cap:
        return
    if before is _MISSING or after is _MISSING:
        sink.append({"path": path,
                     "before": None if before is _MISSING else jsonable(before),
                     "after": None if after is _MISSING else jsonable(after),
                     "op": "added" if before is _MISSING else "removed"})
        return
    if stable(before) == stable(after):
        return
    if isinstance(before, list) and isinstance(after, list):
        for i in range(max(len(before), len(after))):
            b = before[i] if i < len(before) else _MISSING
            a = after[i] if i < len(after) else _MISSING
            _walk(f"{path}[{i}]", b, a, sink, cap)
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted({str(k) for k in before} | {str(k) for k in after}):
            bk = next((kk for kk in before if str(kk) == k), _MISSING)
            ak = next((kk for kk in after if str(kk) == k), _MISSING)
            _walk(f"{path}.{k}",
                  before[bk] if bk is not _MISSING else _MISSING,
                  after[ak] if ak is not _MISSING else _MISSING, sink, cap)
        return
    sink.append({"path": path, "before": jsonable(before),
                 "after": jsonable(after), "op": "changed"})


def diff_state(before: dict, after: dict, cap: int = 60):
    data, conv = [], []
    for key in sorted(set(before) | set(after)):
        b = before.get(key, _MISSING)
        a = after.get(key, _MISSING)
        if b is not _MISSING and a is not _MISSING and stable(b) == stable(a):
            continue
        sink = conv if key in CONV_ATTRS else data
        _walk(key, b, a, sink, cap)
    return data, conv


# --------------------------------------------------------------------------
# 5. The model probe: an httpx MockTransport that answers every OpenRouter
#    call and remembers exactly what it was asked. (AiProbe, extended.)
# --------------------------------------------------------------------------

DEFAULT_AI = {"action": "suggest", "entity": None, "fields": {}, "missing": []}


class _RecordingTransport:
    """Passes the request through to the real provider and keeps the same
    record the stub keeps. Used only by --real-model."""

    def __init__(self, inner, probe):
        self._inner = inner
        self._probe = probe

    async def handle_async_request(self, request):
        import json as _json
        import time as _time

        try:
            body = _json.loads(request.content.decode("utf-8"))
        except Exception:                                         # noqa: BLE001
            body = {}
        messages = body.get("messages") or []
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        started = _time.perf_counter()
        response = await self._inner.handle_async_request(request)
        await response.aread()
        elapsed_ms = (_time.perf_counter() - started) * 1000
        answer, usage = "", {}
        try:
            payload = _json.loads(response.content.decode("utf-8"))
            answer = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = payload.get("usage") or {}
        except Exception:                                         # noqa: BLE001
            pass
        self._probe.calls.append({
            "n": len(self._probe.calls) + 1,
            "system": system if AiProbe.prompt_chars == 0 else system[: AiProbe.prompt_chars],
            "system_chars": len(system),
            "user": user,
            "status": response.status_code,
            "answer": answer,
            "latency_ms": round(elapsed_ms, 1),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            # OpenRouter reports cost per response when the provider does.
            "cost": usage.get("cost"),
            "real": True,
        })
        return response

    async def aclose(self):
        await self._inner.aclose()


class AiProbe:
    # Set from the CLI. 0 keeps the full system prompt on every call; a
    # positive number truncates it, because a corpus of hundreds of cases
    # otherwise carries the same ~12 kB prompt hundreds of times.
    prompt_chars = 0

    # When true, the probe stops answering and lets the request reach the
    # configured provider. Everything else — what was asked, what came
    # back, the latency, the writes — is recorded exactly the same way, so
    # a stubbed run and a real-model run are directly comparable and the
    # only difference is who wrote the answer. Set from --real-model.
    real_model = False

    def __init__(self, script=None):
        import httpx
        self.calls: list[dict] = []
        self._script = list(script) if isinstance(script, list) else ([script] if script is not None else [])
        if self.real_model:
            # A real client, wrapped so the same record is kept. No
            # MockTransport: the request goes where the app would send it,
            # with the app's own key, model, retries and timeout.
            self.client = httpx.AsyncClient(
                transport=_RecordingTransport(httpx.AsyncHTTPTransport(), self)
            )
        else:
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _next(self):
        if not self._script:
            return DEFAULT_AI
        idx = min(len(self.calls), len(self._script) - 1)         # last entry repeats
        return self._script[idx]

    def _handle(self, request):
        import httpx
        try:
            body = json.loads(request.content.decode("utf-8"))
        except Exception:                                         # noqa: BLE001
            body = {}
        messages = body.get("messages") or []
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        spec = self._next()
        status, content = 200, None
        if isinstance(spec, dict) and "__status__" in spec:
            status = int(spec["__status__"])
            content = spec.get("content")
        elif isinstance(spec, dict):
            content = json.dumps(spec, ensure_ascii=False)
        else:
            content = str(spec)
        record = {
            "n": len(self.calls) + 1,
            "url": str(request.url),
            "model": body.get("model", ""),
            "temperature": body.get("temperature"),
            "max_tokens": body.get("max_tokens"),
            "reasoning": body.get("reasoning"),
            "system_prompt": system if not self.prompt_chars else system[: self.prompt_chars],
            "system_prompt_chars": len(system),
            "system_prompt_sha": hashlib.sha256(system.encode("utf-8")).hexdigest()[:12],
            "system_prompt_truncated": bool(self.prompt_chars and len(system) > self.prompt_chars),
            "user_message": user,
            "response_status": status,
            "response_content": content if content is not None else "",
        }
        self.calls.append(record)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "probe forced failure"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": len(system) // 4, "completion_tokens": 8},
            "provider": "probe",
        })

    async def aclose(self):
        try:
            await self.client.aclose()
        except Exception:                                         # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# 6. Branch attribution. Installed once, from here — the repo is read-only.
#    Every ChatReply records the function and line that constructed it, and
#    every _handle_* / _maybe_handle_* entered is appended to a chain.
# --------------------------------------------------------------------------

class Recorder:
    """The live sink. Swapped per turn; None means "do not record"."""
    def __init__(self):
        self.t0 = time.perf_counter()
        self.replies: list[dict] = []
        self.chain: list[str] = []
        self.events: list[list] = []
        self.pushes: list[dict] = []

    def stamp(self, kind, detail):
        self.events.append([round((time.perf_counter() - self.t0) * 1000, 3), kind, detail])


_REC: Recorder | None = None
_INSTALLED = False


def install(chat_mod, notify_mod, live_chat_mod):
    """Monkeypatch, from this script, what the probe needs to see."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    chat_file = os.path.basename(getattr(chat_mod, "__file__", "chat.py"))

    # --- every reply is stamped with the branch that built it -------------
    reply_cls = chat_mod.ChatReply
    orig_init = reply_cls.__init__

    def probe_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        rec = _REC
        if rec is None:
            return
        try:
            frame = sys._getframe(1)
            origin = {"function": frame.f_code.co_name,
                      "line": frame.f_lineno,
                      "file": os.path.basename(frame.f_code.co_filename)}
        except Exception:                                         # noqa: BLE001
            origin = {"function": "", "line": 0, "file": ""}
        try:
            object.__setattr__(self, "_probe_origin", origin)
        except Exception:                                         # noqa: BLE001
            pass
        rec.replies.append(origin)
        rec.stamp("reply_built", f"{origin['function']}:{origin['line']}")

    reply_cls.__init__ = probe_init

    # --- the handler chain ------------------------------------------------
    def is_handler(name: str) -> bool:
        return ("handle" in name or name.startswith("_maybe_")
                or name in ("_route_chat_message", "parse_intent"))

    for name, fn in list(vars(chat_mod).items()):
        if not inspect.iscoroutinefunction(fn) or not is_handler(name):
            continue
        if getattr(fn, "_probe_wrapped", False):
            continue

        def wrap(fn=fn, name=name):
            async def wrapped(*a, **kw):
                rec = _REC
                if rec is not None:
                    rec.chain.append(name)
                    rec.stamp("enter", name)
                try:
                    return await fn(*a, **kw)
                finally:
                    if rec is not None:
                        rec.stamp("exit", name)
            wrapped._probe_wrapped = True
            wrapped.__name__ = name
            return wrapped

        setattr(chat_mod, name, wrap())

    # --- LINE stays off the network, and a push is an observable effect ---
    async def fake_push_text(oa, line_uid, text, *a, **kw):
        rec = _REC
        if rec is not None:
            rec.pushes.append({"kind": "push_text", "oa": oa, "to": str(line_uid),
                               "text": short(text, 300)})
            rec.stamp("line_push", f"{oa}->{line_uid}")
        return []

    async def fake_push_messages(oa, line_uid, messages, *a, **kw):
        rec = _REC
        if rec is not None:
            rec.pushes.append({"kind": "push_messages", "oa": oa, "to": str(line_uid),
                               "text": short(messages, 300)})
            rec.stamp("line_push", f"{oa}->{line_uid}")
        return []

    notify_mod.push_text = fake_push_text
    live_chat_mod.push_text = fake_push_text
    if hasattr(live_chat_mod, "push_messages"):
        live_chat_mod.push_messages = fake_push_messages
    return chat_file


# --------------------------------------------------------------------------
# 7. Fixtures. A case may name a preset and/or supply rows inline.
# --------------------------------------------------------------------------

PERMISSION_PRESETS = {
    "sales": [
        "customer.create", "customer.read", "customer.update", "customer.archive",
        "deal.create", "deal.read", "deal.update", "quote.create", "quote.read",
        "quote.update", "product.manage", "followup.create", "followup.read",
        "followup.update", "note.create", "note.read", "ticket.read", "ticket.create",
        "ticket.update", "ticket.assign", "service_report.read", "warranty.read",
        "warranty.create", "team.manage", "member.manage", "setting.manage",
        "approval.view", "approval.approve", "approval.reject", "approval.manage",
        "view_reports",
    ],
    "sales_no_member_manage": None,      # filled in below
    "technician": ["ticket.read", "ticket.update", "ticket.close",
                   "service_report.create", "service_report.read", "warranty.read"],
    "customer": ["customer.read", "ticket.create", "ticket.read",
                 "warranty.read", "warranty.create"],
    "none": [],
}
PERMISSION_PRESETS["sales_no_member_manage"] = [
    k for k in PERMISSION_PRESETS["sales"] if k != "member.manage"
]


def resolve_permissions(spec, warnings=None):
    """A list of keys, or the name of a preset. An unknown preset is a
    warning, never a silent substitution: a run of hundreds of cases must
    not quietly grant the wrong rights."""
    if spec is None:
        return list(PERMISSION_PRESETS["sales"])
    if isinstance(spec, str):
        if spec not in PERMISSION_PRESETS:
            if warnings is not None:
                warnings.append(f"unknown permissions preset {spec!r}; used 'sales'")
            return list(PERMISSION_PRESETS["sales"])
        return list(PERMISSION_PRESETS[spec])
    return list(spec)


def base_fixture(name: str) -> dict:
    """Seed rows. Deliberately small and named, so a case reads as a scene."""
    if name == "technician":
        return {
            "_tickets": [
                {"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                 "accept_status": "accepted", "assigned_to_ref": "member-1",
                 "customer_name": "สมชาย", "customer_phone": "0812345678",
                 "service_address": "99/1 สุขุมวิท", "issue_description": "แอร์ไม่เย็น",
                 "scheduled_date": "2026-09-08", "scheduled_time": "10:00"},
                {"id": "t2", "ticket_number": "T-2026-0002", "status": "open",
                 "accept_status": "pending", "visibility": "public",
                 "customer_name": "สมหญิง", "service_address": "12 ลาดพร้าว",
                 "issue_description": "ตู้เย็นไม่เย็น", "scheduled_date": "2026-09-09",
                 "scheduled_time": "13:00"},
            ],
            "_warranties": [{"id": "w-1", "serial_number": "SN12345678",
                             "product_name": "แอร์", "status": "active",
                             "warranty_end": "2027-01-01"}],
            "_members": [{"id": "member-1", "chann_uid": "CHN-S-000001",
                          "role": "technician", "status": "active",
                          "first_name": "ช่างเอ"}],
        }
    if name == "customer":
        return {
            "_warranties": [{"id": "w-1", "serial_number": "SN12345678",
                             "product_name": "แอร์", "product_id": "prod-air-1",
                             "warranty_start": "2026-01-01", "warranty_end": "2027-01-01",
                             "status": "active", "customer_chann_uid": "CHN-S-000001"}],
            "_tickets": [{"id": "t1", "ticket_number": "T-2026-0001", "status": "assigned",
                          "accept_status": "accepted", "customer_chann_uid": "CHN-S-000001",
                          "customer_name": "สมชาย", "service_address": "99/1",
                          "issue_description": "แอร์ไม่เย็น",
                          "scheduled_date": "2026-09-12", "scheduled_time": "10:00"}],
        }
    if name == "empty":
        return {}
    # "sales" — the ordinary shop: one customer C-2026-0001 with an open deal,
    # a pending appointment on that customer, a product, a technician, a job.
    return {
        "_customers": [{"id": "CUST-1", "customer_id": "C-2026-0001", "stage": "lead",
                        "first_name": "สมชาย", "last_name": "ใจดี", "phone": "0812345678",
                        "customer_chann_uid": None, "owner_member_id": None,
                        "email": None, "address": None, "notes": None}],
        "_deals": [{"id": "DEAL-1", "deal_id": "D-2026-0001", "contact_id": "CUST-1",
                    "stage": "new", "owner_member_id": None, "notes": None,
                    "products": []}],
        "_follow_ups": [{"id": "FU-1", "entity_type": "customer", "entity_id": "CUST-1",
                         "due_at": "2026-09-12T10:00:00+07:00", "status": "pending",
                         "note": "นัดดูหน้างานกับคุณสมชาย"}],
        "_products": [{"id": "p1", "product_id": "AC12", "product_name": "แอร์ 12000 BTU",
                       "unit_price": "15900.00"}],
        "_members": [{"id": "member-1", "chann_uid": "CHN-S-000001", "role": "sales",
                      "status": "active", "first_name": "พนักงานขาย"},
                     {"id": "m-tech", "chann_uid": "CHN-T-000001", "role": "technician",
                      "status": "active", "first_name": "สมศักดิ์"}],
        "_tickets": [{"id": "t1", "ticket_number": "T-2026-0001", "status": "open",
                      "accept_status": "pending", "customer_name": "สมชาย",
                      "customer_phone": "0812345678", "service_address": "99/1",
                      "issue_description": "แอร์ไม่เย็น", "scheduled_date": "2026-09-08",
                      "scheduled_time": "10:00"}],
        "_quotes": [],
    }


KNOWN_FIXTURES = ("sales", "technician", "customer", "empty")
KNOWN_OAS = ("sales", "technician", "customer")


def build_client(T, case, warnings=None):
    perms = resolve_permissions(case.get("permissions"), warnings)
    role = case.get("role") or ("technician" if case.get("oa") == "technician" else "sales")
    fixture = case.get("fixture")
    preset = case.get("oa") or "sales"
    inline = {}
    if isinstance(fixture, str):
        preset = fixture
    elif isinstance(fixture, dict):
        preset = fixture.get("preset", preset)
        inline = {k: v for k, v in fixture.items() if k != "preset"}
    if warnings is not None:
        if preset not in KNOWN_FIXTURES:
            warnings.append(f"unknown fixture preset {preset!r}; used 'sales'")
        if (case.get("oa") or "sales") not in KNOWN_OAS:
            warnings.append(f"unknown oa {case.get('oa')!r}")
    client = T.FakeDataClient(permission_keys=perms, role=role)
    rows = base_fixture(preset)
    rows.update(inline)
    for key, val in rows.items():
        setattr(client, key if key.startswith("_") else "_" + key, copy.deepcopy(val))
    if case.get("is_owner"):
        client._is_owner = True
    if case.get("member_id"):
        client._member_id = case["member_id"]
    if case.get("pending_intent") is not None:
        client._pending = copy.deepcopy(case["pending_intent"])
    return client, perms, role


def wrap_client(client):
    """Record every public method call independently of the fake's own list.
    A write method that forgets to append to `recorded` is still seen here."""
    observed: list[dict] = []
    depth = {"n": 0}
    for name in dir(type(client)):
        if name.startswith("_"):
            continue
        attr = getattr(client, name, None)
        if not callable(attr):
            continue

        def make(name=name, bound=attr):
            if inspect.iscoroutinefunction(bound):
                async def wrapped(*a, **kw):
                    top = depth["n"] == 0
                    depth["n"] += 1
                    try:
                        return await bound(*a, **kw)
                    finally:
                        depth["n"] -= 1
                        if top:
                            observed.append({"name": name, "kind": classify_call(name),
                                             "args": short((a, kw), 400)})
                return wrapped

            def wrapped(*a, **kw):
                top = depth["n"] == 0
                depth["n"] += 1
                try:
                    return bound(*a, **kw)
                finally:
                    depth["n"] -= 1
                    if top:
                        observed.append({"name": name, "kind": classify_call(name),
                                         "args": short((a, kw), 400)})
            return wrapped

        try:
            setattr(client, name, make())
        except Exception:                                         # noqa: BLE001
            pass
    return observed


def read_recorded(entries, license_id):
    """Turn FakeDataClient.recorded tuples into named, classified calls.

    record_message_entity appends a tuple with NO leading method name
    (tests/unit/test_phase6_chat.py:291); it is recognised by shape and
    flagged name_inferred so the output never pretends to know more than it does."""
    out = []
    for entry in entries:
        seq = list(entry) if isinstance(entry, (tuple, list)) else [entry]
        name = seq[0] if seq else ""
        inferred = False
        if not isinstance(name, str) or classify_call(name) == "unknown":
            if len(seq) == 4 and str(seq[0]) == str(license_id):
                name, inferred = "record_message_entity", True
        out.append({"name": str(name), "kind": classify_call(str(name)),
                    "args": short(seq[1:], 400), "name_inferred": inferred})
    return out


# --------------------------------------------------------------------------
# 8. Running one case.
# --------------------------------------------------------------------------

def empty_turn(n, message):
    return {
        "n": n, "message": message, "reply_text": "", "reply_lines": 0, "reply_chars": 0,
        "quick_replies": [], "reply_fields": {}, "answered_by": {"function": "", "line": 0, "file": ""},
        "handler_chain": [], "reply_sites": [], "events": [],
        "latency_ms": 0.0, "model_consulted": False, "ai_calls": [],
        "calls": [], "observed_calls": [], "reads": [], "writes": [], "conv_writes": [],
        "notifications": [], "line_pushes": [], "unknown_calls": [], "unrecorded_calls": [],
        "state_diff": {"data": [], "conv": []}, "mutated": False,
        "exception": None, "timed_out": False,
    }


async def run_case(T, chat_mod, case, timeout: float):
    global _REC
    rid = str(case.get("id") or "case")
    out = {
        "schema": SCHEMA, "id": rid, "oa": case.get("oa") or "sales",
        "role": "", "language": case.get("language") or "th",
        "seed": case.get("seed"), "permissions": [], "fixture": case.get("fixture") or (case.get("oa") or "sales"),
        "messages": [], "expect": case.get("expect") if isinstance(case.get("expect"), dict) else {},
        "expect_raw": case.get("expect") if not isinstance(case.get("expect"), dict) else None,
        "warnings": [], "turns": [], "totals": {"turns": 0, "ai_calls": 0, "writes": 0,
                                "conv_writes": 0, "notifications": 0,
                                "mutated": False, "latency_ms": 0.0},
        "checks": [], "checks_ok": True, "error": None, "error_trace": "",
    }
    try:
        if case.get("seed") is not None:
            random.seed(case["seed"])
        client, perms, role = build_client(T, case, out["warnings"])
        out["permissions"], out["role"] = perms, role
        observed = wrap_client(client)
        ctx = T._ctx(oa=out["oa"], primary_role=role)
        raw_messages = case.get("messages") or []
        if isinstance(raw_messages, str):
            raw_messages = [raw_messages]

        for i, item in enumerate(raw_messages, start=1):
            if isinstance(item, str):
                text, per_turn_ai = item, None
            elif isinstance(item, dict):
                text, per_turn_ai = str(item.get("text", "")), item.get("ai")
            else:                                      # a number, a null, a list
                text, per_turn_ai = str(item), None
            out["messages"].append(text)
            turn = empty_turn(i, text)
            probe = AiProbe(per_turn_ai if per_turn_ai is not None else case.get("ai"))
            rec = Recorder()
            before = snapshot(client)
            rec_start = len(client.recorded)
            obs_start = len(observed)
            _REC = rec
            t0 = time.perf_counter()
            reply = None
            try:
                reply = await asyncio.wait_for(
                    chat_mod.handle_chat_message(
                        client, message=text, ctx=ctx,
                        language=out["language"], ai_client=probe.client,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                turn["timed_out"] = True
                turn["exception"] = f"TimeoutError after {timeout}s"
            except Exception as exc:                              # noqa: BLE001
                turn["exception"] = f"{type(exc).__name__}: {exc}"
                turn["exception_trace"] = traceback.format_exc()[-2000:]
            finally:
                turn["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
                _REC = None
                await probe.aclose()

            after = snapshot(client)
            data_diff, conv_diff = diff_state(before, after)
            recorded = read_recorded(client.recorded[rec_start:], ctx.license_id)
            obs = observed[obs_start:]

            if reply is not None:
                text_out = reply.text or ""
                turn["reply_text"] = text_out
                turn["reply_lines"] = text_out.count("\n") + 1 if text_out else 0
                turn["reply_chars"] = len(text_out)
                turn["quick_replies"] = [list(q) for q in (reply.quick_replies or [])]
                fields = {}
                for f in getattr(reply, "__dataclass_fields__", {}):
                    if f in ("text", "quick_replies"):
                        continue
                    fields[f] = jsonable(getattr(reply, f, None))
                turn["reply_fields"] = fields
                turn["answered_by"] = getattr(reply, "_probe_origin", None) or (
                    rec.replies[-1] if rec.replies else {"function": "", "line": 0, "file": ""})
            elif rec.replies:
                turn["answered_by"] = rec.replies[-1]

            chain, prev = [], None
            for name in rec.chain:
                if name != prev:
                    chain.append(name)
                prev = name
            turn["handler_chain"] = chain
            turn["reply_sites"] = rec.replies[:40]
            turn["events"] = rec.events[:400]
            turn["ai_calls"] = probe.calls
            turn["model_consulted"] = bool(probe.calls)
            turn["calls"] = recorded
            turn["observed_calls"] = obs
            turn["line_pushes"] = rec.pushes

            def named(items, kinds):
                return [{"name": c["name"], "args": c.get("args", "")}
                        for c in items if c["kind"] in kinds]

            # The fake's own list is the primary record (the brief's
            # instruction). The wrapper is the backstop: a write method that
            # forgets to append to `recorded` still shows up, counted by
            # NAME rather than by argument repr, because the two sources
            # spell their arguments differently.
            writes = named(recorded, ("write", "write?"))
            have: dict = {}
            for w in writes:
                have[w["name"]] = have.get(w["name"], 0) + 1
            for w in named(obs, ("write", "write?")):
                if have.get(w["name"], 0) > 0:
                    have[w["name"]] -= 1
                else:
                    w["only_seen_by_wrapper"] = True
                    writes.append(w)
            turn["writes"] = writes
            turn["reads"] = sorted({c["name"] for c in obs if c["kind"] in ("read", "read?")})
            turn["conv_writes"] = named(recorded, ("conv",))
            turn["notifications"] = named(recorded, ("notify",))
            turn["unknown_calls"] = sorted({c["name"] for c in recorded + obs
                                            if c["kind"] == "unknown"})
            rec_counts: dict = {}
            for c in recorded:
                rec_counts[c["name"]] = rec_counts.get(c["name"], 0) + 1
            missing = []
            for c in obs:
                if rec_counts.get(c["name"], 0) > 0:
                    rec_counts[c["name"]] -= 1
                elif c["kind"] in ("write", "write?", "conv", "notify"):
                    missing.append(c["name"])
            turn["unrecorded_calls"] = sorted(set(missing))
            turn["state_diff"] = {"data": data_diff, "conv": conv_diff}
            turn["mutated"] = bool(writes or data_diff)

            out["turns"].append(turn)
            out["totals"]["ai_calls"] += len(probe.calls)
            out["totals"]["writes"] += len(writes)
            out["totals"]["conv_writes"] += len(turn["conv_writes"])
            out["totals"]["notifications"] += len(turn["notifications"])
            out["totals"]["latency_ms"] = round(
                out["totals"]["latency_ms"] + turn["latency_ms"], 3)
            out["totals"]["mutated"] = out["totals"]["mutated"] or turn["mutated"]
        out["totals"]["turns"] = len(out["turns"])
    except Exception as exc:                                      # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["error_trace"] = traceback.format_exc()[-3000:]
    finally:
        _REC = None

    out["checks"] = check(out)
    out["checks_ok"] = all(c["ok"] for c in out["checks"])
    return out


# --------------------------------------------------------------------------
# 9. Expectations. Free-form and non-fatal: a failed check is data, not a stop.
# --------------------------------------------------------------------------

def check(out):
    exp = out.get("expect") or {}
    turns = out.get("turns") or []
    last = turns[-1] if turns else empty_turn(0, "")
    all_text = "\n".join(t["reply_text"] for t in turns)
    results = []

    def add(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})

    if out.get("error"):
        add("no_harness_error", False, out["error"])
    if any(t.get("exception") for t in turns):
        add("no_exception", False, "; ".join(
            t["exception"] for t in turns if t.get("exception"))[:400])
    if not isinstance(exp, dict) or not exp:
        return results
    if "no_mutation" in exp:
        writes = [w["name"] for t in turns for w in t["writes"]]
        diffs = [d["path"] for t in turns for d in t["state_diff"]["data"]]
        ok = (not writes and not diffs) if exp["no_mutation"] else bool(writes or diffs)
        add("no_mutation", ok, f"writes={writes} data_diff={diffs}")
    if "mutations" in exp:
        writes = sorted({w["name"] for t in turns for w in t["writes"]})
        add("mutations", writes == sorted(exp["mutations"]), f"got {writes}")
    if "model_consulted" in exp:
        got = any(t["model_consulted"] for t in turns)
        add("model_consulted", got == bool(exp["model_consulted"]), f"got {got}")
    for key, negate in (("reply_contains", False), ("reply_not_contains", True)):
        if key in exp:
            needles = exp[key]
            needles = [needles] if isinstance(needles, str) else list(needles)
            for needle in needles:
                hit = needle in all_text
                add(f"{key}:{needle[:30]}", (not hit) if negate else hit,
                    "" if (hit != negate) else "not matched")
    if "answered_by" in exp:
        got = last["answered_by"].get("function", "")
        add("answered_by", exp["answered_by"] in got, f"got {got}")
    if "handler" in exp:
        add("handler", exp["handler"] in last["handler_chain"],
            f"got {last['handler_chain']}")
    if "notifications" in exp:
        got = sorted({n["name"] for t in turns for n in t["notifications"]})
        add("notifications", got == sorted(exp["notifications"]), f"got {got}")
    return results


# --------------------------------------------------------------------------
# 10. Input, output, CLI.
# --------------------------------------------------------------------------

def load_cases(source: str | None):
    if source and os.path.exists(source):
        raw = open(source, encoding="utf-8").read()
    else:
        raw = sys.stdin.read()
    raw = raw.strip()
    if not raw:
        return []
    if raw[0] == "[":
        return json.loads(raw)
    cases = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


SELFTEST_CASES = [
    {"id": "neg-cancel-appointment", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["ไม่ต้องยกเลิกนัด C-2026-0001"],
     "expect": {"no_mutation": True},
     "why": "the owner's example: a refusal to cancel must not cancel"},
    {"id": "cancel-appointment-control", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["ยกเลิกนัด C-2026-0001"],
     "expect": {"no_mutation": False},
     "why": "control: the real instruction MUST mutate, or the case above proves nothing"},
    {"id": "invite-technician", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["ขอรหัสเชิญช่าง"],
     "expect": {"model_consulted": False},
     "why": "must work"},
    {"id": "invite-bare", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["ขอรหัสเชิญ"],
     "expect": {},
     "why": "who is being invited? show what actually happens"},
    {"id": "invite-bare-model-understands", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": [{"text": "ขอรหัสเชิญ",
                   "ai": {"action": "create", "entity": "invite",
                          "fields": {"role": "technician"}, "missing": []}}],
     "expect": {"model_consulted": True},
     "why": "same words, but the model DOES read them as an invite: is there a handler?"},
    {"id": "quote-status-question", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["สร้างใบเสนอราคาไปหรือยัง"],
     "expect": {"no_mutation": True},
     "why": "a question about status must not create"},
    {"id": "fault-with-negation", "oa": "customer", "role": "customer",
     "permissions": "customer", "language": "th", "seed": 1,
     "messages": ["แอร์ไม่เย็น"],
     "expect": {},
     "why": "a symptom containing ไม่ must not be refused"},
    {"id": "two-turn-followup", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": ["ข้อมูลลูกค้า C-2026-0001", "เบอร์อะไรนะ"],
     "expect": {},
     "why": "the second message refers to the first"},
    {"id": "two-turn-mind-change", "oa": "sales", "role": "sales",
     "permissions": "sales", "language": "th", "seed": 1,
     "messages": [{"text": "เพิ่มลูกค้าใหม่",
                   "ai": {"action": "create", "entity": "customer", "fields": {},
                          "missing": ["first_name", "phone"]}},
                  {"text": "ขอดูรายชื่อลูกค้าแทน",
                   "ai": {"action": "read", "entity": "customer", "fields": {}, "missing": []}}],
     "expect": {},
     "why": "changing task mid-flow: what happens to the half-finished record"},
    {"id": "ai-down", "oa": "sales", "role": "sales", "permissions": "sales",
     "language": "th", "seed": 1,
     "messages": [{"text": "สมชายจะซื้อแอร์สองตัวพรุ่งนี้",
                   "ai": {"__status__": 500}}],
     "expect": {"no_mutation": True},
     "why": "one bad model call must not kill the run, and must not write"},
]


def digest(rec):
    turns = rec.get("turns") or []
    bits = []
    for t in turns:
        origin = t["answered_by"]
        bits.append(
            f"    #{t['n']} {t['message'][:34]!r}\n"
            f"        branch   {origin.get('function','')}:{origin.get('line',0)}"
            f"  chain={'>'.join(t['handler_chain'][-3:]) or '-'}\n"
            f"        model    {'YES' if t['model_consulted'] else 'no '}"
            f"  writes={[w['name'] for w in t['writes']] or '-'}"
            f"  conv={[c['name'] for c in t['conv_writes']] or '-'}"
            f"  diff={[d['path'] for d in t['state_diff']['data']] or '-'}\n"
            f"        {t['latency_ms']}ms  reply={t['reply_text'][:64]!r}"
            + (f"\n        EXC {t['exception']}" if t.get("exception") else "")
        )
    flag = "ok " if rec["checks_ok"] and not rec["error"] else "!! "
    fails = [c["name"] + "(" + c["detail"][:60] + ")" for c in rec["checks"] if not c["ok"]]
    return (f"{flag}{rec['id']}  {rec['oa']}/{rec['role']}"
            + (f"  FAILED: {', '.join(fails)}" if fails else "")
            + "\n" + "\n".join(bits))


async def main_async(args):
    T, chat_mod, notify_mod, live_chat_mod = bootstrap(args.repo)
    install(chat_mod, notify_mod, live_chat_mod)
    cases = SELFTEST_CASES if args.selftest else load_cases(args.cases)
    # One throwaway route call first: chat.py imports several modules lazily,
    # and without this the first real case's latency carries the import cost.
    try:
        await run_case(T, chat_mod, {"id": "__warmup__", "oa": "sales",
                                     "messages": ["สวัสดี"], "expect": {}}, args.timeout)
    except Exception:                                             # noqa: BLE001
        pass
    out_fh = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    n_ok = 0
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            case = {"id": f"case-{i}", "messages": [str(case)]}
        try:
            rec = await run_case(T, chat_mod, case, args.timeout)
        except Exception as exc:                                  # noqa: BLE001
            rec = {"schema": SCHEMA, "id": str(case.get("id", f"case-{i}")),
                   "oa": case.get("oa", ""), "role": "", "language": "", "seed": None,
                   "permissions": [], "fixture": "", "messages": [], "expect": {},
                   "expect_raw": None, "warnings": [], "turns": [],
                   "totals": {"turns": 0, "ai_calls": 0, "writes": 0, "conv_writes": 0,
                              "notifications": 0, "mutated": False, "latency_ms": 0.0},
                   "checks": [], "checks_ok": False,
                   "error": f"{type(exc).__name__}: {exc}",
                   "error_trace": traceback.format_exc()[-3000:]}
        out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_fh.flush()
        n_ok += 1 if (rec["checks_ok"] and not rec["error"]) else 0
        if args.summary:
            print(digest(rec), file=sys.stderr)
    if args.out:
        out_fh.close()
    if args.summary:
        print(f"\n=== {len(cases)} cases · {n_ok} clean · {len(cases) - n_ok} flagged ===",
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cases", nargs="?", help="path to a .json list or .jsonl of cases; omit to read stdin")
    ap.add_argument("--repo", default=os.environ.get("PROBE_REPO", DEFAULT_REPO))
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default=None, help="write JSONL here instead of stdout")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="run the ten built-in cases")
    ap.add_argument("--prompt-chars", type=int, default=0,
                    help="truncate the captured system prompt to N chars (0 = keep it whole). "
                         "system_prompt_sha is always recorded, so identical prompts stay identifiable.")
    ap.add_argument("--real-model", action="store_true",
                    help="call the CONFIGURED provider instead of the stub (billable). "
                         "OPENROUTER_API_KEY and OPENROUTER_MODEL must already be in the "
                         "environment; neither is ever printed. Everything else about the "
                         "run is identical, so a stubbed run and a real one are directly "
                         "comparable and the only difference is who wrote the answer.")
    args = ap.parse_args()
    AiProbe.prompt_chars = max(0, args.prompt_chars)
    AiProbe.real_model = bool(args.real_model)
    if AiProbe.real_model:
        import os as _os
        if not _os.environ.get("OPENROUTER_API_KEY") or not _os.environ.get("OPENROUTER_MODEL"):
            raise SystemExit(
                "--real-model needs OPENROUTER_API_KEY and OPENROUTER_MODEL in the "
                "environment. Values are never printed by this script."
            )
        print(
            "# real-model run: answers come from the configured provider, "
            "and every call is billed.", file=sys.stderr,
        )
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
