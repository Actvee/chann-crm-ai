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
