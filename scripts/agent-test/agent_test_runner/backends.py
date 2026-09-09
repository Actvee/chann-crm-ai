"""The two ways a scenario can be executed.

`fake` runs the real chat engine against the fake Data client the unit suite
uses — the same path scripts/dev/simulate-*.py take. No database, no HTTP,
a whole scenario in milliseconds. It proves the Application tier: routing,
wording, permissions, quick replies.

`db` runs the same chat engine against the real Data tier, in-process over
its actual HTTP surface, on a migrated PostgreSQL schema — the pattern
tests/integration/test_http_journey.py established. It is slower and it needs
a PostgreSQL (see database_url below, which gives this working copy its own
database rather than sharing one), but it is the only one that proves the
seam between the tiers, and the only one where registration, invites and
per-OA membership are real.

Both backends answer the same step vocabulary, so a scenario that does not
reach for a backend-only feature runs unchanged on either.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import httpx

from .assertions import Outcome

# Named permission sets, so a scenario says `permissions: sales` instead of
# pasting thirty keys. Kept in step with scripts/dev/simulate-phrasings.py,
# which is where these three lists were first written down.
SALES_KEYS = [
    "customer.create", "customer.read", "customer.update", "customer.archive",
    "deal.create", "deal.read", "deal.update", "quote.create", "quote.read",
    "quote.update", "product.manage", "followup.create", "followup.read",
    "followup.update", "note.create", "note.read", "ticket.read",
    "ticket.create", "ticket.update", "ticket.assign", "service_report.read",
    "warranty.read", "warranty.create", "team.manage", "member.manage",
    "setting.manage", "approval.view", "approval.approve", "approval.reject",
    "approval.manage", "view_reports",
]
TECHNICIAN_KEYS = [
    "ticket.read", "ticket.update", "ticket.close", "service_report.create",
    "service_report.read", "warranty.read",
]
CUSTOMER_KEYS = [
    "customer.read", "ticket.create", "ticket.read", "warranty.read",
    "warranty.create",
]

SUGGEST = {"action": "suggest", "entity": None, "fields": {}, "missing": []}


class BackendError(Exception):
    """Something a scenario asked for that this backend cannot do, said in
    terms of what to change in the scenario."""


def permission_set(spec, oa: str) -> list[str]:
    """A named set, an explicit list, or the set that matches the OA."""
    if spec is None:
        spec = oa
    if isinstance(spec, list):
        return list(spec)
    named = {
        "sales": SALES_KEYS, "technician": TECHNICIAN_KEYS,
        "customer": CUSTOMER_KEYS, "none": [],
    }
    if spec == "all":
        from chann_data.permissions import PERMISSION_KEYS

        return sorted(PERMISSION_KEYS)
    if spec in named:
        return list(named[spec])
    raise BackendError(
        f"unknown permission set {spec!r}. Use a list of permission keys, or "
        f"one of: all, none, {', '.join(sorted(named))}"
    )


class AiProbe:
    """Answers every model call in-process, and remembers it was asked.

    A scenario says what the model would have replied; the probe hands that
    back. Nothing reaches OpenRouter — which is both a cost guarantee and the
    reason a run is deterministic. `used_ai` in an assertion reads `calls`,
    so a scenario can insist an answer came from the deterministic layer.
    """

    def __init__(self, answer: dict | None = None):
        self.calls = 0
        self._answer = json.dumps(answer or SUGGEST, ensure_ascii=False)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": self._answer}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "provider": "agent-test-channel",
        })


class _MemoryRedis:
    """A stand-in for the Data tier's Redis, inside this process.

    Not an optimisation — a correctness requirement for the db backend. The
    Data tier keeps conversational scratch state in Redis on purpose
    (pending_intent, last_entity_ref: short-lived, no audit trail), and its
    documented degrade with Redis down is "ask fresh". With no Redis, every
    multi-turn scenario would therefore restart at every message and the
    backend could only ever test one-shot commands.

    Only the four calls chann_data.cache actually makes are implemented, so
    a fifth one appearing in the Data tier fails loudly here rather than
    being quietly emulated wrong. What this does NOT cover is real Redis
    behaviour under eviction, or two processes sharing one cache — see the
    README's "what this cannot test".
    """

    def __init__(self):
        import time as _time

        self._time = _time
        self._store: dict[str, tuple[float | None, str]] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at is not None and expires_at <= self._time.time():
            self._store.pop(key, None)
            return None
        return value

    def setex(self, key: str, ttl_s: int, value: str) -> None:
        expires_at = self._time.time() + ttl_s if ttl_s and ttl_s > 0 else None
        self._store[key] = (expires_at, value)

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)


def _resolve(value: Any, refs: dict) -> Any:
    """Substitute `$ref` and `$ref.field` from rows seeded earlier.

    Scenarios need to say "the deal for the customer I just created" without
    knowing what id the system will hand out. One dollar-prefixed token is
    the whole syntax; anything more would be a template language, and a
    template language in a test file is a second thing to debug.
    """
    if isinstance(value, str) and value.startswith("$"):
        token = value[1:]
        name, _, field = token.partition(".")
        if name not in refs:
            raise BackendError(
                f"reference {value!r} points at nothing — "
                f"known refs: {', '.join(sorted(refs)) or '(none yet)'}"
            )
        row = refs[name]
        if not field:
            return row.get("id", row)
        if field not in row:
            raise BackendError(
                f"reference {value!r}: the seeded row has no {field!r} "
                f"(it has {', '.join(sorted(row))})"
            )
        return row[field]
    if isinstance(value, dict):
        return {k: _resolve(v, refs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, refs) for v in value]
    return value


# The seed collections both backends understand, and the client call each one
# makes. Going through the client rather than writing rows directly is what
# keeps a seed honest: the real tier's validation runs, so a scenario cannot
# seed a shape production would reject.
async def _seed_through_client(client, license_id, spec: dict, refs: dict) -> None:
    for row in spec.get("products", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        product_id = row.get("product_id") or row.get("product_name")
        created = await client.upsert_product(license_id, product_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("customers", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.create_customer(license_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("deals", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.create_deal(license_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("quotes", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.create_quote(license_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("tickets", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.create_ticket(license_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("warranties", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.register_warranty(license_id, row)
        if ref:
            refs[ref] = created
    for row in spec.get("invites", []):
        row = _resolve(row, refs)
        ref = row.pop("ref", None)
        created = await client.create_invite(license_id, row)
        if ref:
            refs[ref] = created
    for call in spec.get("call", []):
        call = _resolve(call, refs)
        method = call.get("method")
        if not method or not hasattr(client, method):
            raise BackendError(
                f"seed.call: the data client has no method {method!r}"
            )
        ref = call.get("ref")
        args = call.get("args") or {}
        result = await getattr(client, method)(license_id, **args)
        if ref:
            refs[ref] = result if isinstance(result, dict) else {"id": result}


class FakeBackend:
    """The chat engine against the unit suite's fake Data client."""

    name = "fake"

    def __init__(self):
        from .bootstrap import prepare

        prepare()
        import test_phase6_chat as chat_tests

        self._t = chat_tests
        self.client = None
        self.license_id = None
        self.reset({})

    def reset(self, actor: dict) -> None:
        oa = actor.get("oa", "sales")
        self.client = self._t.FakeDataClient(
            permission_keys=permission_set(actor.get("permissions"), oa),
            role=actor.get("role") or ("technician" if oa == "technician" else "sales"),
        )
        self.license_id = self._t.LICENSE_ID

    async def send(self, *, message, oa, role, language, permissions, ai,
                   refs) -> tuple[Outcome, list[str]]:
        # The fake carries the permission set and the role as plain state, so
        # a step can change persona mid-scenario — which is exactly what a
        # "now try it without the permission" test case needs.
        self.client._permission_keys = permission_set(permissions, oa)
        self.client._role = role
        probe = AiProbe(_resolve(ai, refs) if ai else None)
        ctx = self._t._ctx(oa=oa, primary_role=role)
        try:
            reply = await self._t.handle_chat_message(
                self.client, message=message, ctx=ctx, language=language,
                ai_client=probe.client,
            )
        finally:
            await probe.client.aclose()
        return Outcome(
            text=reply.text or "",
            quick_replies=[tuple(q) for q in (reply.quick_replies or [])],
            list_card=reply.list_card,
            images=list(reply.images or []),
            intent=reply.intent,
            used_ai=probe.calls > 0,
        ), []

    async def seed(self, spec: dict, refs: dict) -> list[str]:
        notes: list[str] = []
        await _seed_through_client(self.client, self.license_id, spec, refs)
        for row in spec.get("members", []):
            row = _resolve(row, refs)
            ref = row.pop("ref", None)
            rows = list(getattr(self.client, "_members", []))
            row.setdefault("id", f"member-{len(rows) + 1}")
            row.setdefault("status", "active")
            rows.append(row)
            self.client._members = rows
            if ref:
                refs[ref] = row
        if "redeem" in spec:
            raise BackendError(
                "seed.redeem needs a real registration table — set "
                "`backend: db` on this scenario"
            )
        for attribute, rows in (spec.get("raw") or {}).items():
            setattr(self.client, f"_{attribute}", _resolve(rows, refs))
            notes.append(f"seeded {attribute} directly on the fake client "
                         f"(fake backend only)")
        return notes

    async def http(self, **kwargs) -> tuple[Outcome, list[str]]:
        raise BackendError(
            "the fake backend has no HTTP surface — an `http` step needs "
            "`backend: db` (and TEST_DATABASE_URL set)"
        )

    async def close(self) -> None:
        return None


