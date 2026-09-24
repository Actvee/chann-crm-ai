"""Round 21D — the dashboard draws the plan (spec §5.4, §8). Mostly read as
text, like every UI test in this repo; the pictures are looked at in Task 15.

`TestTheNavDecision` and `TestTheUpgradeContact` are behavioural (review
C13): the nav model and the contact component are transpiled with the
repo's own TypeScript and RUN under node, so ruling R-C ("a plan-locked
entry is shown, locked, to whoever HOLDS the permission that would open
it") is pinned by what the code decides, not by a string being present.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from plan_fixtures import plan_view

ROOT = Path(__file__).resolve().parents[2]
PRESENTATION = ROOT / "presentation"
LIFF = PRESENTATION / "app/liff"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


PLAN = _read("presentation/app/liff/_plan.tsx")
NAV_MODEL = _read("presentation/app/liff/_nav-model.tsx")
NAV = _read("presentation/app/liff/_nav.tsx")
SHELL = _read("presentation/app/liff/sales/_shell.tsx")
SESSION = _read("presentation/app/liff/sales/_session.ts")
FORMAT = _read("presentation/app/liff/sales/_format.ts")
TH = _read("presentation/lib/i18n/th.ts")
EN = _read("presentation/lib/i18n/en.ts")


def _nav_line(key: str) -> str:
    return next(l for l in NAV_MODEL.splitlines() if f'key: "{key}"' in l and "/liff/sales/" in l)


class TestTheNav:
    def test_each_locked_page_declares_its_feature(self):
        for key, feature in (("chats", "feature.live_chat"), ("tickets", "feature.service"),
                             ("teams", "feature.service"), ("reports", "feature.service"),
                             ("satisfaction", "feature.service"), ("approvals", "feature.service"),
                             ("warranties", "feature.warranty"), ("templates", "feature.custom_documents"),
                             ("apiKeys", "feature.external_api")):
            # P24: on the same line as key + href, for check-parity's regex.
            assert re.search(rf'key: "{key}", href: "/liff/sales/[^"]*"[^\n]*?feature: "{re.escape(feature)}"',
                             _nav_line(key)), key

    def test_ai_reports_and_roles_stay_open(self):
        for key in ("aiReports", "roles"):
            assert "feature:" not in _nav_line(key), key

    def test_templates_and_api_keys_stay_readable(self):
        for key in ("templates", "apiKeys"):
            assert 'lockMode: "notice"' in _nav_line(key), key

    def test_teams_locks_only_its_technician_part(self):
        # Ruling 25: sales groups are on every plan.
        line = _nav_line("teams")
        assert 'lockMode: "part"' in line and '"team.manage"' in line
        page = _read("presentation/app/liff/sales/teams/SalesTeams.tsx")
        assert 'planHas(session.plan, "feature.service")' in page
        assert "<PlanLocked" in page and "loadGroups()" in page

    def test_a_locked_entry_is_drawn_from_held_keys_with_its_reason_as_text(self):
        assert "export function lockedBy(" in NAV_MODEL
        assert "export function navState(" in NAV_MODEL
        # R-C replaces the brief's `|| canUpgrade`: the nav tests permission
        # against held_keys, not setting.manage.
        assert "heldKeys" in NAV and "navState(" in NAV
        assert "canUpgrade" not in NAV
        assert "rail-lock-reason" in NAV            # the reason is text, not a title=


class TestTheLockedPage:
    def test_the_shell_renders_it_for_a_locked_route(self):
        assert "<PlanLocked" in SHELL and "lockedBy(" in SHELL

    def test_the_shell_waits_for_me_before_drawing_a_plan_page(self):
        # The PAGE waits for /me (nothing flashes open and then turns into
        # the locked panel); the RAIL does not (fix round 1, review I3).
        assert "!session.ready" in SHELL and "meAnswered={session.meAnswered}" in SHELL

    def test_the_rail_keeps_its_shape_while_me_is_pending(self):
        # Review I3: no "hidden until /me" rule — pending entries are drawn
        # as before and a lock is added in place (content-jumping).
        assert "pending" not in NAV_MODEL.split("export function navState(")[1].split("\n}")[0]
        assert "planPending" not in NAV and "answered: meAnswered" in NAV

    def test_the_rail_has_one_decision(self):
        # Review I2: the lock is drawn from navState's value, not a second rule.
        assert "lockedBy(" not in NAV and 'state === "locked"' in NAV

    def test_the_owner_gets_the_contact_and_everyone_else_the_ask_owner_line(self):
        assert "canUpgrade ?" in PLAN and "askOwner" in PLAN
        assert "openExternal(" in PLAN              # LIFF opens links through liff.openWindow

    def test_no_contact_no_button(self):
        assert "contact?.url" in PLAN and "contactNone" in PLAN


class TestTheSession:
    def test_me_carries_the_plan(self):
        assert "plan: PlanInfo | null" in SESSION
        assert "sales_contact" in SESSION and "held_keys" in SESSION
        assert "heldKeys: Set<string>" in SESSION


class TestThePages:
    def test_the_plan_card_is_on_the_company_page(self):
        page = _read("presentation/app/liff/sales/company/CompanyProfile.tsx")
        assert "<PlanCard" in page
        assert "/plan`" in PLAN

    def test_ai_reports_on_starter(self):
        page = _read("presentation/app/liff/sales/reports/ai/AiReports.tsx")
        assert 'planHas(session.plan, "quota.ai_reports_per_month")' in page
        assert 'planHas(session.plan, "feature.service")' in page
        assert "aiLocked" in page
        # Owner decision Q4: the two service cards are HIDDEN (not drawn
        # locked) and never requested; the fetch loop itself stays as 21C
        # pinned it.
        assert '["open_jobs_by_tech", "satisfaction_avg"]' in page
        assert "shownKeys.map(" in page and "BASIC_KEYS.map((key) => {" not in page
        assert "Promise.allSettled(BASIC_KEYS.map((key) => load(key)))" in page

    def test_every_disabled_control_has_its_reason(self):
        for rel, reason in (("roles/RoleManagement.tsx", "rolesLocked"),
                            ("approvals/settings/ApprovalSettings.tsx", "approvalLocked"),
                            ("members/MemberManagement.tsx", "inviteAtLimit"),
                            ("invoices/InvoiceList.tsx", "sendLineLocked"),
                            ("quotes/[id]/QuoteDetail.tsx", "sendLineLocked"),
                            ("api-keys/ApiKeys.tsx", "apiKeyLocked"),
                            ("company/CompanyProfile.tsx", "chatPolicyLocked")):
            page = _read(f"presentation/app/liff/sales/{rel}")
            assert reason in page, rel
            assert f'title={{t.dashboard.plan.{reason}' not in page, rel   # never only a tooltip

    def test_each_page_asks_the_plan_by_name(self):
        # P25: check-perms reads `planHas(session.plan, "feature.x")`.
        for rel, key in (("roles/RoleManagement.tsx", "feature.custom_roles"),
                         ("approvals/settings/ApprovalSettings.tsx", "feature.multi_level_approval"),
                         ("invoices/InvoiceList.tsx", "feature.customer_line_link"),
                         ("quotes/[id]/QuoteDetail.tsx", "feature.customer_line_link"),
                         ("templates/DocumentTemplates.tsx", "feature.custom_documents"),
                         ("api-keys/ApiKeys.tsx", "feature.external_api"),
                         ("company/CompanyProfile.tsx", "feature.live_chat")):
            assert f'planHas(session.plan, "{key}")' in _read(f"presentation/app/liff/sales/{rel}"), rel

    def test_the_chat_minutes_lock_without_blocking_the_lead_clean_up(self):
        """Ruling 29 (final fix round 1): on a plan without live chat the
        two chat minutes are disabled with their reason, and the one Save
        still writes lead_auto_archive_days — the two refused keys are left
        out of the loop instead of stopping it at the first 403."""
        page = _read("presentation/app/liff/sales/company/CompanyProfile.tsx")
        assert page.count("disabled={!liveChatOn}") == 2
        assert ': [["lead_auto_archive_days", cleanup]]' in page
        assert "for (const [key, value] of writes)" in page

    def test_a_plan_locked_send_has_its_own_words(self):
        """Final fix round 1 (review Minor 2): push_failed/plan_locked is the
        plan, said with what to do — not the generic "LINE ไม่รับข้อความ"."""
        for rel in ("invoices/InvoiceList.tsx", "quotes/[id]/QuoteDetail.tsx"):
            page = _read(f"presentation/app/liff/sales/{rel}")
            assert 'failure.reason === "plan_locked"' in page and "sendPushPlanLocked" in page, rel
        for words in (TH, EN):
            line = next(l for l in words.splitlines() if "sendPushPlanLocked:" in l)
            assert "Pro" in line and "Chann1" in line, line

    def test_a_plan_refusal_is_translated(self):
        assert '"plan_required"' in FORMAT and '"member_limit_reached"' in FORMAT


class TestTheWords:
    KEYS = ("lockedTitle", "lockedShop", "contactButton", "contactNone", "askOwner", "fromPlan",
            "cardTitle", "users", "usersUnlimited", "aiCredits", "inviteAtLimit", "aiLocked",
            "approvalLocked", "approvalFirstOnly", "rolesLocked", "sendLineLocked", "planRequired",
            "memberLimit", "apiKeyLocked", "chatPolicyLocked")

    def test_both_languages(self):
        for key in self.KEYS:
            assert re.search(rf"\b{key}:", TH), key
            assert re.search(rf"\b{key}:", EN), key

    def test_the_spec_copy(self):
        # "Chann1", not the spec's bare "Chann": the brand rule
        # (check-brand.py, owner 24 ก.ย. 2569) is later than the spec.
        for text in ("ฟีเจอร์นี้อยู่ในแพ็กเกจ {min_plan} ขึ้นไป",
                     "ร้านของคุณใช้แพ็กเกจ {plan} · ข้อมูลเดิมยังอยู่ครบ ไม่มีอะไรถูกลบ",
                     "ติดต่อทีม Chann1 เพื่ออัปเกรด", "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย",
                     "ผู้ใช้ครบ {limit} คนตามแพ็กเกจ {plan} แล้ว",
                     "ถามรายงานด้วย AI มีในแพ็กเกจ Pro ขึ้นไป · รายงานพื้นฐานด้านบนใช้ได้ฟรีเสมอ",
                     "อนุมัติหลายระดับมีในแพ็กเกจ Enterprise ขึ้นไป",
                     "สร้างบทบาทเองมีในแพ็กเกจ Pro ขึ้นไป · ใช้บทบาทมาตรฐานได้ตามปกติ",
                     "ส่งทางไลน์มีในแพ็กเกจ Pro ขึ้นไป"):
            assert text in TH, text


# ----------------------------------------------------------------- behaviour

# Transpiles the named .ts/.tsx files with the repo's TypeScript and runs a
# scenario. Relative imports resolve to the real files; the two browser-only
# modules are stubbed (the i18n hook returns the real Thai dictionary).
_HARNESS = r"""
const ts = require("typescript");
const Module = require("module");
const fs = require("fs");
const path = require("path");
const LIFF = path.resolve("app/liff");
const cache = {};
const stubs = {
  "@/lib/i18n/LanguageProvider": { useLanguage: () => ({ t: load(path.resolve("lib/i18n/th.ts")).th, locale: "th" }) },
  "./_shared": { openExternal: () => undefined, proxyHeaders: () => ({}) },
  "next/navigation": { usePathname: () => "/liff/sales" },
  "@/lib/nav-state": { applyNavCollapsed() {}, readNavCollapsed: () => false, writeNavCollapsed() {} },
  "next/link": { __esModule: true, default: (props) => require("react").createElement("a", {
    href: props.href, "data-locked": props["data-locked"], "aria-label": props["aria-label"],
  }, props.children) },
};
function load(file) {
  if (cache[file]) return cache[file].exports;
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.resolve("."));
  cache[file] = m;
  m.require = (id) => {
    if (id in stubs) return stubs[id];
    if (id.startsWith("./") || id.startsWith("../")) {
      const base = path.resolve(path.dirname(file), id);
      for (const ext of [".tsx", ".ts"]) if (fs.existsSync(base + ext)) return load(base + ext);
    }
    return Module.prototype.require.call(m, id);
  };
  m._compile(out, file);
  return m.exports;
}
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const nav = load(path.join(LIFF, "_nav-model.tsx"));
const plan = load(path.join(LIFF, "_plan.tsx"));
const th = load(path.resolve("lib/i18n/th.ts")).th;
const out = {};
if (input.nav) {
  const entries = nav.navGroups(th, "sales").flatMap((g) => g.entries);
  for (const [name, c] of Object.entries(input.nav)) {
    const access = {
      permissions: new Set(c.effective), isOwner: c.owner, held: c.held ? new Set(c.held) : undefined,
      plan: c.plan, answered: c.answered,
    };
    out[name] = Object.fromEntries(entries.map((e) => [e.key, nav.navState(e, access)]));
    out[name].__lockedBy = Object.fromEntries(entries.map((e) => [e.key, nav.lockedBy(e, c.plan)]));
    out[name].__home = nav.homeEntries(th, access).map((e) => e.key);
    // What the rail actually DRAWS for the same person (review I2).
    const React = require("react");
    const { renderToStaticMarkup } = require("react-dom/server");
    const { NavFrame } = load(path.join(LIFF, "_nav.tsx"));
    const html = renderToStaticMarkup(React.createElement(NavFrame, {
      audience: "sales", permissions: access.permissions, isOwner: c.owner, heldKeys: access.held,
      plan: c.plan, meAnswered: c.answered, children: null,
    }));
    const drawn = {};
    for (const m of html.matchAll(/<a href="([^"]+)"([^>]*)>/g)) drawn[m[1]] = /data-locked="true"/.test(m[2]) ? "locked" : "open";
    out[name].__drawn = Object.fromEntries(entries.map((e) => [e.key, drawn[e.href] ?? "hidden"]));
  }
}
if (input.render) {
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  for (const [name, r] of Object.entries(input.render)) {
    out[name] = renderToStaticMarkup(React.createElement(plan[r.component], r.props));
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _run(payload: dict) -> dict:
    node = shutil.which("node")
    if node is None or not (PRESENTATION / "node_modules/typescript").exists():
        pytest.skip("node and presentation/node_modules are needed to run the nav model")
    done = subprocess.run([node, "-e", _HARNESS], cwd=PRESENTATION, input=json.dumps(payload),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


STARTER = plan_view("starter").as_payload()
PRO = plan_view("pro").as_payload()

# A Starter admin who is not the owner: the role holds service and chat keys,
# and /me removes the plan-locked ones from the effective set (Task 5, R-C).
ADMIN_HELD = ["setting.manage", "member.manage", "team.manage", "role.manage", "view_reports",
              "customer.read", "deal.read", "ticket.read", "approval.view", "chat_session.view"]
LOCKED_ON_STARTER = {"ticket.read", "approval.view", "chat_session.view", "warranty.read"}
ADMIN_EFFECTIVE = [k for k in ADMIN_HELD if k not in LOCKED_ON_STARTER]
SALES_HELD = ["customer.read", "deal.read", "quote.read", "chat_session.view"]
TECHLEAD_HELD = ["ticket.read"]


class TestTheNavDecision:
    """navState(entry, access) — ruling R-C's table, run for real."""

    @pytest.fixture(scope="class")
    def result(self):
        people = {
            "owner_starter": {"effective": [], "held": [], "owner": True, "plan": STARTER},
            "admin_starter": {"effective": ADMIN_EFFECTIVE, "held": ADMIN_HELD, "owner": False, "plan": STARTER},
            "admin_pro": {"effective": ADMIN_HELD, "held": ADMIN_HELD, "owner": False, "plan": PRO},
            "sales_starter": {"effective": [k for k in SALES_HELD if k not in LOCKED_ON_STARTER],
                              "held": SALES_HELD, "owner": False, "plan": STARTER},
            "techlead_starter": {"effective": ["customer.read"], "held": TECHLEAD_HELD + ["customer.read"],
                                 "owner": False, "plan": STARTER},
            # Review I1: every key this person holds is plan-locked, so
            # /me answers with an EMPTY effective set.
            "service_only_starter": {"effective": [], "held": ["ticket.read", "service_report.create"],
                                     "owner": False, "plan": STARTER},
            "admin_pending": {"effective": [], "owner": False, "plan": None, "answered": False},
            "admin_no_plan": {"effective": ADMIN_HELD, "owner": False, "plan": None},
            "old_image_no_held": {"effective": ADMIN_EFFECTIVE, "owner": False, "plan": STARTER},
        }
        for person in people.values():
            person.setdefault("answered", True)
        return _run({"nav": people})

    def test_the_rail_draws_exactly_the_decision(self, result):
        # Review I2: for every person and every entry, the rendered rail
        # (link present, data-locked) equals navState — teams "part" too.
        for name, r in result.items():
            decided = {k: v for k, v in r.items() if not k.startswith("__")}
            assert r["__drawn"] == decided, name

    def test_an_empty_answered_set_opens_nothing(self, result):
        r = result["service_only_starter"]
        for key in ("customers", "deals", "quotes", "invoices", "company", "members", "roles",
                    "history", "aiReports", "products", "appointments"):
            assert r[key] == "hidden", key
        assert r["tickets"] == "locked" and r["teams"] == "locked" and r["reports"] == "locked"
        assert r["overview"] == "open" and r["signature"] == "open"
        assert "customers" not in r["__home"]
        assert all(r[key] == "open" for key in r["__home"]), r["__home"]

    def test_a_setting_manage_holder_who_is_not_the_owner_sees_the_locks(self, result):
        # S16/P16: the case the brief's `mayOpen(effective)` got wrong.
        r = result["admin_starter"]
        for key in ("chats", "tickets", "reports", "satisfaction", "approvals", "templates"):
            assert r[key] == "locked", key
        assert r["warranties"] == "hidden"          # never held warranty.read
        assert r["apiKeys"] == "hidden"             # owner only, as before
        assert r["teams"] == "open"                 # team.manage runs sales groups (ruling 25)
        assert r["aiReports"] == "open" and r["roles"] == "open" and r["customers"] == "open"

    def test_anyone_who_holds_the_key_sees_it_locked_not_only_managers(self, result):
        r = result["sales_starter"]
        assert r["chats"] == "locked"               # holds chat_session.view
        assert r["tickets"] == "hidden" and r["templates"] == "hidden"
        assert r["customers"] == "open"

    def test_teams_is_locked_only_for_someone_whose_only_way_in_was_service(self, result):
        assert result["techlead_starter"]["teams"] == "locked"
        assert result["techlead_starter"]["tickets"] == "locked"

    def test_the_owner_sees_every_locked_entry(self, result):
        r = result["owner_starter"]
        for key in ("chats", "tickets", "reports", "satisfaction", "approvals", "warranties",
                    "templates", "apiKeys"):
            assert r[key] == "locked", key
        assert r["teams"] == "open"
        assert r["__lockedBy"]["apiKeys"] == "feature.external_api"
        assert r["__lockedBy"]["aiReports"] is None

    def test_on_pro_only_enterprise_things_lock(self, result):
        r = result["admin_pro"]
        for key in ("chats", "tickets", "teams", "reports", "approvals", "templates"):
            assert r[key] == "open", key
        assert result["owner_starter"]["__lockedBy"]["templates"] == "feature.custom_documents"

    def test_the_rail_keeps_its_rows_while_me_is_pending(self, result):
        # Review I3: drawn as before (fail-open, no lock) from the first
        # paint; the lock is added in place when /me answers.
        r = result["admin_pending"]
        for key in ("chats", "tickets", "teams", "templates", "customers"):
            assert r[key] == "open", key

    def test_a_me_without_a_plan_locks_nothing(self, result):
        r = result["admin_no_plan"]
        assert r["chats"] == "open" and r["tickets"] == "open"

    def test_an_older_me_without_held_keys_hides_rather_than_guesses(self, result):
        r = result["old_image_no_held"]
        assert r["chats"] == "hidden" and r["templates"] == "locked"

    def test_shortcuts_offer_only_what_opens(self, result):
        assert "chats" not in result["owner_starter"]["__home"]
        assert "chats" in result["admin_pro"]["__home"]


class TestTheUpgradeContact:
    @pytest.fixture(scope="class")
    def html(self):
        return _run({"render": {
            "with_url": {"component": "UpgradeContact", "props": {"contact": {"label": "LINE", "url": "https://x.example/oa"}}},
            "empty_url": {"component": "UpgradeContact", "props": {"contact": {"label": "LINE @chann1", "url": ""}}},
            "none": {"component": "UpgradeContact", "props": {"contact": None}},
            "locked_owner": {"component": "PlanLocked", "props": {
                "feature": "feature.service", "plan": STARTER, "canUpgrade": True, "contact": None}},
            "locked_member": {"component": "PlanLocked", "props": {
                "feature": "feature.service", "plan": STARTER, "canUpgrade": False,
                "contact": {"label": "LINE", "url": "https://x.example/oa"}}},
        }})

    def test_a_url_draws_the_button(self, html):
        assert "<button" in html["with_url"] and "ติดต่อทีม Chann1 เพื่ออัปเกรด" in html["with_url"]

    def test_an_empty_url_draws_words_only(self, html):
        # Task 5's minor: sales_contact may arrive with url "".
        assert "<button" not in html["empty_url"]
        assert "ติดต่อทีม Chann1 CRM AI ที่ดูแลร้านของคุณ" in html["empty_url"] and "LINE @chann1" in html["empty_url"]
        assert "<button" not in html["none"]

    def test_the_locked_page(self, html):
        owner, member = html["locked_owner"], html["locked_member"]
        assert "ฟีเจอร์นี้อยู่ในแพ็กเกจ Pro ขึ้นไป" in owner and "Starter" in owner
        assert "ถ้าต้องการใช้ แจ้งเจ้าของร้านได้เลย" in member and "<button" not in member
