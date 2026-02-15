#!/usr/bin/env bash
# deploy_agentcore.sh — Deploy GPU Scheduler Agent to AgentCore Runtime
#
# Usage:
#   ./scripts/deploy_agentcore.sh <env> [--account <aws-account-id>] [--region <aws-region>]
#
# ENV: dev | staging | prod  (required)
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - AgentCore CLI installed (pip install bedrock-agentcore)
#   - Python 3.11+
#   - requirements.txt present at project root
#
# Requirements: 8.3, 8.4

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VALID_ENVS=("dev" "staging" "prod")
ENV=""
AWS_ACCOUNT_OVERRIDE=""
AWS_REGION_OVERRIDE=""

# ── Argument parsing ────────────────────────────────────────────────────

usage() {
  echo "Usage: $0 <env> [--account <aws-account-id>] [--region <aws-region>]"
  echo "  env: dev | staging | prod"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)
      AWS_ACCOUNT_OVERRIDE="$2"; shift 2 ;;
    --region)
      AWS_REGION_OVERRIDE="$2"; shift 2 ;;
    --help|-h)
      usage ;;
    *)
      if [[ -z "$ENV" ]]; then
        ENV="$1"; shift
      else
        echo "ERROR: Unexpected argument '$1'"; usage
      fi ;;
  esac
done

[[ -z "$ENV" ]] && { echo "ERROR: Environment is required."; usage; }

# ── Helpers ──────────────────────────────────────────────────────────────

log()  { echo "[agentcore-deploy] $*"; }
err()  { echo "[agentcore-deploy] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

# ── Validate environment ────────────────────────────────────────────────

if [[ ! " ${VALID_ENVS[*]} " =~ " ${ENV} " ]]; then
  die "Invalid environment '${ENV}'. Must be one of: ${VALID_ENVS[*]}"
fi

ENV_CONFIG="${PROJECT_ROOT}/config/environments/${ENV}.yaml"
[[ -f "$ENV_CONFIG" ]] || die "Environment config not found: ${ENV_CONFIG}"

log "Deploying to AgentCore — environment: ${ENV}"

# ── Pre-flight checks ──────────────────────────────────────────────────

# AWS CLI
if ! command -v aws &>/dev/null; then
  die "AWS CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
fi

if ! aws sts get-caller-identity &>/dev/null; then
  die "AWS credentials not configured. Run 'aws configure' or set AWS_PROFILE."
fi

# AgentCore CLI
if ! command -v agentcore &>/dev/null; then
  die "AgentCore CLI not found. Install: pip install bedrock-agentcore"
fi

# Entrypoint file
ENTRYPOINT="${PROJECT_ROOT}/agent_entrypoint.py"
[[ -f "$ENTRYPOINT" ]] || die "Entrypoint not found: ${ENTRYPOINT}"

# requirements.txt
REQUIREMENTS="${PROJECT_ROOT}/requirements.txt"
[[ -f "$REQUIREMENTS" ]] || die "requirements.txt not found: ${REQUIREMENTS}"

AWS_ACCOUNT="${AWS_ACCOUNT_OVERRIDE:-$(aws sts get-caller-identity --query Account --output text)}"
log "AWS Account: ${AWS_ACCOUNT}"

# ── Load environment config ─────────────────────────────────────────────

read_config() {
  python3 -c "
import yaml, sys
with open('${ENV_CONFIG}') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('$1', '$2'))
"
}

TABLE_NAME=$(read_config "dynamodb_table" "GpuProvisioningInstances-${ENV}")
SSM_PARAM=$(read_config "ssm_parameter" "/gpu-scheduler/${ENV}/regions")
BEDROCK_MODEL=$(read_config "bedrock_model_id" "us.anthropic.claude-sonnet-4-20250514-v1:0")
BEDROCK_REGION="${AWS_REGION_OVERRIDE:-$(read_config "bedrock_region" "us-west-2")}"

log "Config — Table: ${TABLE_NAME} | SSM: ${SSM_PARAM} | Model: ${BEDROCK_MODEL} | Region: ${BEDROCK_REGION}"

# ── Step 1: Configure AgentCore ─────────────────────────────────────────

log "Running agentcore configure..."
cd "$PROJECT_ROOT"
agentcore configure --entrypoint agent_entrypoint.py

# ── Step 2: Launch on AgentCore ─────────────────────────────────────────

log "Launching agent on AgentCore Runtime..."
agentcore launch \
  --env SCHEDULER_ENV="${ENV}" \
  --env SSM_PARAMETER="${SSM_PARAM}" \
  --env DYNAMODB_TABLE="${TABLE_NAME}" \
  --env BEDROCK_MODEL_ID="${BEDROCK_MODEL}" \
  --env BEDROCK_REGION="${BEDROCK_REGION}"

log "AgentCore deployment complete for environment: ${ENV}"
log ""
log "Verify with:"
log "  agentcore status"
log "  agentcore logs"
