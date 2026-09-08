#!/usr/bin/env python3
"""The automated test channel — run scenario files against the real handlers.

    python scripts/agent-test/run.py                     # every scenario, fake backend
    python scripts/agent-test/run.py --list
    python scripts/agent-test/run.py --only sales-day
    python scripts/agent-test/run.py --backend db
    python scripts/agent-test/run.py --json > result.json
    python scripts/agent-test/run.py path/to/one-scenario.yaml

Exit codes: 0 everything passed, 1 a step failed, 2 the run could not start
(a scenario file the runner refuses, a backend that is not available).

Read scripts/agent-test/README.md before writing a scenario — it is written
for someone who has never opened this codebase.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agent_test_runner import assertions, scenario as scenario_mod  # noqa: E402
from agent_test_runner.backends import BackendError, make_backend  # noqa: E402

SCENARIO_DIR = HERE / "scenarios"


class _ExceptionTrap(logging.Handler):
    """A polite reply can hide an explosion.

    On 2 Sep 2026 a scene in simulate-day.py raised inside a catch, logged
    the traceback, answered something that read fine, and the run said zero
    findings. Every exception logged while a step is running is therefore a
    failure of that step, whatever the reply looked like.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.caught: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[1] is not None:
            self.caught.append(
                f"an exception was logged while handling this step: "
                f"{type(record.exc_info[1]).__name__}: "
                f"{str(record.exc_info[1])[:160]}"
            )


TRAP = _ExceptionTrap()


def _quiet_logging(verbose: bool) -> None:
    """Scenario output is the product; the tiers' own logs are noise.

    Left on stderr rather than silenced entirely, so `--json` on stdout stays
    parseable while a person watching the terminal still sees real warnings.
    """
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        stream=sys.stderr)
    for name in ("httpx", "httpcore", "chann_data.cache", "chann_app"):
        logging.getLogger(name).setLevel(
            logging.INFO if verbose else logging.ERROR
        )
    logging.getLogger().addHandler(TRAP)


def _step_kind(step: dict) -> str:
    for verb in scenario_mod.STEP_VERBS:
        if verb in step:
            return verb
    return "expect"


def _describe(step: dict, kind: str) -> str:
    """One line saying what the step did, for the human report and the JSON."""
    if kind == "send":
        body = step["send"]
        return body if isinstance(body, str) else str(body.get("message", ""))
    if kind == "seed":
        return ", ".join(f"{name}({len(rows) if isinstance(rows, list) else len(rows)})"
                         for name, rows in step["seed"].items())
    if kind == "http":
        body = step["http"]
        return f"{str(body.get('method', 'GET')).upper()} {body.get('path')}"
    if kind == "reset":
        return "fresh state"
    return "assert on the last reply"


async def run_scenario(scenario, backend, *, keep_going: bool) -> dict:
    """One scenario, start to finish. Never raises: a broken step becomes a
    reported failure, because the caller may be reading only the JSON."""
    started = time.time()
    backend.reset(scenario.actor)
    refs: dict = {}
    last: assertions.Outcome | None = None
    results: list[dict] = []
    aborted = False

    for index, step in enumerate(scenario.steps, start=1):
        kind = _step_kind(step)
        record = {
            "n": index, "kind": kind, "input": _describe(step, kind),
            "ok": True, "problems": [], "notes": [],
        }
        if step.get("note"):
            record["note"] = step["note"]
        if aborted:
            record.update(ok=False, skipped=True,
                          problems=["not run — an earlier step failed"])
            results.append(record)
            continue
        TRAP.caught.clear()
        try:
            outcome, notes = await _execute(step, kind, scenario, backend, refs, last)
            record["notes"] = notes
            if outcome is not None:
                last = outcome
                if kind == "http":
                    record["status"] = outcome.status
                    record["body"] = outcome.body
                else:
                    record["reply"] = outcome.text
                    record["quick_replies"] = [list(q) for q in outcome.quick_replies]
                    record["images"] = outcome.images
                    record["intent"] = outcome.intent
                    record["used_ai"] = outcome.used_ai
            if "expect" in step:
                target = outcome if outcome is not None else last
                if target is None:
                    record["problems"] = [
                        "nothing to assert on — no `send` or `http` has run yet"
                    ]
                else:
                    record["problems"] = assertions.check(step["expect"], target)
                    if kind not in ("send", "http"):
                        record["reply"] = target.text
        except BackendError as exc:
            record["problems"] = [str(exc)]
        except Exception as exc:  # noqa: BLE001 - reported, never propagated
            record["problems"] = [f"{type(exc).__name__}: {exc}"]
        record["problems"] = list(record["problems"]) + TRAP.caught[:]
        record["ok"] = not record["problems"]
        if not record["ok"] and not keep_going:
            aborted = True
        results.append(record)

    return {
        "name": scenario.name,
        "file": str(scenario.path),
        "description": scenario.description,
        "backend": backend.name,
        "ok": all(r["ok"] for r in results),
        "seconds": round(time.time() - started, 3),
        "steps": results,
    }


