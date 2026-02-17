#!/usr/bin/env bash
# deploy.sh — Deploy GPU Cross-Region Dynamic Scheduler
#
# Usage:
#   ./scripts/deploy.sh [ENV]
#
# ENV: dev | staging | prod  (default: dev)
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - Python 3.11+
#   - pip / virtualenv
#
# What this script does:
#   1. Validates environment and AWS credentials
#   2. Deploys DynamoDB table via CloudFormation
#   3. Uploads region config to SSM Parameter Store
#   4. Installs Python dependencies
#   5. Runs smoke tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV="${1:-dev}"
VALID_ENVS=("dev" "staging" "prod")

# ── Helpers ──────────────────────────────────────────────────────────────

log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

# ── Validate environment ────────────────────────────────────────────────

if [[ ! " ${VALID_ENVS[*]} " =~ " ${ENV} " ]]; then
  die "Invalid environment '${ENV}'. Must be one of: ${VALID_ENVS[*]}"
fi

ENV_CONFIG="${PROJECT_ROOT}/config/environments/${ENV}.yaml"
if [[ ! -f "$ENV_CONFIG" ]]; then
  die "Environment config not found: ${ENV_CONFIG}"
fi

log "Deploying environment: ${ENV}"
log "Config file: ${ENV_CONFIG}"

# ── Check AWS credentials ──────────────────────────────────────────────

if ! aws sts get-caller-identity &>/dev/null; then
  die "AWS credentials not configured. Run 'aws configure' or set AWS_PROFILE."
fi

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
log "AWS Account: ${AWS_ACCOUNT}"

# ── Load environment-specific settings ──────────────────────────────────

# Use venv Python if available (has pyyaml installed)
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"

# Parse stack name and SSM parameter name from env config
STACK_NAME=$("$PYTHON" -c "
import yaml, sys
with open('${ENV_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('stack_name', 'gpu-scheduler-${ENV}'))
")

TABLE_NAME=$("$PYTHON" -c "
import yaml, sys
with open('${ENV_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('dynamodb_table', 'GpuProvisioningInstances-${ENV}'))
")

SSM_PARAM=$("$PYTHON" -c "
import yaml, sys
with open('${ENV_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('ssm_parameter', '/gpu-scheduler/${ENV}/regions'))
")

log "Stack: ${STACK_NAME} | Table: ${TABLE_NAME} | SSM: ${SSM_PARAM}"

# ── Step 1: Deploy DynamoDB table ───────────────────────────────────────

log "Deploying DynamoDB table via CloudFormation..."
aws cloudformation deploy \
  --template-file "${PROJECT_ROOT}/infra/dynamodb.yaml" \
  --stack-name "${STACK_NAME}" \
  --parameter-overrides \
    TableName="${TABLE_NAME}" \
    Environment="${ENV}" \
  --no-fail-on-empty-changeset \
  --tags Project=gpu-cross-region-scheduler Environment="${ENV}"

log "DynamoDB table deployed: ${TABLE_NAME}"

# ── Step 2: Upload region config to SSM ─────────────────────────────────

REGIONS_CONFIG="${PROJECT_ROOT}/config/regions.yaml"
if [[ -f "$REGIONS_CONFIG" ]]; then
  log "Uploading region config to SSM: ${SSM_PARAM}"
  REGIONS_JSON=$("$PYTHON" -c "
import yaml, json, sys
with open('${REGIONS_CONFIG}') as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
")
  aws ssm put-parameter \
    --name "${SSM_PARAM}" \
    --type String \
    --value "${REGIONS_JSON}" \
    --overwrite
  log "Region config uploaded to SSM."
else
  log "WARN: ${REGIONS_CONFIG} not found, skipping SSM upload."
fi

# ── Step 2b: Upload AUTH_SECRET_KEY to SSM (SecureString) ───────────────

AUTH_SECRET=$("$PYTHON" -c "
import yaml
with open('${ENV_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('auth_secret_key', ''))
")

if [[ -n "$AUTH_SECRET" ]]; then
  AUTH_SSM_PARAM="/gpu-scheduler/${ENV}/auth-secret-key"
  log "Uploading AUTH_SECRET_KEY to SSM: ${AUTH_SSM_PARAM}"
  aws ssm put-parameter \
    --name "${AUTH_SSM_PARAM}" \
    --type SecureString \
    --value "${AUTH_SECRET}" \
    --overwrite
  log "AUTH_SECRET_KEY uploaded to SSM."
else
  log "WARN: auth_secret_key not found in ${ENV_CONFIG}, skipping."
fi

# ── Step 3: Install Python dependencies ─────────────────────────────────

log "Installing Python dependencies..."
cd "$PROJECT_ROOT"
pip install -e ".[dev]" --quiet

# ── Step 4: Smoke test ──────────────────────────────────────────────────

if [[ "$ENV" != "prod" ]]; then
  log "Running smoke tests (excluding slow PBT and e2e tests)..."
  python -m pytest tests/ -x -q --tb=short \
    --ignore-glob="tests/test_pbt_*" \
    --ignore=tests/test_e2e.py \
    --ignore=tests/test_build_agent.py \
    --ignore=tests/test_entrypoint.py \
    2>&1 | tail -5 || {
    err "Smoke tests failed. Deployment may be incomplete."
    exit 1
  }
  log "Smoke tests passed."
else
  log "Skipping tests in prod (run manually with: python -m pytest tests/ -v)"
fi

# ── Done ────────────────────────────────────────────────────────────────

log "Deployment complete for environment: ${ENV}"
log ""
log "Next steps:"
log "  - Verify DynamoDB table: aws dynamodb describe-table --table-name ${TABLE_NAME}"
log "  - Verify SSM parameter: aws ssm get-parameter --name ${SSM_PARAM}"
log "  - Run the scheduler:    python -m src.agent.main"
