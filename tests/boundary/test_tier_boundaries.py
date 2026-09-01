"""Executable enforcement of the 4-tier boundary (CLAUDE.md 4, Master Spec 1.7).

These tests read the source, not the running app, because the rule is about
what a tier is *allowed to depend on* — a runtime test would only catch the
violation on the code path that happens to execute.

The rules:
    Presentation -> Application only
    Application  -> Data only, via internal HTTP
    Data         -> PostgreSQL / Redis  (the only tier that may)
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PERSISTENCE_MODULES = {"sqlalchemy", "psycopg", "psycopg2", "redis", "asyncpg", "alembic"}
VENDOR_PDF_MODULES = {"zcatalyst_sdk", "zcatalyst", "catalyst"}


def _python_files(tier: str) -> list[Path]:
    return sorted((ROOT / tier).rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _violations(tier: str, forbidden: set[str]) -> list[str]:
    found = []
    for path in _python_files(tier):
        bad = _imported_roots(path) & forbidden
        if bad:
            found.append(f"{path.relative_to(ROOT)} imports {sorted(bad)}")
    return found


class TestApplicationTier:
    def test_application_does_not_access_persistence_directly(self):
        """The Application Tier must reach state only through the Data Tier."""
        violations = _violations("application", PERSISTENCE_MODULES)
        assert not violations, (
            "Application Tier must not import persistence libraries; "
            "use DataClient over internal HTTP instead:\n  " + "\n  ".join(violations)
        )

    def test_application_does_not_import_data_tier_modules(self):
        for path in _python_files("application"):
            source = path.read_text(encoding="utf-8")
            assert "from data." not in source and "import data." not in source, (
                f"{path.relative_to(ROOT)} imports Data Tier code directly"
            )

    def test_application_has_a_data_client(self):
        assert (ROOT / "application/chann_app/data_client.py").exists()

    def test_domain_code_does_not_import_a_pdf_vendor_sdk(self):
        """ADR-021 replaced the PDF engine once already. Domain code depends on
        the PdfRenderer protocol so the next swap stays a one-class change.

        One narrow, explicit exception: services/pdf/smartbrowz.py IS the
        concrete PdfRenderer adapter for SmartBrowz — by definition, the
        adapter itself has to import the vendor SDK somewhere, or the
        PdfRenderer abstraction could never actually be implemented at
        all. The boundary this test protects is that nothing OTHER than
        the designated adapter file depends on the vendor SDK directly.
        """
        allowed = {"application/chann_app/services/pdf/smartbrowz.py"}
        violations = _violations("application", VENDOR_PDF_MODULES)
        violations = [v for v in violations if not any(a in v for a in allowed)]
        assert not violations, "PDF vendor SDK must sit behind PdfRenderer:\n  " + "\n  ".join(violations)

    def test_authorization_does_not_branch_on_tenant_role_names(self):
        """Custom role labels are tenant data. Application policy may branch
        on permission keys or the protected owner flag, never role strings."""
        role_comparison = re.compile(
            r"\brole\b\s*(?:==|!=)\s*['\"](?:owner|admin|member|cs|sales|technician)['\"]",
            re.IGNORECASE,
        )
        offenders = []
        for path in _python_files("application"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if role_comparison.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        assert not offenders, "authorization hardcodes role names: " + ", ".join(offenders)


class TestDataTier:
    def test_data_tier_owns_persistence(self):
        """Positive assertion — if this ever fails, persistence has drifted out
        of the tier that is supposed to own it."""
        imported: set[str] = set()
        for path in _python_files("data"):
            imported |= _imported_roots(path)
        assert imported & PERSISTENCE_MODULES, "Data Tier should be the tier that owns DB/cache access"

    def test_data_tier_does_not_call_the_application_tier(self):
        """State flows downward. A Data -> Application call would invert the
        dependency and create a cycle across the boundary."""
        for path in _python_files("data"):
            source = path.read_text(encoding="utf-8")
            assert "from application" not in source and "import application" not in source, (
                f"{path.relative_to(ROOT)} calls upward into the Application Tier"
            )


class TestPresentationTier:
    def test_presentation_has_no_python_persistence_code(self):
        violations = _violations("presentation", PERSISTENCE_MODULES)
        assert not violations, "\n  ".join(violations)

    def test_presentation_only_talks_to_the_application_tier(self):
        """Next.js source must not carry the Data Tier URL. Presentation calls
        Application; only Application knows where Data lives."""
        offenders = []
        for ext in ("*.ts", "*.tsx", "*.js", "*.jsx"):
            for path in (ROOT / "presentation").rglob(ext):
                if "node_modules" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "DATA_BASE_URL" in text or "/internal/v1" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, "Presentation must not reference the Data Tier:\n  " + "\n  ".join(offenders)

    def test_runtime_urls_are_not_baked_into_the_next_build(self):
        """The exact image is promoted across environments. Next's `env`
        option substitutes values at build time, which would make the image
        environment-specific even if its tag stayed the same."""
        config = (ROOT / "presentation/next.config.js").read_text(encoding="utf-8")
        assert "env:" not in config and "env =" not in config

        offenders = []
        for path in (ROOT / "presentation").rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            if text.lstrip().startswith('"use client"') and "APPLICATION_BASE_URL" in text:
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, f"client code bakes Application URL into build: {offenders}"


class TestContainerBuildContract:
    @pytest.mark.parametrize("tier", ["data", "application", "presentation"])
    def test_tier_has_a_dockerignore(self, tier):
        assert (ROOT / tier / ".dockerignore").is_file()

    def test_presentation_build_context_excludes_generated_trees(self):
        rules = (ROOT / "presentation/.dockerignore").read_text(encoding="utf-8").splitlines()
        assert "node_modules/" in rules
        assert ".next/" in rules

    @pytest.mark.parametrize("tier", ["data", "application"])
    def test_python_container_uses_json_cmd_and_exec(self, tier):
        dockerfile = (ROOT / tier / "Dockerfile").read_text(encoding="utf-8")
        cmd_lines = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
        assert len(cmd_lines) == 1
        assert cmd_lines[0].startswith('CMD ["sh", "-c", "exec uvicorn ')

    def test_phase1_does_not_require_sharp_install_scripts(self):
        config = (ROOT / "presentation/next.config.js").read_text(encoding="utf-8")
        assert "images: { unoptimized: true }" in config


class TestInfrastructureSafetyBoundary:
    def test_reference_only_infrastructure_stays_as_data_sources(self):
        terraform = (ROOT / "infrastructure/terraform/main.tf").read_text(encoding="utf-8")
        protected_types = (
            "google_compute_network",
            "google_vpc_access_connector",
            "google_sql_database_instance",
            "google_redis_instance",
            "google_artifact_registry_repository",
        )
        for resource_type in protected_types:
            assert f'data "{resource_type}"' in terraform
            assert f'resource "{resource_type}"' not in terraform

    def test_terraform_declares_no_forbidden_identity_or_secret_resources(self):
        terraform = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "infrastructure/terraform").glob("*.tf"))
        )
        forbidden = ('resource "google_project_iam', 'resource "google_service_account',
                     'resource "google_secret_manager')
        assert not [marker for marker in forbidden if marker in terraform]

    def test_terraform_declares_only_the_new_phase1_resource_types(self):
        terraform = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "infrastructure/terraform").glob("*.tf"))
        )
        expected = {
            "google_sql_database",
            "google_sql_user",
            "google_cloud_run_v2_service",
            "google_storage_bucket",
        }
        declared = set()
        for line in terraform.splitlines():
            line = line.strip()
            if line.startswith('resource "'):
                declared.add(line.split('"')[1])
        assert declared == expected

    def test_cloud_run_public_invocation_is_explicit_and_dev_only(self):
        terraform = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "infrastructure/terraform").glob("*.tf"))
        )
        variables = (ROOT / "infrastructure/terraform/variables.tf").read_text(
            encoding="utf-8"
        )
        plan_gate = (ROOT / "scripts/dev-infra-plan.sh").read_text(encoding="utf-8")
        assert terraform.count(
            "invoker_iam_disabled = "
            "var.dev_reduced_security_disable_invoker_iam_check"
        ) == 3
        assert (
            '!var.dev_reduced_security_disable_invoker_iam_check || '
            'var.environment == "dev"'
        ) in terraform
        variable_start = variables.index(
            'variable "dev_reduced_security_disable_invoker_iam_check"'
        )
        next_block = variables.find('\nvariable "', variable_start + 1)
        variable_block = variables[
            variable_start:next_block if next_block != -1 else None
        ]
        assert "default     = false" in variable_block
        assert "required_public_services" in plan_gate
        assert 'after.get("invoker_iam_disabled") is not True' in plan_gate
        assert 'member = "allUsers"' not in terraform

        examples = ROOT / "infrastructure/terraform/envs"
        assert "dev_reduced_security_disable_invoker_iam_check = true" in (
            examples / "dev/terraform.tfvars.example"
        ).read_text(encoding="utf-8")
        for environment in ("stage", "production"):
            assert "dev_reduced_security_disable_invoker_iam_check = false" in (
                examples / environment / "terraform.tfvars.example"
            ).read_text(encoding="utf-8")

    def test_cloud_run_images_are_digest_inputs_and_existing_connector_is_reused(self):
        terraform = (ROOT / "infrastructure/terraform/cloud_run.tf").read_text(
            encoding="utf-8"
        )
        assert terraform.count('resource "google_cloud_run_v2_service"') == 3
        assert terraform.count("data.google_vpc_access_connector.connector.id") == 3
        for tier in ("data", "application", "presentation"):
            assert f'image = var.image_digests["{tier}"]' in terraform

    def test_data_cloud_run_uses_cloud_sql_unix_socket_without_mutating_instance(self):
        terraform = (ROOT / "infrastructure/terraform/cloud_run.tf").read_text(
            encoding="utf-8"
        )
        assert terraform.count('cloud_sql_instance {') == 1
        assert 'mount_path = "/cloudsql"' in terraform
        assert "data.google_sql_database_instance.primary.connection_name" in terraform
        assert 'execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"' in terraform
        assert "private_ip_address" not in terraform

    def test_runtime_secrets_are_sensitive_and_real_tfvars_are_ignored(self):
        variables = (ROOT / "infrastructure/terraform/variables.tf").read_text(
            encoding="utf-8"
        )
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for variable in ("database_password", "admin_secret", "jwt_secret",
                         "line_credentials", "liff_ids", "line_login_channel_id",
                         "openrouter_api_key"):
            block_start = variables.index(f'variable "{variable}"')
            next_block = variables.find('\nvariable "', block_start + 1)
            block = variables[block_start:next_block if next_block != -1 else None]
            assert "sensitive" in block and "true" in block
        assert "infrastructure/terraform/envs/*/terraform.tfvars" in gitignore
        assert "infrastructure/terraform/envs/*/backend.hcl" in gitignore

    def test_dev_plan_gate_cannot_apply_or_import(self):
        script = (ROOT / "scripts/dev-infra-plan.sh").read_text(encoding="utf-8")
        executable_lines = [
            line.strip()
            for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert any('infra-preflight.sh' in line for line in executable_lines)
        assert not any("terraform apply" in line or "terraform import" in line
                       for line in executable_lines)
        assert "allowed_managed_types" in script
        assert "required_addresses" in script

    def test_dev_plan_evidence_does_not_retain_raw_sensitive_json(self):
        script = (ROOT / "scripts/dev-infra-plan.sh").read_text(encoding="utf-8")
        assert "umask 077" in script
        assert "cleanup_sensitive_plan_json" in script
        assert 'rm -f "${SENSITIVE_PLAN_JSON}"' in script
        assert "plan-policy-summary.json" in script
        assert "RAW_PLAN_JSON_RETAINED=NO" in script
        assert "DO_NOT_UPLOAD_BINARY_PLAN=YES" in script
        assert "PLAN_POLICY_JSON=" not in script


class TestConfigurationContract:
    @pytest.mark.parametrize(
        "tier,forbidden_literal",
        [
            ("application", "chann1-1"),
            ("data", "chann1-1"),
        ],
    )
    def test_no_hardcoded_infrastructure_identifiers(self, tier, forbidden_literal):
        """Values marked DERIVED_AT_DEPLOY must not be baked into an artifact
        that gets promoted unchanged across three environments (ADR-008)."""
        offenders = [
            str(p.relative_to(ROOT))
            for p in _python_files(tier)
            if forbidden_literal in p.read_text(encoding="utf-8")
        ]
        assert not offenders, f"hardcoded {forbidden_literal!r} in: {offenders}"


class TestAssignmentVocabularyStaysInSync:
    """The Application and Data tiers each own a copy of the assignment rule
    vocabulary, because they do not import from each other.

    Duplication is the deliberate choice — a shared package for forty lines
    of closed lists is worse — but it must not be able to drift silently.
    Adding an operator to one side and not the other would let someone save
    a rule the engine refuses to execute, months before anyone finds out.
    """

    def _both(self):
        """Imported normally, not by file path.

        A path-loaded module gets a synthetic __module__, which breaks
        dataclass construction inside the engine — the failure has nothing
        to do with what this test is checking.
        """
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for tier in ("application", "data"):
            path = str(root / tier)
            if path not in sys.path:
                sys.path.insert(0, path)

        from chann_app.services import assignment_validation as app
        from chann_data import assignment_engine as data

        return app, data

    def test_the_two_copies_agree(self):
        app, data = self._both()

        assert app.OPERATORS == data.OPERATORS, "operator lists have drifted"
        assert app.SELECTION_STRATEGIES == data.SELECTION_STRATEGIES
        assert app.CAPACITY_MODES == data.CAPACITY_MODES
        assert app.RULE_VERSION == data.RULE_VERSION

    def test_both_reject_the_same_bad_rule(self):
        """Agreeing on the vocabulary is not enough — they must also agree
        on what to do with it."""
        app, data = self._both()

        bad = {
            "scope": "nope",
            "match_criteria": [{"field": "x", "operator": "sounds_like", "value": "a"}],
            "selection_strategy": "vibes",
        }
        assert app.validate_rule(bad) == data.validate_rule(bad)


class TestDocumentEndpointsReturnFiles:
    """The Presentation proxy parses every response as JSON.

    Endpoints that return a PDF must therefore be listed in that proxy's
    pass-through predicate, or res.json() throws, the catch turns it into
    a 503, and the person is told the server is unavailable while holding
    a request the Application Tier answered with 200 and a valid file.

    That failure leaves NO trace: no error in the Application Tier because
    it succeeded, and no 503 in its access log because it never returned
    one. It survived several rounds of looking at the wrong tier, so the
    two sides are pinned together here.
    """

    def _document_routes(self) -> set[str]:
        """Application Tier routes whose response is a file, not JSON."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (
            root / "application/chann_app/routers_phase2.py"
        ).read_text(encoding="utf-8")

        found = set()
        # A handler returning fastapi Response with a media_type is
        # returning bytes; find the route path decorating it.
        for match in re.finditer(
            r'@router\.get\("([^"]+)"\)(.{0,3000}?)(?=@router\.|\Z)', source, re.S,
        ):
            path, body = match.group(1), match.group(2)
            if "media_type=" in body and "application/pdf" in body:
                found.add(path.rstrip("/").rsplit("/", 1)[-1])
        return found

    def _proxy_predicate(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (
            root / "presentation/app/api/phase2/[...path]/route.ts"
        ).read_text(encoding="utf-8")

    def test_every_pdf_route_is_passed_through_by_the_proxy(self):
        segments = self._document_routes()
        assert segments, "no PDF-returning routes found — has the detection broken?"

        proxy = self._proxy_predicate()
        for segment in segments:
            token = segment.strip("{}")
            # Either the literal last segment, or the parent collection for
            # routes ending in an id like /documents/{document_id}.
            assert token in proxy or "documents" in proxy, (
                f"route ending in {segment!r} returns a PDF but the proxy will "
                "parse it as JSON and answer 503"
            )

    def test_the_proxy_does_not_json_parse_documents(self):
        proxy = self._proxy_predicate()
        assert "isDocumentPath" in proxy
        assert "callApplicationRaw" in proxy, (
            "documents must be streamed, not parsed"
        )


class TestDocumentsOpenInsideLine:
    """LINE's in-app browser refuses blob: URLs.

    The dashboard runs inside LIFF and nowhere else, so fetching a PDF as
    a blob and linking to the resulting blob: URL produces exactly one
    outcome: "ไม่สามารถเปิดลิงก์ได้ เนื่องจากเกิดข้อผิดพลาดที่ไม่คาดคิด".

    It also made the person press twice — once to fetch, once on the
    button that appeared — to reach that dead end.

    A signed https link avoids all of it: it carries its own
    authorisation, opens like any other URL, and can be forwarded to a
    customer as-is.
    """

    def _quote_pages(self) -> dict[str, str]:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "presentation/app/liff/sales/quotes"
        return {
            str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in root.rglob("*.tsx")
        }

    def test_no_page_builds_a_blob_url_for_a_document(self):
        for name, source in self._quote_pages().items():
            assert "createObjectURL" not in source, (
                f"{name} builds a blob: URL — LINE will refuse to open it"
            )

    def test_documents_are_opened_through_liff(self):
        """liff.openWindow, not an anchor and not window.open: a popup
        opened after an await is not user-initiated and gets blocked."""
        pages = self._quote_pages()
        assert any("openExternal(" in source for source in pages.values()), (
            "no quote page opens a document through the LIFF helper"
        )

    def test_the_link_endpoint_exists_to_open(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (
            root / "application/chann_app/routers_phase2.py"
        ).read_text(encoding="utf-8")
        assert "/documents/{document_id}/link" in source, (
            "the pages ask for a signed link; the endpoint must exist"
        )


class TestEveryClientCallHasARoute:
    """The failure that keeps recurring: a method or path that exists on
    one tier and not the other.

    It has happened three ways — an endpoint built only in the Data tier
    so every dashboard call 404'd, a client sending PATCH to a route that
    only accepts POST, and a URL assembled from an f-string that no route
    matched. None of them fail at import; they fail on the first press of
    a button, in production.
    """

    def _data_routes(self):
        import re
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "data"))
        from chann_data.main import app

        routes = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/internal/v1/"):
                routes.append((
                    path,
                    getattr(route, "methods", set()) or set(),
                    re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"),
                ))
        return routes

    def test_every_data_client_call_matches_a_route_and_method(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (
            root / "application/chann_app/data_client.py"
        ).read_text(encoding="utf-8")
        routes = self._data_routes()

        problems = []
        for match in re.finditer(
            r'self\._client\.(get|post|patch|put|delete)\(\s*\n?\s*f?"([^"]*)"'
            r'(?:\s*\n?\s*f?"([^"]*)")?',
            source,
        ):
            verb = match.group(1).upper()
            url = (match.group(2) or "") + (match.group(3) or "")
            url = url.replace("{self._base}", "")
            if not url.startswith("/internal/v1/"):
                continue  # /health and similar live outside the surface
            probe = re.sub(r"\{[^}]*\}", "placeholder", url)

            matching = [p for p, methods, rx in routes if rx.match(probe)]
            if not matching:
                problems.append(f"{verb} {url} — no such route")
            elif not any(
                verb in methods for p, methods, rx in routes if rx.match(probe)
            ):
                allowed = sorted(
                    m for p, methods, rx in routes if rx.match(probe)
                    for m in methods if m != "HEAD"
                )
                problems.append(f"{verb} {url} — route accepts {allowed}")

        assert not problems, "\n".join(["client/route mismatches:", *problems])


class TestEveryDashboardCallHasARoute:
    """The other direction of the same failure: a page calling a path the
    Application Tier does not serve.

    It has happened once already — Phase 12's ticket endpoints were built
    in the Data tier only, so every dashboard call 404'd — and it fails on
    the first press of a button rather than at import.
    """

    def test_no_page_calls_a_path_that_does_not_exist(self):
        import re
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "application"))
        from chann_app.main import app

        routes = [
            re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$")
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1/")
        ]

        problems = []
        for source_file in (root / "presentation/app").rglob("*.tsx"):
            text = source_file.read_text(encoding="utf-8")
            for match in re.finditer(
                r"/api/phase2/([A-Za-z0-9_\-/${}.\[\]]*)", text,
            ):
                url = re.sub(r"\$\{[^}]*\}", "X", match.group(1))
                url = url.split("?")[0].rstrip("/")
                if not url or "$" in url:
                    # A URL split across source lines; the scanner sees
                    # half of it and cannot judge. Joining such URLs is
                    # the fix, not loosening this check.
                    continue
                probe = "/api/v1/" + url.replace("X", "placeholder")
                if not any(rx.match(probe) for rx in routes):
                    problems.append(f"{url}   ({source_file.name})")

        assert not problems, "\n".join(
            ["dashboard calls with no Application Tier route:", *problems]
        )


