#!/usr/bin/env bash
# Selective deployment, dependency-aware validation (playbook 02 section 4).
#
# Two different questions, deliberately answered separately:
#   DEPLOY_SCOPE   the smallest set of runtimes that must be redeployed
#   VALIDATE_SCOPE every tier that could be affected by the change
#
# They are not the same set. A Data Tier change deploys one service but can
# break every tier above it.
set -euo pipefail

BASE="${1:-origin/main}"
if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  echo "CHANGED_FILES=unknown"
  echo "DEPLOY_SCOPE=all"
  echo "VALIDATE_SCOPE=all"
  echo "IMPACT_REASON=base_ref_unavailable_fail_closed"
  exit 0
fi
CHANGED="$(git diff --name-only "$BASE"...HEAD)"

deploy=""; validate=""
add_deploy()   { case " $deploy "   in *" $1 "*) ;; *) deploy="$deploy $1";;   esac; }
add_validate() { case " $validate " in *" $1 "*) ;; *) validate="$validate $1";; esac; }

while read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    presentation/*)
      add_deploy presentation
      add_validate presentation ;;
    application/*)
      add_deploy application
      add_validate application; add_validate presentation; add_validate cross_tier ;;
    data/*)
      add_deploy data
      add_validate data; add_validate application; add_validate presentation; add_validate cross_tier ;;
    database/*)
      add_deploy database
      add_validate database; add_validate data; add_validate application
      add_validate presentation; add_validate cross_tier ;;
    tests/*|scripts/*|.github/*)
      add_validate all ;;
    docker-compose.yml|Makefile|pytest.ini|requirements-test.txt|.env.example)
      add_validate all ;;
    infrastructure/*)
      add_deploy infrastructure; add_validate all ;;
  esac
done <<< "$CHANGED"

echo "CHANGED_FILES=$(printf '%s\n' "$CHANGED" | grep -c . || true)"
echo "DEPLOY_SCOPE=${deploy:-none}"
echo "VALIDATE_SCOPE=${validate:-none}"
