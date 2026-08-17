#!/usr/bin/env bash
# DEV-only infrastructure planning gate.
#
# This script performs read-only preflight discovery and Terraform planning.
# It never runs apply/import and rejects plans that touch IAM, service
# accounts, Secret Manager, or delete/replace a managed resource.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_ROOT="${ROOT}/infrastructure/terraform"
PROJECT="${CHANN_PROJECT:-chann1-1}"
REGION="${CHANN_REGION:-asia-southeast1}"
TFVARS_FILE="${CHANN_DEV_TFVARS:-${TF_ROOT}/envs/dev/terraform.tfvars}"
BACKEND_FILE="${CHANN_DEV_BACKEND:-${TF_ROOT}/envs/dev/backend.hcl}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="${CHANN_PLAN_OUTPUT_DIR:-${ROOT}/.artifacts/dev-infra-plan-${RUN_ID}}"
EVIDENCE_FILE="${OUTPUT_DIR}/preflight-evidence.json"
PLAN_FILE="${OUTPUT_DIR}/dev.tfplan"

fail() {
  printf 'STATUS=BLOCKED\nREASON=%s\n' "$1" >&2
  exit "${2:-1}"
}

[[ "${APP_ENV:-}" == "dev" ]] || fail "APP_ENV_must_equal_dev" 10
[[ "${CHANN_ALLOW_DEV_TERRAFORM_PLAN:-}" == "YES" ]] || \
  fail "set_CHANN_ALLOW_DEV_TERRAFORM_PLAN=YES_for_read_only_DEV_plan" 11

for command_name in gcloud terraform python3 sha256sum; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing_${command_name}" 12
done

[[ -f "${TFVARS_FILE}" ]] || fail "missing_DEV_tfvars:${TFVARS_FILE}" 13
[[ -f "${BACKEND_FILE}" ]] || fail "missing_approved_backend_file:${BACKEND_FILE}" 14

if grep -Eq 'VERIFY_WITH_PREFLIGHT|REQUIRED|CHANGEME|PLACEHOLDER' \
  "${TFVARS_FILE}" "${BACKEND_FILE}"; then
  fail "placeholder_detected_in_DEV_Terraform_inputs" 15
fi

grep -Eq '^[[:space:]]*environment[[:space:]]*=[[:space:]]*"dev"[[:space:]]*$' \
  "${TFVARS_FILE}" || fail "tfvars_environment_must_equal_dev" 16
grep -Eq "^[[:space:]]*project_id[[:space:]]*=[[:space:]]*\"${PROJECT}\"[[:space:]]*$" \
  "${TFVARS_FILE}" || fail "tfvars_project_must_equal_${PROJECT}" 17
grep -Eq '^[[:space:]]*enable_cloud_run_services[[:space:]]*=[[:space:]]*true[[:space:]]*$' \
  "${TFVARS_FILE}" || fail "complete_DEV_plan_requires_Cloud_Run_x3_enabled" 18

mkdir -p "${OUTPUT_DIR}"

printf 'MODE=DEV_PLAN_ONLY_NO_APPLY_NO_IMPORT\n'
printf 'RUN_ID=%s\nPROJECT=%s\nREGION=%s\n' "${RUN_ID}" "${PROJECT}" "${REGION}"
printf 'NO_IAM_INSPECTION_OR_CHANGE=YES\n'
printf 'NO_SERVICE_ACCOUNT_INSPECTION_OR_CHANGE=YES\n'
printf 'NO_SECRET_MANAGER_INSPECTION_OR_CHANGE=YES\n'

CHANN_PROJECT="${PROJECT}" CHANN_REGION="${REGION}" \
CHANN_PREFLIGHT_EVIDENCE="${EVIDENCE_FILE}" \
  "${ROOT}/scripts/infra-preflight.sh"

python3 - "${EVIDENCE_FILE}" "${PROJECT}" "${REGION}" <<'PY'
import datetime as dt
import json
import pathlib
import sys