class TestEveryTenantRouteIsGuarded:
    """A tenant-scoped route that resolves a principal but never checks a
    permission is readable by any member of any role.

    Every exemption below is a route where a permission check would be
    wrong, not one that was forgotten — and naming them here is what makes
    a forgotten one visible.
    """

    # route path -> why a permission check does not apply
    EXEMPT = {
        # Returning your own permissions cannot require a permission
        # without circularity, and it discloses nothing the caller does
        # not already have.
        "/licenses/{license_id}/me/permissions": "returns the caller's own keys",
        # Guarded by is_owner instead: ownership is not a permission a role
        # can be granted, it is a property of one member.
        "/licenses/{license_id}/ownership-transfers": "checks principal.is_owner",
        # The recipient accepts; the Data tier verifies the caller IS the
        # named recipient, which a permission key cannot express.
        "/licenses/{license_id}/ownership-transfers/{transfer_id}/accept":
            "the data tier verifies the caller is the recipient",
        # A platform route, not a tenant one: require_admin plus an
        # explicit platform.admin.break_glass check.
        "/platform/licenses/{license_id}/break-glass/transfer-owner":
            "platform admin route with its own check",
    }

    def test_no_tenant_route_is_missing_its_guards(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (
            root / "application/chann_app/routers_phase2.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        problems = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            paths = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and getattr(d.func.value, "id", "") == "router"
                and d.args
                and isinstance(d.args[0], ast.Constant)
            ]
            if not paths or "{license_id}" not in paths[0]:
                continue
            if paths[0] in self.EXEMPT:
                continue

            body = ast.dump(node)
            if "_require_same_tenant" not in body:
                problems.append(f"{paths[0]} ({node.name}) — no tenant check")
            if "'require'" not in body:
                problems.append(f"{paths[0]} ({node.name}) — no permission check")

        assert not problems, "\n".join(["unguarded tenant routes:", *problems])

    def test_the_exemption_list_has_not_gone_stale(self):
        """An exemption for a route that no longer exists hides the next
        one that needs looking at."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (
            root / "application/chann_app/routers_phase2.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        declared = set()
        for node in ast.walk(tree):
            for d in getattr(node, "decorator_list", []):
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and getattr(d.func.value, "id", "") == "router"
                    and d.args
                    and isinstance(d.args[0], ast.Constant)
                ):
                    declared.add(d.args[0].value)

        stale = sorted(set(self.EXEMPT) - declared)
        assert not stale, f"exemptions for routes that no longer exist: {stale}"


class TestCreateFormsAreActuallyWired:
    """A form gated on a permission that is never loaded renders nothing.

    That shipped: all three list pages imported fetchPermissions, declared
    the state, and never called it — so `permissions.has(...)` was always
    false and the create buttons were invisible in production while the
    code looked complete in review.

    The cause was a string replacement that silently did not match. These
    assertions check the wiring rather than the intent.
    """

    LISTS = {
        "customers/CustomerList.tsx": "customer.create",
        "deals/DealList.tsx": "deal.create",
        "quotes/QuoteList.tsx": "quote.create",
    }

    def _source(self, name: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (
            root / "presentation/app/liff/sales" / name
        ).read_text(encoding="utf-8")

    def test_every_list_page_loads_the_permissions_it_gates_on(self):
        problems = []
        for name, key in self.LISTS.items():
            source = self._source(name)
            if f'permissions.has("{key}")' not in source:
                problems.append(f"{name}: no create form gated on {key}")
                continue
            if "await fetchPermissions(" not in source:
                problems.append(
                    f"{name}: gates on {key} but never calls fetchPermissions — "
                    "the button can never appear"
                )
        assert not problems, "\n".join(problems)

    def test_the_pickers_are_populated(self):
        """A deal form needs customers and a quote form needs deals. Both
        are additionally gated on the list being non-empty, so an unfilled
        picker hides the form as completely as a missing permission."""
        problems = []
        deals = self._source("deals/DealList.tsx")
        if "contacts.length > 0" in deals and "setContacts(" not in deals:
            problems.append("DealList gates on contacts but never loads them")
        quotes = self._source("quotes/QuoteList.tsx")
        if "openDeals.length > 0" in quotes and "setOpenDeals(" not in quotes:
            problems.append("QuoteList gates on openDeals but never loads them")
        assert not problems, "\n".join(problems)

    def test_each_create_form_posts_to_a_real_route(self):
        import re
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "application"))
        from chann_app.main import app

        routes = [
            re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$")
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1/")
            and "POST" in (getattr(route, "methods", set()) or set())
        ]

        expected = {
            "customers/CustomerList.tsx": "/api/v1/licenses/X/customers",
            "deals/DealList.tsx": "/api/v1/licenses/X/deals",
            "quotes/QuoteList.tsx": "/api/v1/licenses/X/quotes",
        }
        for name, probe in expected.items():
            assert any(rx.match(probe.replace("X", "placeholder")) for rx in routes), (
                f"{name} posts to {probe}, which no Application route serves"
            )


class TestSchemasCarryWhatCallersRead:
    """The Application Tier reading a field the Data Tier never sends.

    member["id"] had been read since Phase 12 and MemberOut has never
    declared it, so every technician's "งานของฉัน" raised KeyError and
    answered with a generic apology. Nothing caught it because the chat
    fake returned an "id" the real endpoint did not.

    This compares the fields the Application Tier subscripts out of a
    member against what the schema promises.
    """

    def test_member_out_declares_every_field_the_app_reads(self):
        import re
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "data"))
        from chann_data.schemas import MemberOut

        declared = set(MemberOut.model_fields)

        read = set()
        for name in ("services/chat.py", "routers_phase2.py"):
            source = (
                root / "application/chann_app" / name
            ).read_text(encoding="utf-8")
            read |= set(re.findall(r'\bmember\[\s*"(\w+)"\s*\]', source))

        missing = sorted(read - declared)
        assert not missing, (
            "the Application Tier reads these off a member, and MemberOut "
            f"does not send them: {missing}"
        )

    def test_the_chat_fake_matches_the_schema(self):
        """A fake more generous than the real client hides exactly this."""
        import re
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "data"))
        from chann_data.schemas import MemberOut

        source = (
            root / "tests/unit/test_phase6_chat.py"
        ).read_text(encoding="utf-8")
        block = re.search(
            r"async def get_member\(.*?\n        \}", source, re.S,
        )
        assert block, "get_member not found in the fake"
        faked = set(re.findall(r'"(\w+)":', block.group(0)))

        invented = sorted(faked - set(MemberOut.model_fields))
        assert not invented, (
            f"the fake returns fields MemberOut does not: {invented}"
        )



class TestCreatingFromWhereYouAlreadyAre:
    """A record you are looking at answers its own question.

    Opening a deal from the deal list has to ask which customer; opening
    one from a customer's page does not, because the page already knows.
    Making someone answer it anyway is the friction that sends them back
    to chat.
    """

    def _source(self, name: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (
            root / "presentation/app/liff/sales" / name
        ).read_text(encoding="utf-8")

    def test_a_customer_page_can_open_a_deal(self):
        source = self._source("customers/[id]/CustomerDetail.tsx")
        assert "createDeal" in source
        assert '"/api/phase2/licenses/${licenseId}/deals"' in source or (
            "/deals`" in source and 'method: "POST"' in source
        )

    def test_a_deal_page_can_create_a_quote(self):
        source = self._source("deals/[id]/DealDetail.tsx")
        assert "createQuote" in source
        assert 'method: "POST"' in source

    def test_long_lists_are_searchable(self):
        """A native select is fine for four options and useless for four
        hundred: a shop with a real customer list cannot scroll to find
        someone, and on a phone the list closes if you look away."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        form = (
            root / "presentation/app/liff/_inline-create.tsx"
        ).read_text(encoding="utf-8")
        assert "SearchablePicker" in form, (
            "the shared create form still uses a plain select for pickers"
        )
        assert "<select" not in form, (
            "a native select survived in the create form"
        )
