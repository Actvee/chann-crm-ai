#!/usr/bin/env bash
# Selective DEV deployment. DEV only — Stage and Production go through the
# release pipeline so the promoted artifact is the one that was proven.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-auto}"
BASE="${2:-origin/main}"
PROJECT="${CHANN_PROJECT:-chann1-1}"
REGION="${CHANN_REGION:-asia-southeast1}"

if [ "${APP_ENV:-dev}" != "dev" ]; then
  echo "REFUSING: dev-deploy.sh is for DEV only (APP_ENV=${APP_ENV:-dev})" >&2
  exit 1
fi

if [ "$TARGET" = "auto" ]; then
  TARGET="$(./scripts/detect-impact.sh "$BASE" | awk -F= '/^DEPLOY_SCOPE=/{print $2}')"
  echo "auto-detected deploy scope:$TARGET"
fi

deploy_tier() {
  local tier="$1"
  echo "--- deploying $tier ---"
  # Consolidate env vars into ONE --set-env-vars flag with a custom delimiter.
  # Passing the flag repeatedly does not merge the way it appears to, and that
  # cost the reference project real debugging time (CLAUDE.md section 7).
  echo "gcloud run deploy chann-crm-ai-dev-$tier \\"
  echo "  --project=$PROJECT --region=$REGION --source=./$tier \\"
  echo '  --set-env-vars="^@^APP_ENV=dev@PLATFORM_VERSION=$PLATFORM_VERSION@GIT_COMMIT=$GIT_COMMIT"'
  echo "(dry run — wire this up once the Terraform adoption gate has passed)"
}

case "$TARGET" in
  *infrastructure*)
    echo "REFUSING: infrastructure is outside dev-deploy.sh; run the preflight/state/backend gate and review a Terraform plan." >&2
    exit 2 ;;
  *database*|*all*) echo "--- migration gate first ---"
                     echo "cd database && alembic upgrade head && python3 scripts/seed_reference.py" ;;
esac
for tier in data application presentation; do
  case "$TARGET" in *"$tier"*|*all*) deploy_tier "$tier" ;; esac
done
echo "done. Deployment is not completion — run runtime acceptance next."
