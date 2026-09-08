"""Reading and checking a scenario file before anything is executed.

Validation is strict and happens up front, for one reason: the author of a
scenario is usually an AI that cannot see a traceback in context. A typo in
an assertion name must come back as "unknown assertion 'contain' at
scenarios/x.yaml step 3 — did you mean 'contains'?", not as a step that
silently passed because nobody looked at that key.

Unknown keys are therefore errors, never ignored. A scenario that runs is a
scenario every word of which was understood.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

OAS = ("sales", "technician", "customer")
LANGUAGES = ("th", "en")
BACKENDS = ("fake", "db", "any")

# The reply shapes that mean the system failed the person. Kept identical to
# the BAD map in scripts/dev/simulate-phrasings.py — one vocabulary for
# "this answer is a failure", so a finding here and a finding there mean the
# same thing. Change one, change both.
BAD_CLASSES: dict[str, tuple[str, ...]] = {
    "generic_error": ("ขออภัย",),
    "not_sure": ("ยังไม่แน่ใจว่าต้องการอะไร",),
    "permission": ("คุณยังไม่มีสิทธิ์", "ยังไม่มีสิทธิ์ใช้งาน"),
    "not_a_feature": ("ระบบยังไม่มีฟังก์ชันนี้",),
    "ai_down": ("ระบบไม่พร้อมใช้งาน",),
    "not_found": ("ไม่พบ",),
}

EXPECT_KEYS = {
    "contains", "not_contains", "regex", "quick_replies_include", "has_image",
    "intent", "max_lines", "max_chars", "is_not", "used_ai",
    # http-only
    "status", "json_path", "json_contains",
}

SEND_KEYS = {"message", "oa", "role", "language", "permissions", "ai"}
SEED_KEYS = {
    "products", "customers", "deals", "tickets", "warranties", "quotes",
    "members", "invites", "redeem", "raw", "call",
}
HTTP_KEYS = {"method", "path", "json", "params", "as"}
ACTOR_KEYS = {"oa", "role", "language", "permissions", "line_user_id"}
STEP_VERBS = ("send", "seed", "reset", "http")


class ScenarioError(Exception):
    """A scenario file the runner refuses to run, with where and why."""


@dataclass
class Scenario:
    name: str
    path: Path
    description: str = ""
    backend: str = "any"
    actor: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)

    def runs_on(self, backend: str) -> bool:
        return self.backend in ("any", backend)


def _fail(where: str, message: str) -> None:
    raise ScenarioError(f"{where}: {message}")


def _as_list(value: Any) -> list:
    """One string or many is the same assertion. Scenario authors write the
    singular far more often, and forcing a list on them buys nothing."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _suggest(unknown: str, known) -> str:
    """Nearest known key by shared prefix — enough to catch the plural/typo
    mistakes that are 90% of what goes wrong, without a fuzzy-match dep."""
    best, best_len = None, 0
    for candidate in known:
        shared = 0
        for a, b in zip(unknown, candidate):
            if a != b:
                break
            shared += 1
        if shared > best_len:
            best, best_len = candidate, shared
    return f" — did you mean {best!r}?" if best and best_len >= 3 else ""


def _check_keys(where: str, obj: dict, allowed, what: str) -> None:
    for key in obj:
        if key not in allowed:
            _fail(where, f"unknown {what} {key!r}{_suggest(key, allowed)}. "
                         f"Allowed: {', '.join(sorted(allowed))}")


def _check_expect(where: str, expect: Any) -> None:
    if not isinstance(expect, dict):
        _fail(where, "`expect` must be a mapping of assertions")
    if not expect:
        _fail(where, "`expect` is empty — an assertion that asserts nothing "
                     "passes silently and hides the step it was meant to guard")
    _check_keys(where, expect, EXPECT_KEYS, "assertion")
    for label in _as_list(expect.get("is_not")):
        if label not in BAD_CLASSES:
            _fail(where, f"unknown bad-reply class {label!r}. "
                         f"Allowed: {', '.join(sorted(BAD_CLASSES))}")
    for key in ("max_lines", "max_chars", "status"):
        if key in expect and not isinstance(expect[key], int):
            _fail(where, f"`{key}` must be a whole number")
    for key in ("has_image", "used_ai"):
        if key in expect and not isinstance(expect[key], bool):
            _fail(where, f"`{key}` must be true or false")
    if "intent" in expect:
        if not isinstance(expect["intent"], dict):
            _fail(where, "`intent` must be a mapping, e.g. {action: create, entity: customer}")
        _check_keys(where, expect["intent"], {"action", "entity"}, "intent key")
    if "json_path" in expect and not isinstance(expect["json_path"], dict):
        _fail(where, "`json_path` must be a mapping of dotted path -> expected value")
    for pattern in _as_list(expect.get("regex")):
        try:
            re.compile(pattern)
        except re.error as exc:
            _fail(where, f"`regex` {pattern!r} does not compile: {exc}")


