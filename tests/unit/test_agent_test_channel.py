"""The agent test channel tests itself.

A harness whose scenarios have rotted is worse than no harness: its output is
read as evidence, and a scenario that quietly stopped exercising anything
still prints a green line. So the shipped scenarios run here, in the suite the
project already runs before every deploy, and a step that fails fails the
build.

The rest of this file proves the properties the README promises to whoever
writes a scenario next: a passing file passes, a failing file fails with a
usable reason and a non-zero exit, `--json` is machine-readable, `--list` and
`--only` work, and a file the runner does not fully understand is refused
rather than half-executed.

Only the `fake` backend runs here. The `db` backend needs PostgreSQL, and the
suite this file belongs to must run without one; the db path is exercised by
scripts/agent-test/scenarios/per-oa-registration.yaml, run by hand with
TEST_DATABASE_URL set (see scripts/agent-test/README.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHANNEL = ROOT / "scripts" / "agent-test"
RUN = CHANNEL / "run.py"
SCENARIOS = CHANNEL / "scenarios"

sys.path.insert(0, str(CHANNEL))

from agent_test_runner import scenario as scenario_mod  # noqa: E402


def _run(*args, cwd=None):
    """The runner as the other AI invokes it: a subprocess, reading only its
    exit code and its stdout. Calling main() in-process would miss the two
    things most likely to break for them — the sys.path bootstrap and the
    exit code."""
    return subprocess.run(
        [sys.executable, str(RUN), *args],
        cwd=str(cwd or ROOT), capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def full_run():
    """One run of every shipped scenario, shared by the assertions below.

    Each subprocess pays for importing the whole Application tier, so the
    difference between one run and four is most of this file's wall time.
    """
    result = _run("--json")
    return result, json.loads(result.stdout)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


PASSING = """
name: self-test-passing
description: a greeting is answered as a greeting
steps:
  - send: "สวัสดี"
    expect:
      contains: "สวัสดี"
      used_ai: false
      is_not: [generic_error, not_sure, permission]
"""

FAILING = """
name: self-test-failing
description: asserts something no reply contains
steps:
  - send: "สวัสดี"
    expect:
      contains: "NOTHING WILL EVER SAY THIS"
