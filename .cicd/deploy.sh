#!/usr/bin/env bash
#
# Source Ledger — deploy the accelerator to a provisioned CDP environment.
#
# Deploy is not provisioning. This script assumes the stack from
# infra/cdp/provision.sh already exists and only ships what changed:
#
#   govern   import SDX / Ranger policies      (governance/sdx/)
#   jobs     upload + register CDE jobs        (data/ingest/, pipelines/jobs/)
#   app      publish the Serve layer           (app/)
#   all      all three, in that order          ← default
#
# Order matters: access control lands before data moves, and data lands before
# anything serves it.
#
#   .cicd/deploy.sh                  # dry run — prints every command
#   .cicd/deploy.sh all --execute    # ship it
#   .cicd/deploy.sh app --execute    # ship only the Serve layer

set -euo pipefail

TARGET="all"
EXECUTE=false
for arg in "$@"; do
  case "$arg" in
    govern|jobs|app|all) TARGET="$arg" ;;
    --execute)           EXECUTE=true ;;
    -h|--help)           sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

CDP_ENV="${CDP_ENV:-source-ledger-dev}"
CDE_SERVICE="${CDE_SERVICE:-${CDP_ENV}-cde}"
CDE_VC="${CDE_VC:-source-ledger-vc}"
CDE_RESOURCE="${CDE_RESOURCE:-source-ledger-jobs}"
AI_WORKBENCH="${AI_WORKBENCH:-${CDP_ENV}-ai}"
RANGER_URL="${RANGER_URL:-https://${CDP_ENV}-ranger.cloudera.site}"

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

run() {
  if $EXECUTE; then
    echo "+ $*"
    "$@"
  else
    echo "    $*"
  fi
}

if ! $EXECUTE; then
  cat <<'BANNER'
Source Ledger deploy — DRY RUN
======================
Nothing will be changed. Every command that would run is printed below.
Re-run with --execute to deploy.
BANNER
fi

echo
echo "target       ${TARGET}"
echo "environment  ${CDP_ENV}"
echo "commit       $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"

# --- govern ----------------------------------------------------------------
# First, always. Policies exist before there is data for them to protect.
deploy_govern() {
  step "SDX — import Ranger policies"
  local policies="${ROOT}/governance/sdx/ranger-policies.json"

  # Fail early on a malformed file rather than half-importing a policy set.
  run python -c "import json,sys; json.load(open(sys.argv[1]))" "$policies"

  # updateIfExists makes the import idempotent, so re-running on every merge is
  # safe and the committed file stays the source of truth.
  run curl -sS -u "${RANGER_ADMIN_USER:-admin}:${RANGER_ADMIN_PASSWORD:-}" \
    -X POST -H 'Content-Type: application/json' \
    -d "@${policies}" \
    "${RANGER_URL}/service/plugins/policies/importPoliciesFromFile?updateIfExists=true"

  echo "    classifications in that file are applied to Atlas at table creation"
}

# --- jobs ------------------------------------------------------------------
# Upload the code, then re-import the job definitions so the schedule always
# matches the committed source.
deploy_jobs() {
  step "Data Engineering — upload job code"
  run cde resource create --name "${CDE_RESOURCE}" --type files || true
  run cde resource upload --name "${CDE_RESOURCE}" \
    --local-path "${ROOT}/pipelines/jobs/url_enrichment.py"
  run cde resource upload --name "${CDE_RESOURCE}" \
    --local-path "${ROOT}/data/ingest/load_to_iceberg.py"

  step "Data Engineering — register job definitions"
  run cde job import --file "${ROOT}/pipelines/cde/url_enrichment.job.yaml"

  step "Iceberg — apply DDL (IF NOT EXISTS throughout, safe to re-run)"
  run cde spark submit-sql --file "${ROOT}/data/iceberg/ddl.sql" \
    --vc-id "${CDE_VC}"
}

# --- app -------------------------------------------------------------------
# The Serve layer runs as a Cloudera AI Application. Restart picks up the new
# code; there is no build artifact because there is no build step.
deploy_app() {
  step "Serve layer — publish to Cloudera AI"
  run cdp ml create-application \
    --workspace-name "${AI_WORKBENCH}" \
    --name source_ledger \
    --subdomain source_ledger \
    --script "app/server.py" \
    --kernel python3 \
    --cpu 1 --memory 2 || true

  run cdp ml restart-application \
    --workspace-name "${AI_WORKBENCH}" \
    --name source_ledger

  echo "    → https://${CDP_ENV}-ai.cloudera.site/source_ledger"
}

case "$TARGET" in
  govern) deploy_govern ;;
  jobs)   deploy_jobs ;;
  app)    deploy_app ;;
  all)    deploy_govern; deploy_jobs; deploy_app ;;
esac

if $EXECUTE; then
  printf '\n\033[1mDeployed.\033[0m\n'
else
  printf '\n\033[1mDry run complete.\033[0m Re-run with --execute to deploy.\n'
fi
