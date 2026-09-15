#!/usr/bin/env bash
#
# Source Ledger — one-time platform provisioning via the CDP CLI.
#
# Stands up everything the accelerator needs that is not application code:
# a Data Lake, a CDE service + virtual cluster, an AI Workbench, and the Iceberg
# database. Run once per environment; `make deploy` skips it when the stack
# already exists.
#
#   ./infra/cdp/provision.sh                # dry run — prints every command
#   ./infra/cdp/provision.sh --execute      # actually provision
#
# Prerequisites: `cdp` CLI configured (`cdp configure`) with rights to create
# environments in the target cloud account.

set -euo pipefail

EXECUTE=false
[[ "${1:-}" == "--execute" ]] && EXECUTE=true

# --- Configuration ---------------------------------------------------------
# Override any of these from the environment; the defaults describe a small
# demo footprint, not a production sizing.
CDP_ENV="${CDP_ENV:-source-ledger-dev}"
CDP_REGION="${CDP_REGION:-us-west-2}"
CDP_CREDENTIAL="${CDP_CREDENTIAL:-source-ledger-cross-account}"
DATALAKE_NAME="${DATALAKE_NAME:-${CDP_ENV}-dl}"
DATALAKE_SCALE="${DATALAKE_SCALE:-LIGHT_DUTY}"
CDE_SERVICE="${CDE_SERVICE:-${CDP_ENV}-cde}"
CDE_VC="${CDE_VC:-source-ledger-vc}"
AI_WORKBENCH="${AI_WORKBENCH:-${CDP_ENV}-ai}"
STORAGE_BASE="${STORAGE_BASE:-s3a://${CDP_ENV}}"

# --- Plumbing --------------------------------------------------------------
step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

run() {
  if $EXECUTE; then
    echo "+ $*"
    "$@"
  else
    echo "    $*"
  fi
}

# Skip creation when the resource is already there, so the script is safe to
# re-run against a half-provisioned environment.
exists() {
  $EXECUTE || return 1
  "$@" >/dev/null 2>&1
}

if ! $EXECUTE; then
  cat <<'BANNER'
Source Ledger provisioning — DRY RUN
============================
No CDP resources will be created. Every command that would run is printed below.
Re-run with --execute to provision for real.
BANNER
fi

echo
echo "environment  ${CDP_ENV} (${CDP_REGION})"
echo "credential   ${CDP_CREDENTIAL}"
echo "storage      ${STORAGE_BASE}"

# --- 1. Environment + Data Lake -------------------------------------------
# The foundation: identity, storage, and SDX. Everything else attaches to it.
step "1/5  Environment and Data Lake (SDX)"
if exists cdp environments describe-environment --environment-name "${CDP_ENV}"; then
  echo "    environment ${CDP_ENV} already exists — skipping"
else
  run cdp environments create-aws-environment \
    --environment-name "${CDP_ENV}" \
    --credential-name "${CDP_CREDENTIAL}" \
    --region "${CDP_REGION}" \
    --security-access cidr=0.0.0.0/0 \
    --log-storage storageLocationBase="${STORAGE_BASE}/logs"

  run cdp environments wait-for-environment-running --environment-name "${CDP_ENV}"

  run cdp datalake create-aws-datalake \
    --datalake-name "${DATALAKE_NAME}" \
    --environment-name "${CDP_ENV}" \
    --cloud-provider-configuration "storageBucketLocation=${STORAGE_BASE}/datalake" \
    --scale "${DATALAKE_SCALE}"
fi

# --- 2. Data Engineering ---------------------------------------------------
# Runs data/ingest/load_to_iceberg.py and pipelines/jobs/url_enrichment.py.
step "2/5  Data Engineering service and virtual cluster"
run cdp de enable-service \
  --name "${CDE_SERVICE}" \
  --env "${CDP_ENV}" \
  --instance-type m5.2xlarge \
  --minimum-instances 0 \
  --maximum-instances 4 \
  --initial-instances 1 \
  --enable-workload-analytics

run cdp de create-vc \
  --name "${CDE_VC}" \
  --cluster-id "${CDE_SERVICE}" \
  --cpu-requests 4 \
  --memory-requests 16Gi \
  --spark-version SPARK3

# --- 3. AI Workbench -------------------------------------------------------
# Hosts the Serve layer as an Application, and retrieval/notebooks/ as sessions.
step "3/5  AI Workbench (Serve layer host)"
run cdp ml create-workspace \
  --workspace-name "${AI_WORKBENCH}" \
  --environment-name "${CDP_ENV}" \
  --instance-type m5.xlarge \
  --min-instances 1 \
  --max-instances 3 \
  --enable-governance

# --- 4. Iceberg database ---------------------------------------------------
# The DDL is authoritative and every statement is IF NOT EXISTS, so this is
# safe on an environment that already has the tables.
step "4/5  Iceberg database and tables"
DDL_PATH="$(dirname "$0")/../../data/iceberg/ddl.sql"
run cdp de submit-job \
  --cluster-id "${CDE_SERVICE}" \
  --vc-id "${CDE_VC}" \
  --job-type spark-sql \
  --file "${DDL_PATH}"

# --- 5. SDX policies -------------------------------------------------------
# Access control lands before any data does, not after.
step "5/5  SDX / Ranger policies"
POLICY_PATH="$(dirname "$0")/../../governance/sdx/ranger-policies.json"
echo "    import ${POLICY_PATH} into Ranger for ${CDP_ENV}"
echo "    (see governance/README.md — run '.cicd/deploy.sh govern')"

# --- Done ------------------------------------------------------------------
if $EXECUTE; then
  printf '\n\033[1mProvisioning submitted.\033[0m Environment creation takes ~60 min.\n'
  echo "Watch:  cdp environments describe-environment --environment-name ${CDP_ENV}"
  echo "Then:   make deploy"
else
  printf '\n\033[1mDry run complete.\033[0m Re-run with --execute to provision.\n'
  echo "Provisioning is a one-time step — 'make deploy' skips it when the stack exists."
fi