async def _execute(step, kind, scenario, backend, refs, last):
    """Run one step, returning (outcome or None, notes)."""
    if kind == "reset":
        backend.reset(scenario.actor)
        refs.clear()
        return None, ["state reset"]
    if kind == "seed":
        return None, await backend.seed(step["seed"], refs)
    if kind == "send":
        body = step["send"]
        if isinstance(body, str):
            body = {"message": body}
        actor = scenario.actor
        oa = body.get("oa") or actor.get("oa") or "sales"
        role = body.get("role") or actor.get("role") or (
            "technician" if oa == "technician" else "sales"
        )
        permissions = body.get("permissions", actor.get("permissions"))
        language = body.get("language") or actor.get("language") or "th"
        return await backend.send(
            message=body["message"], oa=oa, role=role, language=language,
            permissions=permissions, ai=body.get("ai"), refs=refs,
        )
    if kind == "http":
        body = step["http"]
        return await backend.http(
            method=str(body.get("method", "GET")).upper(), path=body["path"],
            body=body.get("json"), params=body.get("params"),
            principal=body.get("as"), refs=refs,
        )
    return None, []


def _print_report(report: dict, *, verbose: bool) -> None:
    for scenario in report["scenarios"]:
        mark = "ok" if scenario["ok"] else "!!"
        failed = sum(1 for s in scenario["steps"] if not s["ok"])
        tail = "" if scenario["ok"] else f", {failed} failed"
        print(f"{mark} {scenario['name']:<34} {len(scenario['steps'])} steps"
              f"{tail}  ({scenario['seconds']}s)")
        for step in scenario["steps"]:
            if step["ok"] and not verbose:
                continue
            head = f"     step {step['n']} {step['kind']}: {step['input'][:60]}"
            print(head)
            for note in step.get("notes", []):
                print(f"       note: {note}")
            if step.get("reply") is not None and (verbose or not step["ok"]):
                first = (step["reply"] or "").splitlines()
                print(f"       reply: {(first[0] if first else '(empty)')[:110]}")
            if step.get("status") is not None:
                print(f"       http {step['status']}")
            for problem in step["problems"]:
                head, *rest = str(problem).splitlines()
                print(f"       FAIL {head}")
                for line in rest:
                    print(f"            {line}")
    summary = report["summary"]
    print(f"\n{summary['scenarios']} scenarios · {summary['passed']} passed · "
          f"{summary['failed']} failed · {summary['steps']} steps · "
          f"{summary['steps_failed']} step failures  (backend={report['backend']})")


def _collect(args) -> list:
    if args.files:
        found = []
        for name in args.files:
            path = Path(name)
            if not path.exists():
                raise scenario_mod.ScenarioError(f"{name}: no such file")
            found.append(scenario_mod.load(path))
        return found
    return scenario_mod.discover(SCENARIO_DIR)


async def _main_async(args, scenarios) -> dict:
    backend = make_backend(args.backend)
    try:
        reports = [await run_scenario(s, backend, keep_going=args.keep_going)
                   for s in scenarios]
    finally:
        await backend.close()
    steps = sum(len(r["steps"]) for r in reports)
    return {
        "backend": args.backend,
        "ok": all(r["ok"] for r in reports),
        "scenarios": reports,
        "summary": {
            "scenarios": len(reports),
            "passed": sum(1 for r in reports if r["ok"]),
            "failed": sum(1 for r in reports if not r["ok"]),
            "steps": steps,
            "steps_failed": sum(1 for r in reports
                                for s in r["steps"] if not s["ok"]),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/agent-test/run.py",
        description="Run data-defined scenarios against the real chat and HTTP handlers.",
    )
    parser.add_argument("files", nargs="*",
                        help="scenario files to run; default is every file in "
                             "scripts/agent-test/scenarios/")
    parser.add_argument("--backend", choices=("fake", "db"), default="fake",
                        help="fake (default, no database) or db (real Data tier "
                             "over HTTP against TEST_DATABASE_URL)")
    parser.add_argument("--only", metavar="NAME", action="append", default=[],
                        help="run only the scenario(s) with this name; repeatable")
    parser.add_argument("--list", action="store_true",
                        help="print the available scenarios and exit")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable report on stdout instead of the "
                             "human summary")
    parser.add_argument("--verbose", action="store_true",
                        help="print every step, not only the failures")
    parser.add_argument("--keep-going", action="store_true",
                        help="run the remaining steps after a failure instead of "
                             "skipping them (they usually depend on the failed one)")
    args = parser.parse_args(argv)
    _quiet_logging(args.verbose)

    try:
        scenarios = _collect(args)
    except scenario_mod.ScenarioError as exc:
        print(f"scenario error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        if args.json:
            print(json.dumps([
                {"name": s.name, "file": str(s.path), "backend": s.backend,
                 "description": s.description, "steps": len(s.steps)}
                for s in scenarios
            ], ensure_ascii=False, indent=2))
        else:
            for s in scenarios:
                print(f"{s.name:<34} backend={s.backend:<5} {len(s.steps):>3} steps  "
                      f"{s.description[:60]}")
        return 0

    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.name for s in scenarios}
        if unknown:
            print(f"scenario error: no scenario named {', '.join(sorted(unknown))}. "
                  f"Try --list.", file=sys.stderr)
            return 2
        scenarios = [s for s in scenarios if s.name in wanted]

    runnable = [s for s in scenarios if s.runs_on(args.backend)]
    skipped = [s for s in scenarios if not s.runs_on(args.backend)]
    if not runnable:
        print(f"scenario error: none of the selected scenarios run on the "
              f"{args.backend} backend "
              f"({', '.join(f'{s.name} needs {s.backend}' for s in skipped)})",
              file=sys.stderr)
        return 2

    try:
        report = asyncio.run(_main_async(args, runnable))
    except BackendError as exc:
        print(f"backend error: {exc}", file=sys.stderr)
        return 2
    report["skipped"] = [{"name": s.name, "needs_backend": s.backend}
                         for s in skipped]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(report, verbose=args.verbose)
        for s in skipped:
            print(f"-- skipped {s.name}: needs backend={s.backend}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