path, project, region = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
evidence = json.loads(path.read_text(encoding="utf-8"))
assert evidence["mode"] == "READ_ONLY", "preflight mode is not READ_ONLY"
assert evidence["destructive_change"] is False, "preflight reports a destructive change"
assert evidence["hard_failures"] == 0, "preflight has hard failures"
assert evidence["project"] == project, "preflight project mismatch"
assert evidence["region"] == region, "preflight region mismatch"
generated = dt.datetime.fromisoformat(evidence["generated_at"].replace("Z", "+00:00"))
age = dt.datetime.now(dt.timezone.utc) - generated
assert age <= dt.timedelta(hours=1), "preflight evidence is older than one hour"
PY

terraform -chdir="${TF_ROOT}" init -input=false -reconfigure \
  -backend-config="${BACKEND_FILE}"
terraform -chdir="${TF_ROOT}" validate

set +e
terraform -chdir="${TF_ROOT}" plan -input=false -lock-timeout=60s \
  -detailed-exitcode -var-file="${TFVARS_FILE}" -out="${PLAN_FILE}"
plan_rc=$?
set -e
[[ "${plan_rc}" -eq 0 || "${plan_rc}" -eq 2 ]] || fail "terraform_plan_failed" "${plan_rc}"

terraform -chdir="${TF_ROOT}" show -json "${PLAN_FILE}" >"${OUTPUT_DIR}/plan.json"
terraform -chdir="${TF_ROOT}" show -no-color "${PLAN_FILE}" >"${OUTPUT_DIR}/plan.txt"

python3 - "${OUTPUT_DIR}/plan.json" <<'PY'
import json
import pathlib
import sys

plan = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
forbidden_fragments = ("_iam", "service_account", "secret_manager")
allowed_managed_types = {
    "google_sql_database",
    "google_sql_user",
    "google_cloud_run_v2_service",
    "google_storage_bucket",
}
required_addresses = {
    "google_sql_database.application",
    "google_sql_user.application",
    "google_cloud_run_v2_service.data[0]",
    "google_cloud_run_v2_service.application[0]",
    "google_cloud_run_v2_service.presentation[0]",
}
counts = {}
observed_addresses = set()
for change in plan.get("resource_changes", []):
    resource_type = change.get("type", "")
    mode = change.get("mode")
    actions = change.get("change", {}).get("actions", [])
    address = change.get("address", "")
    if mode == "managed":
        observed_addresses.add(address)
    if mode == "managed" and any(fragment in resource_type for fragment in forbidden_fragments):
        raise SystemExit(f"BLOCKED forbidden managed resource type: {resource_type}")
    if mode == "managed" and resource_type not in allowed_managed_types:
        raise SystemExit(f"BLOCKED managed resource outside DEV boundary: {address} ({resource_type})")
    if mode == "managed" and "delete" in actions:
        raise SystemExit(f"BLOCKED delete/replace action: {address} {actions}")
    key = "/".join(actions) if actions else "none"
    counts[key] = counts.get(key, 0) + 1

missing = sorted(required_addresses - observed_addresses)
if missing:
    raise SystemExit("BLOCKED incomplete DEV plan; missing: " + ", ".join(missing))

print("PLAN_POLICY=PASS")
print("PLAN_ACTION_COUNTS=" + json.dumps(counts, sort_keys=True, separators=(",", ":")))
PY

(cd "${OUTPUT_DIR}" && sha256sum preflight-evidence.json plan.json plan.txt dev.tfplan >SHA256SUMS)
printf 'STATUS=DEV_PLAN_PASS_NOT_APPLIED\n'
printf 'PLAN_SUMMARY=%s\n' "${OUTPUT_DIR}/plan.txt"
printf 'PLAN_POLICY_JSON=%s\n' "${OUTPUT_DIR}/plan.json"
printf 'CHECKSUMS=%s\n' "${OUTPUT_DIR}/SHA256SUMS"
