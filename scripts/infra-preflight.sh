#!/usr/bin/env bash
#
# infra-preflight.sh (v2) — read-only GCP inventory for Chann CRM AI.
#
# Why v2 exists
# -------------
# v1 wrapped every command in a helper that swallowed the exit code and
# returned 0, then printed STATUS=PREFLIGHT_COMPLETE unconditionally. It
# therefore "passed" with gcloud missing entirely. CLAUDE.md uses preflight
# as the gate before the first terraform apply, so a gate that cannot fail is
# worse than no gate: it manufactures confidence at the exact point where
# confidence is dangerous.
#
# v2 separates two ideas that v1 conflated:
#   require  — if this fails, preflight FAILS and exits non-zero
#   probe    — absence is a legitimate finding, recorded, not fatal
#
# Still strictly read-only. No create/update/delete. No IAM inspection.
# No Secret Manager inspection.
#
set -uo pipefail

PROJECT="${CHANN_PROJECT:-chann1-1}"
REGION="${CHANN_REGION:-asia-southeast1}"
EVIDENCE="${CHANN_PREFLIGHT_EVIDENCE:-preflight-evidence.json}"

SQL_INSTANCES=(chann1-dev-pg chann1-stage-pg chann1-prod-pg)
AR_REPOS=(chann1-dev chann1-stage)

FAILURES=0
FINDINGS=()

