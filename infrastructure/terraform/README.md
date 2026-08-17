# Terraform — adoption gate

**Do not run `terraform apply` until `scripts/infra-preflight.sh` has passed
and every persistent resource is classified.**

Live read-only discovery passed on 2026-08-17; see
`docs/PHASE1_READINESS_VERIFIED_2026-08-17.md`. The existing `chann1-platform`
backend buckets are absent, so they are not reusable. This root deliberately
has an empty GCS backend block and must be initialized with an app-specific,
approved backend file, for example:

```bash
terraform init -backend-config=envs/dev/backend.hcl
```

The backend bucket does not exist yet. Creating it is an infrastructure
change and is not performed by this scaffold.

## DEV-only plan gate

The checked-in `scripts/dev-infra-plan.sh` is the only supported planning
entry point for this checkpoint. It runs a fresh read-only preflight, requires
evidence no older than one hour, initializes only an explicitly configured DEV
backend, and writes a human-readable plan plus policy/checksum evidence. It
refuses non-DEV input, placeholders, deletes/replacements, and any planned IAM,
service-account, or Secret Manager resource.

The runner uses a private process umask, deletes the raw JSON plan immediately
after policy evaluation, and emits a redacted `plan-policy-summary.json` for
review. The saved binary plan remains sensitive because Terraform plans can
contain credentials; keep it local with mode `0600` and never upload it. A
subsequent approved apply must use the exact reviewed binary plan or create and
review a new one.

It contains no apply/import path. The expected invocation, after the new
state bucket has been separately approved/created and the two `.example`
files have been copied and filled, is:

```bash
APP_ENV=dev CHANN_ALLOW_DEV_TERRAFORM_PLAN=YES ./scripts/dev-infra-plan.sh
```

Do not run it yet while `envs/dev/backend.hcl` is absent. A successful plan is
still not permission to apply.

## Ownership model

| Resource | Classification | Why |
|---|---|---|
| Cloud SQL instances | `REFERENCE_ONLY_NOT_MANAGED` | `data` source. A mistaken `destroy` cannot delete a data source; it can delete an imported resource. |
| Redis instances | `REFERENCE_ONLY_NOT_MANAGED` | same |
| VPC network / connector | `REFERENCE_ONLY_NOT_MANAGED` | same |
| Artifact Registry (dev/stage) | `REFERENCE_ONLY_NOT_MANAGED` | pre-existing |
| Cloud Run services | `NEW_RESOURCE_REQUIRED` | genuinely ours |
| Application database + user | `NEW_RESOURCE_REQUIRED` | genuinely ours |
| GCS bucket | `NEW_RESOURCE_REQUIRED` if enabled and absent | Phase 10/13; feature-gated off in Phase 1 |

`ALREADY_MANAGED_BY_STATE`: none proven. Do not import/adopt the reference-only
resources just to make a plan pass.

## Current Terraform scope

This root resolves the reference-only resources and declares only these new
application-owned resource types:

- `google_sql_database` and built-in PostgreSQL `google_sql_user`;
- three `google_cloud_run_v2_service` resources, each with its immutable image
  digest, environment-specific runtime configuration, existing VPC connector,
  private Cloud SQL/Redis path, deletion protection, and bounded scaling;
- an optional private/versioned `google_storage_bucket`, disabled by default
  until a file phase requires it and preflight proves the chosen name absent.

No IAM policy/binding, Service Account, or Secret Manager resource is declared.
The approved DEV capability-limited exception sets
`dev_reduced_security_disable_invoker_iam_check = true` for all three Cloud Run
services. Application-layer controls remain mandatory: LINE signatures/JWT in
Application and the shared internal secret in Data. A lifecycle precondition
blocks this exception outside DEV, and the DEV plan gate rejects a service
whose planned `invoker_iam_disabled` value is not explicitly true. Stage and
Production examples keep the value false. This is reproducible lifecycle proof
with a declared security limitation, not production-grade service isolation.
Cloud Run documents that disabling this check still requires the operator to
possess `run.services.setIamPolicy`; that capability is not inspected here. A
future DEV apply must stop on a permission error rather than widening IAM scope.

The database password, LIFF identifiers, and reduced-security runtime
secrets are sensitive Terraform inputs, but Terraform state still stores them. This is an explicit
project limitation, not equivalent to Secret Manager. Never commit the real
`terraform.tfvars` or backend file; repository ignore rules cover both.

Switching a resource from `data` to `resource` later is a deliberate decision
to take ownership, not a default.

## Names to fill in

`ENVIRONMENT_RESOURCE_MAP.yaml` marks the network and connector names
`VERIFY_WITH_PREFLIGHT` in the original handoff. The checked-in examples now
contain the names proven by the 2026-08-17 read-only inventory; re-run preflight
before a future plan because live infrastructure can change.