"""


class TestTheRunnerRunsAScenario:
    def test_a_passing_scenario_passes_and_exits_zero(self, tmp_path):
        path = _write(tmp_path, "pass.yaml", PASSING)
        result = _run(str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout
        assert "0 step failures" in result.stdout

    def test_a_failing_scenario_fails_and_exits_one(self, tmp_path):
        path = _write(tmp_path, "fail.yaml", FAILING)
        result = _run(str(path))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "1 failed" in result.stdout

    def test_the_failure_reason_says_what_was_wanted_and_what_came_back(self, tmp_path):
        """The reader cannot open a debugger, so the reason has to carry the
        actual reply — not just "assertion failed"."""
        path = _write(tmp_path, "fail.yaml", FAILING)
        result = _run(str(path))
        assert "NOTHING WILL EVER SAY THIS" in result.stdout
        assert "closest line:" in result.stdout
        assert "สวัสดี" in result.stdout

    def test_a_json_scenario_file_works_as_well_as_yaml(self, tmp_path):
        """YAML needs PyYAML; JSON is the fallback the README promises, so it
        must not be a second-class path."""
        path = tmp_path / "pass.json"
        path.write_text(json.dumps({
            "name": "self-test-json",
            "steps": [{"send": "สวัสดี", "expect": {"contains": "สวัสดี"}}],
        }, ensure_ascii=False), encoding="utf-8")
        result = _run(str(path))
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheJsonReport:
    def test_json_is_parseable_and_carries_the_reply_and_the_reason(self, tmp_path):
        path = _write(tmp_path, "fail.yaml", FAILING)
        result = _run(str(path), "--json")
        assert result.returncode == 1
        report = json.loads(result.stdout)
        assert report["ok"] is False
        assert report["backend"] == "fake"
        assert report["summary"] == {
            "scenarios": 1, "passed": 0, "failed": 1,
            "steps": 1, "steps_failed": 1,
        }
        step = report["scenarios"][0]["steps"][0]
        assert step["n"] == 1 and step["kind"] == "send"
        assert step["ok"] is False
        assert "สวัสดี" in step["reply"], "the actual reply must be in the report"
        assert step["problems"] and "NOTHING WILL EVER SAY THIS" in step["problems"][0]

    def test_a_passing_run_reports_ok_and_no_problems(self, tmp_path):
        path = _write(tmp_path, "pass.yaml", PASSING)
        result = _run(str(path), "--json")
        report = json.loads(result.stdout)
        assert report["ok"] is True
        assert all(not s["problems"] for s in report["scenarios"][0]["steps"])

    def test_stdout_is_only_json_so_a_program_can_pipe_it(self, tmp_path):
        """The tiers log to stderr; a stray log line on stdout would break
        every caller that parses the report."""
        path = _write(tmp_path, "pass.yaml", PASSING)
        result = _run(str(path), "--json")
        json.loads(result.stdout)


class TestListingAndSelecting:
    def test_list_shows_every_shipped_scenario(self):
        result = _run("--list")
        assert result.returncode == 0
        for path in sorted(SCENARIOS.glob("*.yaml")):
            assert scenario_mod.load(path).name in result.stdout

    def test_only_runs_just_that_one(self):
        result = _run("--only", "sales-day", "--json")
        report = json.loads(result.stdout)
        assert [s["name"] for s in report["scenarios"]] == ["sales-day"]

    def test_an_unknown_only_name_is_refused_rather_than_running_nothing(self):
        """Exit 0 with nothing run would read as "everything passed"."""
        result = _run("--only", "no-such-scenario")
        assert result.returncode == 2
        assert "no scenario named" in result.stderr

    def test_a_db_only_scenario_is_skipped_not_failed_on_the_fake_backend(self, full_run):
        _, report = full_run
        assert {s["name"] for s in report["skipped"]} == {"per-oa-registration"}
        assert report["ok"] is True


class TestABadScenarioFileIsRefused:
    @pytest.mark.parametrize("body,needle", [
        ("name: x\nsteps:\n  - send: 'hi'\n    expect:\n      contain: 'a'\n",
         "did you mean 'contains'"),
        ("name: x\nsteps:\n  - send: 'hi'\n    expect: {}\n",
         "asserts nothing"),
        ("name: x\nsteps:\n  - send: 'hi'\n    expect:\n      is_not: [nonsense]\n",
         "unknown bad-reply class"),
        ("name: x\nsteps:\n  - send: 'hi'\n    expect:\n      regex: '['\n",
         "does not compile"),
        ("name: x\nsteps: []\n",
         "non-empty list"),
        ("name: Not A Name\nsteps:\n  - send: 'hi'\n    expect:\n      contains: 'a'\n",
         "lowercase letters"),
        ("name: x\nsteps:\n  - seed: {custmers: []}\n",
         "unknown seed collection"),
    ])
    def test_it_says_which_file_which_step_and_what_to_fix(self, tmp_path, body, needle):
        path = _write(tmp_path, "bad.yaml", body)
        result = _run(str(path))
        assert result.returncode == 2, result.stdout
        assert needle in result.stderr, result.stderr
        assert "bad.yaml" in result.stderr

    def test_a_step_that_does_two_things_is_refused(self, tmp_path):
        path = _write(tmp_path, "bad.yaml",
                      "name: x\nsteps:\n  - send: 'hi'\n    seed: {customers: []}\n")
        result = _run(str(path))
        assert result.returncode == 2
        assert "a step does one thing" in result.stderr


class TestEveryShippedScenarioStillPasses:
    """The point of the whole file.

    Parametrised per scenario rather than run as one batch so a failure names
    the scenario in the pytest output, which is what a reader sees first.
    """

    @pytest.mark.parametrize(
        "name",
        [s.name for s in scenario_mod.discover(SCENARIOS) if s.runs_on("fake")],
    )
    def test_it_passes_on_the_fake_backend(self, name, full_run):
        result, report = full_run
        scenario = next(s for s in report["scenarios"] if s["name"] == name)
        failures = [
            f"step {step['n']} ({step['kind']} {step['input']}): "
            + " | ".join(step["problems"])
            for step in scenario["steps"] if not step["ok"]
        ]
        assert not failures, f"{name}\n" + "\n".join(failures)
        assert scenario["ok"], f"{name}: scenario did not pass"

    def test_at_least_one_scenario_covers_each_oa(self):
        """A channel that only ever exercises the Sales OA would pass this
        suite while proving a third of the product."""
        covered = set()
        for scenario in scenario_mod.discover(SCENARIOS):
            covered.add(scenario.actor.get("oa", "sales"))
            for step in scenario.steps:
                body = step.get("send")
                if isinstance(body, dict) and body.get("oa"):
                    covered.add(body["oa"])
        assert covered == {"sales", "technician", "customer"}


class TestTheSchemaAndTheRunnerAgree:
    """scenario.schema.json is documentation with teeth only while it lists
    the same keys the runner accepts. Nothing validates scenarios against it
    at runtime (that would need a new dependency), so the drift is caught
    here instead."""

    @pytest.fixture(scope="class")
    def schema(self):
        return json.loads((CHANNEL / "scenario.schema.json").read_text(encoding="utf-8"))

    def test_the_assertion_vocabulary_matches(self, schema):
        assert set(schema["$defs"]["expect"]["properties"]) == scenario_mod.EXPECT_KEYS

    def test_the_seed_collections_match(self, schema):
        assert set(schema["$defs"]["seed"]["properties"]) == scenario_mod.SEED_KEYS

    def test_the_bad_reply_classes_match(self, schema):
        assert set(schema["$defs"]["badClass"]["enum"]) == set(scenario_mod.BAD_CLASSES)

    def test_the_send_keys_match(self, schema):
        mapping = schema["$defs"]["send"]["oneOf"][1]
        assert set(mapping["properties"]) == scenario_mod.SEND_KEYS

    def test_the_step_verbs_match(self, schema):
        properties = set(schema["$defs"]["step"]["properties"])
        assert properties == set(scenario_mod.STEP_VERBS) | {"expect", "note"}

    def test_the_bad_reply_classes_match_the_simulator(self):
        """`is_not: [not_sure]` here and a NOT_SURE finding in
        simulate-phrasings.py must mean the same thing; two lists that drift
        would make one of the two silently weaker."""
        source = (ROOT / "scripts" / "dev" / "simulate-phrasings.py").read_text(
            encoding="utf-8")
        for label, needles in scenario_mod.BAD_CLASSES.items():
            assert f'"{label.upper()}"' in source, label
            for needle in needles:
                assert needle in source, (label, needle)


class TestNoScenarioCanReachTheOutsideWorld:
    def test_the_model_key_is_replaced_not_defaulted(self, monkeypatch):
        """A real OPENROUTER_API_KEY in the environment must not become
        spendable just because someone ran the harness on a dev box."""
        from agent_test_runner import bootstrap

        # prepare() is what puts the tiers on sys.path, so it has to run
        # before chann_app can be imported at all.
        from chann_app.config import settings

        # Restore the original settings when the test ends, including changes
        # made by prepare(); later tests must not inherit a fake API key.
        monkeypatch.setattr(settings, "openrouter_api_key", settings.openrouter_api_key)
        monkeypatch.setattr(settings, "openrouter_model", settings.openrouter_model)
        bootstrap.prepare()
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-a-real-key")
        bootstrap.prepare()
        assert settings.openrouter_api_key == "agent-test-channel"

    def test_the_readme_documents_the_channels_the_ai_may_use(self):
        """The README is the whole interface for someone with no context; a
        section quietly deleted would strand them."""
        readme = (CHANNEL / "README.md").read_text(encoding="utf-8")
        for required in (
            "scripts/agent-test/run.py",
            "pytest tests/unit tests/boundary",
            "simulate-phrasings.py",
            "check-parity.py",
            "TEST_DATABASE_URL",
            "signature",
            "Exit codes",
            "What this channel cannot test",
        ):
            assert required in readme, required


class TestMessageActions:
    def test_list_card_buttons_count_as_actions(self):
        from agent_test_runner.assertions import Outcome, check
        outcome = Outcome(list_card={'rows':[{'action_text':'ข้อมูลลูกค้า C-2026-0001'}]})
        assert check({'actions_include':['C-2026-0001']}, outcome) == []

    def test_text_without_a_button_does_not_count(self):
        from agent_test_runner.assertions import Outcome, check
        outcome = Outcome(text='C-2026-0001', quick_replies=[('C-2026-0001','help')])
        assert check({'actions_include':['C-2026-0001']}, outcome)

    def test_quick_reply_payload_counts_as_an_action(self):
        from agent_test_runner.assertions import Outcome, check
        assert not check({'actions_include':'C-2026-0001'}, Outcome(quick_replies=[('ดู','ข้อมูลลูกค้า C-2026-0001')]))


class TestTheDocumentedCommandBootstrapsItself:
    """A command the docs tell people to run must work on its own.

    Review v3, T05: running this file alone failed
    `test_the_model_key_is_replaced_not_defaulted` with
    ModuleNotFoundError, and passed in a full run only because another
    test file collected earlier had put the tiers on `sys.path`. A gate
    that is green for a reason unrelated to the code under test is not a
    gate. `tests/unit/conftest.py` now does the inserts; this proves it
    from a fresh interpreter with no PYTHONPATH, which is the only way to
    see the difference.
    """

    def test_a_single_file_run_needs_no_pythonpath(self):
        import os

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "tests/unit/test_agent_test_channel.py::"
                "TestNoScenarioCanReachTheOutsideWorld::"
                "test_the_model_key_is_replaced_not_defaulted",
            ],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