c_red()  { printf '\033[31m%s\033[0m\n' "$1"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$1"; }
c_ylw()  { printf '\033[33m%s\033[0m\n' "$1"; }

section() { printf '\n===== %s =====\n' "$1"; }

record() { # name status detail
  FINDINGS+=("{\"check\":\"$1\",\"status\":\"$2\",\"detail\":\"$(printf '%s' "$3" | tr '"' "'" | tr -d '\n')\"}")
}

require() { # description, then command
  local desc="$1"; shift
  if out=$("$@" 2>&1); then
    c_grn "  PASS  $desc"
    record "$desc" "PASS" "$out"
  else
    c_red "  FAIL  $desc"
    printf '        %s\n' "$out" | head -5
    record "$desc" "FAIL" "$out"
    FAILURES=$((FAILURES + 1))
  fi
}

probe() { # description, then command — absence is a finding, not a failure
  local desc="$1"; shift
  if out=$("$@" 2>&1); then
    printf '  ----  %s\n' "$desc"
    printf '%s\n' "$out" | sed 's/^/        /'
    record "$desc" "FOUND" "$out"
  else
    c_ylw "  ABSENT/ERROR  $desc"
    record "$desc" "ABSENT_OR_ERROR" "$out"
  fi
}

echo "===== CHANN CRM AI INFRA PREFLIGHT (v2) ====="
echo "MODE=READ_ONLY"
echo "PROJECT=$PROJECT"
echo "REGION=$REGION"
echo "NO_IAM_INSPECTION=YES"
echo "NO_SECRET_MANAGER_INSPECTION=YES"

# --------------------------------------------------------------------------
section "0. TOOLING AND AUTHENTICATION (hard requirements)"
# Everything below is meaningless without these, so they are `require`.
# --------------------------------------------------------------------------
require "gcloud is installed" command -v gcloud
require "gcloud has an authenticated account" bash -c \
  'test -n "$(gcloud config get-value account 2>/dev/null | grep -v unset)"'
require "target project is reachable" gcloud projects describe "$PROJECT" --format='value(projectId)'

if [ "$FAILURES" -gt 0 ]; then
  c_red ""
  c_red "PREFLIGHT FAILED at the tooling stage — every inventory result below"
  c_red "would be an empty set caused by local setup, not by GCP reality."
  echo "STATUS=PREFLIGHT_FAILED"
  echo "DESTRUCTIVE_CHANGE=NO"
  exit 1
fi

# --------------------------------------------------------------------------
section "1. PERSISTENT INFRASTRUCTURE EXPECTED TO EXIST"
# Documented as present. If any is genuinely gone, that changes the plan, so
# these are `require` — we want a non-zero exit, not a shrug.
# --------------------------------------------------------------------------
require "Cloud SQL instances listable" gcloud sql instances list \
  --project="$PROJECT" --format='table(name,region,databaseVersion,state,settings.tier)'
require "Redis instances listable" gcloud redis instances list \
  --project="$PROJECT" --region="$REGION" \
  --format='table(name,region,tier,memorySizeGb,host,port,state)'
require "VPC networks listable" gcloud compute networks list \
  --project="$PROJECT" --format='table(name,subnetMode,routingConfig.routingMode)'
require "Serverless VPC connectors listable" gcloud compute networks vpc-access connectors list \
  --project="$PROJECT" --region="$REGION" \
  --format='table(name,network,ipCidrRange,state)'
require "Artifact Registry repositories listable" gcloud artifacts repositories list \
  --project="$PROJECT" --location="$REGION" \
  --format='table(name.basename(),format,mode,createTime)'

# --------------------------------------------------------------------------
section "2. APPLICATION STATE EXPECTED TO BE ABSENT"
# Cloud Run services and the old application database were removed by intent.
# Their absence is the expected finding; their presence is important news.
# --------------------------------------------------------------------------
probe "Cloud Run services" gcloud run services list \
  --project="$PROJECT" --region="$REGION" \
  --format='table(metadata.name,status.url,status.latestReadyRevisionName)'

for inst in "${SQL_INSTANCES[@]}"; do
  probe "databases on $inst" gcloud sql databases list \
    --project="$PROJECT" --instance="$inst" --format='table(name,charset,collation)'
  # v1 never looked at users, yet the handoff asserts the old app users were
  # deleted. DB users are not IAM, so they remain in scope.
  probe "sql users on $inst" gcloud sql users list \
    --project="$PROJECT" --instance="$inst" --format='table(name,host,type)'
done

# --------------------------------------------------------------------------
section "3. GAPS v1 NEVER LOOKED AT"
# --------------------------------------------------------------------------
# RUNTIME_CONFIG_CONTRACT marks GCS_BUCKET_NAME REQUIRED_BY_FILE_FEATURES,
# but no bucket name appears anywhere in the resource map.
probe "GCS buckets" gcloud storage buckets list --project="$PROJECT" \
  --format='table(name,location,storageClass)'

# ENVIRONMENT_RESOURCE_MAP marks the Production registry VERIFY_WITH_PREFLIGHT,
# yet v1 only ever looped over the dev and stage repositories.
for repo in "${AR_REPOS[@]}"; do
  probe "packages in $repo" gcloud artifacts packages list \
    --project="$PROJECT" --location="$REGION" --repository="$repo" \
    --format='table(name.basename(),createTime,updateTime)'
done
probe "any repository matching prod" bash -c \
  "gcloud artifacts repositories list --project='$PROJECT' --location='$REGION' \
   --format='value(name.basename())' | grep -i prod || echo 'no production-named repository found'"

probe "Cloud Scheduler jobs" gcloud scheduler jobs list \
  --project="$PROJECT" --location="$REGION" --format='table(name,schedule,state)'
probe "Cloud Run jobs" gcloud run jobs list \
  --project="$PROJECT" --region="$REGION" --format='table(name,lastModifier)'

# --------------------------------------------------------------------------
section "4. TERRAFORM STATE"
# The adoption gate depends on knowing whether state exists at all. v1 never
# touched this, which left the most important question unanswered.
# --------------------------------------------------------------------------
if command -v terraform >/dev/null 2>&1; then
  probe "terraform version" terraform version
else
  c_ylw "  terraform is not installed locally — state classification cannot be"
  c_ylw "  completed from this machine."
  record "terraform available" "ABSENT_OR_ERROR" "terraform not installed"
fi
probe "candidate terraform state buckets" bash -c \
  "gcloud storage buckets list --project='$PROJECT' --format='value(name)' 2>/dev/null \
   | grep -iE 'tfstate|terraform' || echo 'no bucket name suggests terraform state'"

# --------------------------------------------------------------------------
section "5. EVIDENCE"
# --------------------------------------------------------------------------
{
  printf '{\n'
  printf '  "generated_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "project": "%s",\n' "$PROJECT"
  printf '  "region": "%s",\n' "$REGION"
  printf '  "mode": "READ_ONLY",\n'
  printf '  "destructive_change": false,\n'
  printf '  "hard_failures": %s,\n' "$FAILURES"
  printf '  "findings": [\n'
  first=1
  for f in "${FINDINGS[@]}"; do
    if [ "$first" -eq 1 ]; then first=0; else printf ',\n'; fi
    printf '    %s' "$f"
  done
  printf '\n  ]\n}\n'
} > "$EVIDENCE"

echo "  evidence written to $EVIDENCE"

section "6. NEXT STEP — TERRAFORM ADOPTION GATE"
cat <<'TXT'
Classify every persistent resource above before the first terraform apply:

  ALREADY_MANAGED_BY_STATE
  IMPORT_OR_ADOPT_REQUIRED
  REFERENCE_ONLY_NOT_MANAGED
  NEW_RESOURCE_REQUIRED

Recommended default for Cloud SQL / Redis / VPC / connector:
REFERENCE_ONLY_NOT_MANAGED via `data` sources. A data source cannot be
destroyed by a mistaken plan; an imported resource can.
TXT

if [ "$FAILURES" -gt 0 ]; then
  c_red ""
  c_red "PREFLIGHT FAILED ($FAILURES hard failures)"
  echo "STATUS=PREFLIGHT_FAILED"
  echo "DESTRUCTIVE_CHANGE=NO"
  exit 1
fi

c_grn ""
c_grn "PREFLIGHT PASSED"
echo "STATUS=PREFLIGHT_COMPLETE"
echo "DESTRUCTIVE_CHANGE=NO"
exit 0