def _check_step(where: str, step: Any) -> None:
    if not isinstance(step, dict):
        _fail(where, "a step must be a mapping, e.g. {send: 'สวัสดี', expect: {contains: 'สวัสดี'}}")
    verbs = [v for v in STEP_VERBS if v in step]
    if len(verbs) > 1:
        _fail(where, f"a step does one thing; found {', '.join(verbs)}. Split it.")
    if not verbs and "expect" not in step:
        _fail(where, f"a step needs one of {', '.join(STEP_VERBS)} or a bare `expect`"
                     f"; found {', '.join(sorted(step)) or 'nothing'}")
    _check_keys(where, step, set(STEP_VERBS) | {"expect", "note"}, "step key")

    if "send" in step:
        body = step["send"]
        if isinstance(body, str):
            body = {"message": body}
        if not isinstance(body, dict):
            _fail(where, "`send` must be a string or a mapping with `message`")
        _check_keys(where, body, SEND_KEYS, "send key")
        if not str(body.get("message") or "").strip():
            _fail(where, "`send` needs a non-empty `message`")
        if body.get("oa") and body["oa"] not in OAS:
            _fail(where, f"unknown oa {body['oa']!r}. Allowed: {', '.join(OAS)}")
        if body.get("language") and body["language"] not in LANGUAGES:
            _fail(where, f"unknown language {body['language']!r}. Allowed: {', '.join(LANGUAGES)}")
        if "permissions" in body and not isinstance(body["permissions"], (list, str)):
            _fail(where, "`permissions` must be a list of permission keys or the "
                         "name of a set (all, none, sales, technician, customer)")
        if "ai" in body and not isinstance(body["ai"], dict):
            _fail(where, "`ai` must be the mapping the model would have answered, "
                         "e.g. {action: create, entity: customer, fields: {}, missing: []}")

    if "seed" in step:
        if not isinstance(step["seed"], dict):
            _fail(where, "`seed` must be a mapping of collection -> rows")
        _check_keys(where, step["seed"], SEED_KEYS, "seed collection")
        for name, rows in step["seed"].items():
            if name == "raw":
                if not isinstance(rows, dict):
                    _fail(where, "`seed.raw` must be a mapping of fake-client attribute -> rows")
                continue
            if not isinstance(rows, list):
                _fail(where, f"`seed.{name}` must be a list of rows")
            for row in rows:
                if not isinstance(row, dict):
                    _fail(where, f"`seed.{name}` rows must be mappings")

    if "http" in step:
        body = step["http"]
        if not isinstance(body, dict):
            _fail(where, "`http` must be a mapping with `method` and `path`")
        _check_keys(where, body, HTTP_KEYS, "http key")
        if not body.get("path"):
            _fail(where, "`http` needs a `path`, e.g. /api/v1/licenses/{license_id}/customers")
        method = str(body.get("method", "GET")).upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            _fail(where, f"unsupported HTTP method {method!r}")

    if "expect" in step:
        _check_expect(where, step["expect"])
        if "send" in step or (not any(v in step for v in STEP_VERBS)):
            for key in ("status", "json_path", "json_contains"):
                if key in step["expect"]:
                    _fail(where, f"`{key}` asserts on an HTTP response; "
                                 f"use it on an `http` step")


def parse(raw: Any, path: Path) -> Scenario:
    """Turn a loaded mapping into a Scenario, or explain why it is not one."""
    where = str(path)
    if not isinstance(raw, dict):
        _fail(where, "the top level of a scenario file must be a mapping with "
                     "`name` and `steps`")
    _check_keys(where, raw, {"name", "description", "backend", "actor", "steps"},
                "top-level key")
    name = raw.get("name") or path.stem
    if not NAME_RE.match(str(name)):
        _fail(where, f"`name` {name!r} must be lowercase letters, digits and dashes "
                     f"— it is what --only matches on")
    backend = raw.get("backend", "any")
    if backend not in BACKENDS:
        _fail(where, f"unknown backend {backend!r}. Allowed: {', '.join(BACKENDS)}")
    actor = raw.get("actor") or {}
    if not isinstance(actor, dict):
        _fail(where, "`actor` must be a mapping of defaults for send steps")
    _check_keys(where, actor, ACTOR_KEYS, "actor key")
    if "permissions" in actor and not isinstance(actor["permissions"], (list, str)):
        _fail(where, "`actor.permissions` must be a list of permission keys or the "
                     "name of a set (all, none, sales, technician, customer)")
    if actor.get("language") and actor["language"] not in LANGUAGES:
        _fail(where, f"unknown actor language {actor['language']!r}. "
                     f"Allowed: {', '.join(LANGUAGES)}")
    if actor.get("oa") and actor["oa"] not in OAS:
        _fail(where, f"unknown actor oa {actor['oa']!r}. Allowed: {', '.join(OAS)}")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        _fail(where, "`steps` must be a non-empty list")
    for index, step in enumerate(steps, start=1):
        _check_step(f"{where} step {index}", step)
    return Scenario(
        name=str(name), path=path, description=str(raw.get("description") or ""),
        backend=backend, actor=actor, steps=steps,
    )


def load(path: Path) -> Scenario:
    """Read one scenario file. YAML and JSON are both accepted; YAML needs
    PyYAML, which is a test-only dependency (requirements-test.txt) and is
    never imported by any shipped runtime."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:  # pragma: no cover - depends on the environment
            raise ScenarioError(
                f"{path}: reading YAML needs PyYAML. Install the test "
                f"requirements (pip install -r requirements-test.txt) or write "
                f"the scenario as .json instead."
            ) from None
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ScenarioError(f"{path}: not valid YAML — {exc}") from None
    elif path.suffix.lower() == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScenarioError(f"{path}: not valid JSON — {exc}") from None
    else:
        raise ScenarioError(
            f"{path}: a scenario file must end in .yaml, .yml or .json"
        )
    return parse(raw, path)


def discover(directory: Path) -> list[Scenario]:
    """Every scenario in a directory, sorted by name so runs are repeatable."""
    found: list[Scenario] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in (".yaml", ".yml", ".json"):
            found.append(load(path))
    names: dict[str, Path] = {}
    for scenario in found:
        if scenario.name in names:
            raise ScenarioError(
                f"{scenario.path}: the name {scenario.name!r} is already used by "
                f"{names[scenario.name]} — --only would be ambiguous"
            )
        names[scenario.name] = scenario.path
    return sorted(found, key=lambda s: s.name)
