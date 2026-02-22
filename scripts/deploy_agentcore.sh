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

PYTHON="${PROJECT_ROOT}/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"

read_config() {
  "$PYTHON" -c "
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

# ── Step 1: Verify AgentCore config ─────────────────────────────────────

AGENTCORE_CONFIG="${PROJECT_ROOT}/.bedrock_agentcore.yaml"
if [[ ! -f "$AGENTCORE_CONFIG" ]]; then
  die ".bedrock_agentcore.yaml not found. Run 'agentcore configure' interactively first."
fi
log "AgentCore config found: ${AGENTCORE_CONFIG}"

# ── Step 2: Ensure IAM role has Memory permissions ──────────────────────

MEMORY_ID=$(read_config "memory_id" "gpu_scheduler_memory-1az3i38LW2")
MEMORY_REGION=$(read_config "memory_region" "us-west-2")

EXECUTION_ROLE_ARN=$("$PYTHON" -c "
import yaml
with open('${AGENTCORE_CONFIG}') as f:
    cfg = yaml.safe_load(f)
agent_cfg = cfg.get('agents', {}).get('agent_entrypoint', {}).get('aws', {})
print(agent_cfg.get('execution_role', ''))
" 2>/dev/null || echo "")

if [[ -n "$EXECUTION_ROLE_ARN" ]]; then
  ROLE_NAME=$(echo "$EXECUTION_ROLE_ARN" | sed 's|.*/||')
  log "Ensuring Memory permissions on role: ${ROLE_NAME}"

  MEMORY_ARN="arn:aws:bedrock-agentcore:${BEDROCK_REGION}:${AWS_ACCOUNT}:memory/${MEMORY_ID}"

  MEMORY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockAgentCoreMemoryAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateMemoryRecord",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:DeleteMemoryRecord",
        "bedrock-agentcore:UpdateMemoryRecord"
      ],
      "Resource": "${MEMORY_ARN}"
    }
  ]
}
EOF
)

  aws iam put-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name "BedrockAgentCoreMemoryAccess" \
    --policy-document "${MEMORY_POLICY}" \
    2>/dev/null \
    && log "Memory IAM policy attached to role: ${ROLE_NAME}" \
    || log "WARN: Could not attach Memory policy (may already exist or insufficient IAM permissions)"
else
  log "WARN: Could not determine execution role from .bedrock_agentcore.yaml, skipping Memory IAM policy"
fi

# ── Step 3: Patch dockerignore template to exclude project-specific dirs ─

# AgentCore CLI uses a built-in dockerignore.template to decide which files
# go into the deployment package.  It does NOT read .gitignore or .dockerignore.
# We temporarily append project-specific exclusions so that large directories
# like 01-network/ (Terraform, ~683 MB) are not packaged.

DOCKERIGNORE_TEMPLATE=$("$PYTHON" -c "
from importlib.resources import files
print(files('bedrock_agentcore_starter_toolkit').joinpath('utils/runtime/templates/dockerignore.template'))
" 2>/dev/null || echo "")

DOCKERIGNORE_PATCHED=false
DOCKERIGNORE_BACKUP=""

if [[ -n "$DOCKERIGNORE_TEMPLATE" && -f "$DOCKERIGNORE_TEMPLATE" ]]; then
  DOCKERIGNORE_BACKUP="${DOCKERIGNORE_TEMPLATE}.bak"
  cp "$DOCKERIGNORE_TEMPLATE" "$DOCKERIGNORE_BACKUP"

  cat >> "$DOCKERIGNORE_TEMPLATE" <<'EXTRA'

# --- gpu-scheduler project-specific exclusions (auto-appended by deploy script) ---
01-network/
infra/
scripts/
.hypothesis/
.kiro/
*.tfstate
*.tfstate.backup
EXTRA

  DOCKERIGNORE_PATCHED=true
  log "Patched dockerignore.template with project exclusions"
fi

# ── Step 4: Clear cached deployment package ─────────────────────────────

CACHE_DIR="${PROJECT_ROOT}/.bedrock_agentcore/agent_entrypoint"
if [[ -d "$CACHE_DIR" ]]; then
  log "Clearing cached deployment package to force rebuild..."
  rm -rf "$CACHE_DIR"
  log "Cache cleared: ${CACHE_DIR}"
fi

# ── Step 5: Launch on AgentCore ─────────────────────────────────────────

log "Launching agent on AgentCore Runtime..."

log "Memory — ID: ${MEMORY_ID} | Region: ${MEMORY_REGION}"

# ── Load AUTH_SECRET_KEY from SSM Parameter Store ───────────────────────

AUTH_SSM_PARAM="/gpu-scheduler/${ENV}/auth-secret-key"
log "Loading AUTH_SECRET_KEY from SSM: ${AUTH_SSM_PARAM}"
AUTH_SECRET_KEY=$(aws ssm get-parameter \
  --name "${AUTH_SSM_PARAM}" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text 2>/dev/null) \
  || die "Failed to load AUTH_SECRET_KEY from SSM parameter: ${AUTH_SSM_PARAM}. Create it with: aws ssm put-parameter --name '${AUTH_SSM_PARAM}' --type SecureString --value '<your-secret>'"

agentcore launch \
  --env SCHEDULER_ENV="${ENV}" \
  --env SSM_PARAMETER="${SSM_PARAM}" \
  --env DYNAMODB_TABLE="${TABLE_NAME}" \
  --env BEDROCK_MODEL_ID="${BEDROCK_MODEL}" \
  --env BEDROCK_REGION="${BEDROCK_REGION}" \
  --env MEMORY_ID="${MEMORY_ID}" \
  --env MEMORY_REGION="${MEMORY_REGION}" \
  --env AUTH_SECRET_KEY="${AUTH_SECRET_KEY}"
LAUNCH_EXIT=$?

# ── Step 6: Restore dockerignore template ───────────────────────────────

if [[ "$DOCKERIGNORE_PATCHED" == "true" && -n "$DOCKERIGNORE_BACKUP" && -f "$DOCKERIGNORE_BACKUP" ]]; then
  mv "$DOCKERIGNORE_BACKUP" "$DOCKERIGNORE_TEMPLATE"
  log "Restored original dockerignore.template"
fi

if [[ $LAUNCH_EXIT -ne 0 ]]; then
  die "agentcore launch failed (exit code: $LAUNCH_EXIT)"
fi

log "AgentCore deployment complete for environment: ${ENV}"
log ""
log "Verify with:"
log "  agentcore status"
log "  agentcore logs"