class DbBackend:
    """The chat engine and the dashboard's own routes against the real Data
    tier, on a migrated PostgreSQL schema.

    Both FastAPI apps are mounted on in-process ASGI transports rather than
    sockets: the routing, the request validation and the response models are
    the real ones, but nothing listens on a port and nothing leaves the
    machine. `reset` makes a new tenant rather than a new schema — the
    migration runs once per process, and a fresh licence is enough isolation
    because every query in the Data tier is tenant-scoped.
    """

    name = "db"

    def __init__(self, database_url: str):
        from .bootstrap import prepare

        self.root = prepare()
        self.database_url = database_url
        self._engine = None
        self._session_factory = None
        self._data_app = None
        self._app_app = None
        self.client = None
        self.license_id = None
        self._identities: dict[str, dict] = {}

    # ------------------------------------------------------------ setup

    def _rebuild_schema(self) -> None:
        """Empty the test database, the way tests/integration/conftest.py does.

        Two extra precautions, both learned here: sessions left behind by an
        earlier run (this harness, or the integration suite) are terminated
        first, because one idle-in-transaction connection turns DROP SCHEMA
        into a lock wait and then into a migration that fails halfway with a
        message about a table; and the connection doing the drop is disposed
        before alembic starts, so the migration is not racing this process for
        the schema it just recreated.
        """
        from sqlalchemy import create_engine, text

        engine = create_engine(self.database_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                ))
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
        finally:
            engine.dispose()

    def _migrate(self) -> None:
        import subprocess
        import sys

        from sqlalchemy import create_engine

        env = {**os.environ, "DATABASE_URL": self.database_url}
        last = ""
        # Two attempts: the first failure is nearly always contention with a
        # connection that has only just gone away, and a second clean rebuild
        # settles it. A third would only hide a real problem.
        for _ in range(2):
            self._rebuild_schema()
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=str(self.root / "database"), env=env,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                self._engine = create_engine(self.database_url, future=True)
                return
            last = (result.stdout + "\n" + result.stderr)[-1500:]
        raise BackendError(
            "alembic upgrade head failed twice — the db backend needs a "
            "reachable PostgreSQL at TEST_DATABASE_URL, and the schema it "
            f"points at is now empty:\n{last}"
        )

    def start(self) -> None:
        from sqlalchemy.orm import sessionmaker

        self._migrate()
        from chann_data import config as data_config
        from chann_data.db import get_session as data_get_session
        from chann_data.main import app as data_app

        from chann_data.cache import cache

        data_config.settings.admin_secret = "agent-test-channel-secret"
        cache._client = _MemoryRedis()
        self._cache = cache
        self._session_factory = sessionmaker(bind=self._engine, future=True)

        def _session():
            session = self._session_factory()
            try:
                yield session
            finally:
                session.close()

        data_app.dependency_overrides[data_get_session] = _session
        self._data_app = data_app

        from chann_app import data_client as dc
        from chann_app.main import app as application_app
        from chann_app.routers_admin import get_data_client

        transport = httpx.ASGITransport(app=data_app)

        class _WiredClient(dc.DataClient):
            def __init__(self):
                super().__init__(base_url="http://data",
                                 secret="agent-test-channel-secret")
                self._client = httpx.AsyncClient(
                    transport=transport, base_url="http://data",
                )

        self.client = _WiredClient()
        application_app.dependency_overrides[get_data_client] = lambda: self.client
        self._app_app = application_app
        self.reset({})

    # ------------------------------------------------------------ tenant

    def reset(self, actor: dict) -> None:
        """A brand-new licence, with the scenario's actor as its owner."""
        from sqlalchemy.orm import Session

        from chann_data.models import ChannIdentity
        from chann_data.repositories.phase65 import RegistrationRepository

        # A fresh cache with the fresh tenant: conversational scratch state
        # from the previous scenario must not leak into the next one, and a
        # stale permission entry would be worse than a slow lookup.
        if getattr(self, "_cache", None) is not None:
            self._cache._client = _MemoryRedis()
        self._warned_about_permissions = False

        tag = uuid.uuid4().hex[:8]
        owner_uid = f"CHN-AT-{tag}"
        with Session(self._engine) as session:
            session.add(ChannIdentity(
                chann_uid=owner_uid, line_user_id=f"line-at-{tag}",
                primary_role="sales", display_name="Agent Test Owner",
            ))
            session.commit()
        with Session(self._engine) as session:
            licence = RegistrationRepository(session).create_license(
                company_name=f"ร้านทดสอบ {tag}", created_by_chann_uid=owner_uid,
            )
            session.commit()
            self.license_id = str(licence.id)
        self._identities = {"sales": {"chann_uid": owner_uid,
                                      "line_user_id": f"line-at-{tag}"}}
        self._tag = tag

    def _line_user_id(self, oa: str, explicit: str | None) -> str:
        """One LINE account per scenario by default.

        Migration 0026's whole point is that the same LINE identity is a
        different registration on each OA, so the default has to be the SAME
        line_user_id everywhere — otherwise a scenario about per-OA personas
        would be testing three different people and always pass.
        """
        if explicit:
            return explicit
        return f"line-at-{self._tag}"

    # ------------------------------------------------------------ steps

    async def send(self, *, message, oa, role, language, permissions, ai,
                   refs) -> tuple[Outcome, list[str]]:
        from chann_app.services.identity import resolve_context

        notes: list[str] = []
        # Said once per scenario, not once per message: a note repeated on
        # every step trains the reader to skip notes.
        if permissions is not None and not self._warned_about_permissions:
            self._warned_about_permissions = True
            notes.append(
                "`permissions` was ignored: on the db backend the member's "
                "role in license_members decides, which is the point of "
                "running against the real tier"
            )
        ctx = await resolve_context(
            self.client, oa, self._line_user_id(oa, None), "Agent Test",
        )
        probe = AiProbe(_resolve(ai, refs) if ai else None)
        try:
            reply = await self._t_handle(message, ctx, language, probe)
        finally:
            await probe.client.aclose()
        return Outcome(
            text=reply.text or "",
            quick_replies=[tuple(q) for q in (reply.quick_replies or [])],
            list_card=reply.list_card,
            images=list(reply.images or []),
            intent=reply.intent,
            used_ai=probe.calls > 0,
        ), notes

    async def _t_handle(self, message, ctx, language, probe):
        from chann_app.services.chat import handle_chat_message

        return await handle_chat_message(
            self.client, message=message, ctx=ctx, language=language,
            ai_client=probe.client,
        )

    async def seed(self, spec: dict, refs: dict) -> list[str]:
        notes: list[str] = []
        if "raw" in spec:
            raise BackendError(
                "seed.raw writes onto the fake client's private state and has "
                "no meaning against a real database — use the real collections "
                "or `seed.call`"
            )
        if "members" in spec:
            raise BackendError(
                "seed.members invents a membership row; on the db backend a "
                "member is made by redeeming an invite — use seed.invites plus "
                "seed.redeem"
            )
        await _seed_through_client(self.client, self.license_id, spec, refs)
        for row in spec.get("redeem", []):
            row = _resolve(row, refs)
            ref = row.pop("ref", None)
            oa = row.get("oa", "technician")
            line_user_id = self._line_user_id(oa, row.get("line_user_id"))
            identity = await self.client.resolve_identity(
                line_user_id, "technician" if oa == "technician" else "sales",
                row.get("display_name") or "Agent Test",
            )
            member = await self.client.redeem_invite(
                invite_code=row["invite"], chann_uid=identity["chann_uid"],
                display_name=row.get("display_name"), oa=oa,
            )
            if ref:
                refs[ref] = member
            notes.append(f"{identity['chann_uid']} redeemed a {oa} invite")
        return notes

    async def http(self, *, method, path, body, params, principal, refs
                   ) -> tuple[Outcome, list[str]]:
        from chann_app.routers_phase2 import get_tenant_principal
        from chann_app.services.authorization import TenantPrincipal

        principal = principal or {}
        oa = principal.get("oa", "sales")
        keys = permission_set(principal.get("permissions", "all"), oa)
        identity = self._identities.get("sales", {})
        who = TenantPrincipal(
            chann_uid=principal.get("chann_uid") or identity.get("chann_uid", ""),
            license_id=self.license_id,
            role=principal.get("role", "owner"),
            is_owner=bool(principal.get("is_owner", True)),
            permission_keys=frozenset(keys),
            audience=oa,
        )
        self._app_app.dependency_overrides[get_tenant_principal] = lambda: who
        url = _resolve(path, refs).replace("{license_id}", self.license_id)
        transport = httpx.ASGITransport(app=self._app_app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://application") as http_client:
            response = await http_client.request(
                method, url, json=_resolve(body, refs) if body else None,
                params=_resolve(params, refs) if params else None,
            )
        try:
            parsed = response.json()
        except ValueError:
            parsed = response.text
        return Outcome(status=response.status_code, body=parsed,
                       text=response.text), []

    async def close(self) -> None:
        if self._data_app is not None:
            self._data_app.dependency_overrides.clear()
        if self._app_app is not None:
            self._app_app.dependency_overrides.clear()
        if self.client is not None:
            await self.client._client.aclose()
        if self._engine is not None:
            # Left-open pooled connections keep a lock on the public schema,
            # and the next thing to run against this database is usually
            # tests/integration, whose fixture starts with DROP SCHEMA.
            self._engine.dispose()


def database_url() -> str:
    """Which database the db backend rebuilds — its own, by default.

    The first thing this backend does is DROP SCHEMA, so it must never point
    at a database anything else is using. `TEST_DATABASE_URL` is shared: the
    integration suite uses it, and several checkouts of this repository can
    be running at once, which has already cost two people a run. So the
    server, the credentials and the port come from TEST_DATABASE_URL while
    the database NAME is derived from this working copy's directory, and the
    database is created on first use.

    `AGENT_TEST_DATABASE_URL` overrides the whole thing for anyone who wants
    to say exactly where it goes.
    """
    explicit = os.environ.get("AGENT_TEST_DATABASE_URL", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not base:
        raise BackendError(
            "the db backend needs a PostgreSQL to talk to. Either\n"
            "  export TEST_DATABASE_URL="
            "postgresql+psycopg://chann:chann@127.0.0.1:5432/chann_crm_ai_test\n"
            "(the server and credentials are taken from it; the database name "
            "is this working copy's own, so the shared one is never touched)\n"
            "or set AGENT_TEST_DATABASE_URL to the exact database to use."
        )
    from sqlalchemy.engine import make_url

    url = make_url(base)
    from .bootstrap import REPO_ROOT

    suffix = re.sub(r"[^a-z0-9_]", "_", REPO_ROOT.name.lower())[:24] or "worktree"
    return str(url.set(database=f"chann_agent_test_{suffix}"))


def ensure_database(url: str) -> None:
    """Create the harness's own database if it is not there yet.

    Deliberately not dropped afterwards: rebuilding one empty database is
    cheap, and a database left behind is easier to explain than a run that
    fails because someone else's run removed it mid-flight.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    target = make_url(url)
    admin = create_engine(
        str(target.set(database="postgres")), future=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    except Exception as exc:  # noqa: BLE001 - reported as a backend problem
        raise BackendError(
            f"could not reach PostgreSQL to prepare {target.database!r}: {exc}"
        ) from None
    finally:
        admin.dispose()


def make_backend(name: str) -> FakeBackend | DbBackend:
    if name == "fake":
        return FakeBackend()
    if name == "db":
        url = database_url()
        ensure_database(url)
        backend = DbBackend(url)
        backend.start()
        return backend
    raise BackendError(f"unknown backend {name!r} — use fake or db")
