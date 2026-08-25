#!/usr/bin/env bash
# Deterministic, non-cloud source verification for Google Cloud Shell.
# Creates only temporary local containers/files. It never calls gcloud and
# never runs terraform plan/apply/import.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
SOURCE_PHASE="${CHANN_SOURCE_PHASE:-1}"
MIGRATION_HEAD="$(basename "$(find "${ROOT}/database/alembic/versions" -maxdepth 1 -type f -name '*.py' | sort | tail -1)" .py)"
RESULT_FILE="${ROOT}/phase${SOURCE_PHASE}-source-verification-${RUN_ID}.txt"
WORK_DIR="$(mktemp -d)"
POSTGRES_CONTAINER=""
TERRAFORM_VERSION="${CHANN_TERRAFORM_VERSION:-1.15.8}"

cleanup() {
  if [[ -n "${POSTGRES_CONTAINER}" ]]; then
    docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true
  fi
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

log() { printf '%s\n' "$*" | tee -a "${RESULT_FILE}"; }
run() {
  local label="$1"
  shift
  log "CHECK=${label} STATUS=RUNNING"
  if "$@" >>"${RESULT_FILE}" 2>&1; then
    log "CHECK=${label} STATUS=PASS"
  else
    local rc=$?
    log "CHECK=${label} STATUS=FAIL RC=${rc}"
    tail -80 "${RESULT_FILE}"
    exit "${rc}"
  fi
}

cd "${ROOT}"
: >"${RESULT_FILE}"
log "MODE=LOCAL_SOURCE_VALIDATION_NO_CLOUD_MUTATION"
log "RUN_ID=${RUN_ID}"
log "NO_GCLOUD_CALLS=YES"
log "NO_TERRAFORM_PLAN_APPLY_IMPORT=YES"
log "RUNTIME_BUSINESS_ACCEPTANCE=NOT_VERIFIED"
log "MIGRATION_HEAD=${MIGRATION_HEAD}"

for command_name in python3 node npm docker curl unzip zip sha256sum; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    log "CHECK=PREREQUISITE STATUS=FAIL MISSING=${command_name}"
    exit 10
  }
done
run DOCKER_ACCESS docker info

python3 -m venv "${WORK_DIR}/venv"
run PYTHON_DEPENDENCIES "${WORK_DIR}/venv/bin/pip" install \
  -r data/requirements.txt -r application/requirements.txt \
  -r database/requirements.txt -r requirements-test.txt
run PYTHON_BOUNDARY_UNIT "${WORK_DIR}/venv/bin/python" -m pytest \
  tests/boundary tests/unit -q

# Catches ORM constructor kwargs that are not real columns. Runs before the
# Postgres steps on purpose: it needs no database, and this exact class of
# bug once passed lint, syntax checks and 148 unit tests before a real
# database caught it. Failing here is seconds instead of minutes.
run MODEL_KWARGS "${WORK_DIR}/venv/bin/python" scripts/check-model-kwargs.py

POSTGRES_CONTAINER="chann-crm-ai-verify-${RUN_ID,,}"
run POSTGRES_START docker run -d --name "${POSTGRES_CONTAINER}" \
  -e POSTGRES_USER=chann -e POSTGRES_PASSWORD=chann \
  -e POSTGRES_DB=chann_crm_ai_test -P postgres:16-alpine

for _ in $(seq 1 60); do
  if docker exec "${POSTGRES_CONTAINER}" pg_isready -U chann >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "${POSTGRES_CONTAINER}" pg_isready -U chann >/dev/null 2>&1 || {
  log "CHECK=POSTGRES_READY STATUS=FAIL"
  exit 11
}
log "CHECK=POSTGRES_READY STATUS=PASS"

POSTGRES_PORT="$(docker port "${POSTGRES_CONTAINER}" 5432/tcp | awk -F: 'NR==1{print $NF}')"
export TEST_DATABASE_URL="postgresql+psycopg://chann:chann@127.0.0.1:${POSTGRES_PORT}/chann_crm_ai_test"
run DATABASE_FROM_EMPTY "${WORK_DIR}/venv/bin/python" -m pytest tests/integration -q

if [[ -f presentation/package-lock.json ]]; then
  run PRESENTATION_DEPENDENCIES npm ci --cache "${WORK_DIR}/npm-cache" --no-audit --no-fund \
    --prefix presentation
else
  run PRESENTATION_DEPENDENCIES npm install --cache "${WORK_DIR}/npm-cache" --no-audit --no-fund \
    --prefix presentation
  log "CHECK=PRESENTATION_LOCK_GENERATED STATUS=PASS FILE=presentation/package-lock.json"
fi
run PRESENTATION_TYPECHECK npm run typecheck --prefix presentation
run PRESENTATION_BUILD env NEXT_TELEMETRY_DISABLED=1 npm run build --prefix presentation

for tier in data application presentation; do
  run "DOCKER_BUILD_${tier^^}" docker build -t "chann-crm-ai/${tier}:source-verify" "${tier}"
done

case "$(uname -m)" in
  x86_64) terraform_arch="amd64" ;;
  aarch64|arm64) terraform_arch="arm64" ;;
  *) log "CHECK=TERRAFORM_ARCH STATUS=FAIL"; exit 12 ;;
esac
terraform_zip="terraform_${TERRAFORM_VERSION}_linux_${terraform_arch}.zip"
terraform_base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"
run TERRAFORM_DOWNLOAD curl --fail --silent --show-error --location \
  "${terraform_base}/${terraform_zip}" --output "${WORK_DIR}/${terraform_zip}"
run TERRAFORM_CHECKSUMS curl --fail --silent --show-error --location \
  "${terraform_base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" \
  --output "${WORK_DIR}/terraform_SHA256SUMS"
checksum_line="$(grep " ${terraform_zip}$" "${WORK_DIR}/terraform_SHA256SUMS")"
printf '%s\n' "${checksum_line}" >"${WORK_DIR}/terraform_expected"
run TERRAFORM_SHA256 bash -c \
  "cd '${WORK_DIR}' && sha256sum --check --status terraform_expected"
mkdir -p "${WORK_DIR}/terraform-bin"
unzip -q "${WORK_DIR}/${terraform_zip}" -d "${WORK_DIR}/terraform-bin"
terraform_bin="${WORK_DIR}/terraform-bin/terraform"
export TF_DATA_DIR="${WORK_DIR}/terraform-data"
run TERRAFORM_FMT "${terraform_bin}" -chdir=infrastructure/terraform fmt -check -recursive
run TERRAFORM_INIT_NO_BACKEND "${terraform_bin}" -chdir=infrastructure/terraform init -backend=false
run TERRAFORM_VALIDATE "${terraform_bin}" -chdir=infrastructure/terraform validate

run RELEASE_MANIFEST "${WORK_DIR}/venv/bin/python" scripts/release-manifest.py \
  --phase "${SOURCE_PHASE}" --platform-version "0.${SOURCE_PHASE}.0-source-verify" --environment dev \
  --migration-head "${MIGRATION_HEAD}" \
  --verification boundary_tests=PASS \
  --verification auth_tests=PASS \
  --verification multi_tenant_isolation=PASS \
  --verification database_from_empty=PASS \
  --verification presentation_build=PASS \
  --verification runtime_smoke=NOT_VERIFIED \
  --out "${WORK_DIR}/release-manifest.json"

VERIFIED_ARCHIVE="$(dirname "${ROOT}")/chann-crm-ai-phase${SOURCE_PHASE}-source-verified-${RUN_ID}.zip"
log "RESULT=SOURCE_VALIDATION_PASS_RUNTIME_ACCEPTANCE_NOT_VERIFIED"
log "RESULT_FILE=${RESULT_FILE}"
log "VERIFIED_ARCHIVE=${VERIFIED_ARCHIVE}"
(cd "$(dirname "${ROOT}")" && zip -q -r "${VERIFIED_ARCHIVE}" "$(basename "${ROOT}")" \
  -x '*/node_modules/*' '*/.next/*' '*/__pycache__/*' '*/.pytest_cache/*' \
     '*/.terraform/*' '*/.artifacts/*' '*/envs/*/backend.hcl' \
     '*/envs/*/terraform.tfvars' '*.pyc' '*.tsbuildinfo' '*.log' '*.env' \
     '*.tfplan')
