# AgentCore Runtime 部署指南

> GPU Cross-Region Dynamic Scheduler — AgentCore Runtime 部署文档
>
> Requirements: 8.1, 8.2, 8.5, 8.6

## 前置条件

### 工具

| 工具 | 最低版本 | 安装方式 |
|------|---------|---------|
| Python | 3.11+ | https://www.python.org/downloads/ |
| AWS CLI | 2.x | `brew install awscli` 或 [官方文档](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) |
| AgentCore CLI | latest | `pip install bedrock-agentcore` |
| Terraform | 1.0+ | 仅网络层部署需要 |

### AWS 权限

部署账号的 IAM 用户/角色需要以下权限：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentCoreDeployment",
      "Effect": "Allow",
      "Action": [
        "bedrock:*",
        "iam:PassRole",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SchedulerResources",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstanceTypeOfferings",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "ssm:GetParameter",
        "ssm:PutParameter",
        "cloudformation:*"
      ],
      "Resource": "*"
    }
  ]
}
```

> 生产环境建议将 `Resource` 限定到具体 ARN。


## 环境准备

### 1. 创建 DynamoDB 表

每个环境需要独立的 DynamoDB 表：

```bash
# 使用现有的基础设施部署脚本
./scripts/deploy.sh <env>
```

或手动通过 CloudFormation：

```bash
aws cloudformation deploy \
  --template-file infra/dynamodb.yaml \
  --stack-name gpu-scheduler-<env> \
  --parameter-overrides TableName=GpuProvisioningInstances-<env> Environment=<env>
```

### 2. 创建 SSM 参数

将区域配置上传到 SSM Parameter Store：

```bash
# 将 regions.yaml 转为 JSON 并上传
python3 -c "
import yaml, json
with open('config/regions.yaml') as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
" | aws ssm put-parameter \
    --name /gpu-scheduler/<env>/regions \
    --type String \
    --value file:///dev/stdin \
    --overwrite
```

### 3. 配置认证（可选）

如果启用 token 认证，需要设置认证服务端点和密钥：

```bash
# 认证服务端点
export AUTH_ENDPOINT=https://auth.example.com/verify
# JWT 验证密钥（用于离线验证）
export AUTH_SECRET_KEY=<your-secret-key>
```

## 分环境部署

### 环境配置文件

每个环境的配置位于 `config/environments/<env>.yaml`：

| 环境 | 配置文件 | 审批阈值 | 地理限制 | 日志级别 |
|------|---------|---------|---------|---------|
| dev | `dev.yaml` | 50 | 无 | DEBUG |
| staging | `staging.yaml` | 20 | ap-south-1, ap-northeast-1/2 | INFO |
| prod | `prod.yaml` | 10 | ap-south-1, ap-northeast-1/2 | WARNING |

### 环境变量

AgentCore Runtime 通过 `--env` 注入以下环境变量：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SCHEDULER_ENV` | 环境名称 | `dev` |
| `SSM_PARAMETER` | SSM 参数路径 | `/gpu-scheduler/{env}/regions` |
| `DYNAMODB_TABLE` | DynamoDB 表名 | `GpuProvisioningInstances-{env}` |
| `BEDROCK_MODEL_ID` | Bedrock 模型 ID | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| `BEDROCK_REGION` | Bedrock 区域 | `us-west-2` |
| `AUTH_ENDPOINT` | 认证服务端点 | — |
| `AUTH_SECRET_KEY` | JWT 验证密钥 | — |
| `MEMORY_ID` | AgentCore Memory 资源 ID | `gpu_scheduler_memory-1az3i38LW2` |
| `MEMORY_REGION` | Memory 所在区域 | `us-west-2` |

### 使用部署脚本

```bash
# Dev 环境
./scripts/deploy_agentcore.sh dev

# Staging 环境
./scripts/deploy_agentcore.sh staging

# Prod 环境（指定账号和区域）
./scripts/deploy_agentcore.sh prod --account 123456789012 --region us-west-2
```

### 手动部署

```bash
# 1. 配置 AgentCore
agentcore configure --entrypoint agent_entrypoint.py

# 2. 启动（以 prod 为例）
agentcore launch \
  --env SCHEDULER_ENV=prod \
  --env SSM_PARAMETER=/gpu-scheduler/prod/regions \
  --env DYNAMODB_TABLE=GpuProvisioningInstances-prod \
  --env BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0 \
  --env BEDROCK_REGION=us-west-2 \
  --env AUTH_ENDPOINT=https://auth.example.com/verify \
  --env AUTH_SECRET_KEY=<secret> \
  --env MEMORY_ID=gpu_scheduler_memory-1az3i38LW2 \
  --env MEMORY_REGION=us-west-2
```

## 验证方法

### 1. 检查部署状态

```bash
agentcore status
agentcore logs
```

### 2. 发送测试请求

```bash
# 正常消息
curl -X POST <agentcore-endpoint> \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "查询当前所有运行中的 GPU 实例",
    "token": "<valid-auth-token>"
  }'
```

预期响应：

```json
{
  "status": "completed",
  "session_id": "sess_xxx",
  "result": "...",
  "user_id": "user_001"
}
```

### 3. 验证基础设施

```bash
# DynamoDB 表
aws dynamodb describe-table --table-name GpuProvisioningInstances-<env>

# SSM 参数
aws ssm get-parameter --name /gpu-scheduler/<env>/regions
```

## 回滚步骤

### 1. 停止当前部署

```bash
agentcore stop
```

### 2. 回退到上一版本

```bash
# 切换到上一个稳定版本的代码
git checkout <previous-tag>

# 重新部署
./scripts/deploy_agentcore.sh <env>
```

### 3. 验证回滚

```bash
agentcore status
# 发送测试请求确认功能正常
```

## 首次部署（新 AWS 账号）

在全新的 AWS 账号中首次部署时，需要额外完成以下步骤：

### 1. 创建 IAM 角色

```bash
# 创建 AgentCore 执行角色
aws iam create-role \
  --role-name AgentCoreSchedulerRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "bedrock.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# 附加所需策略（参见前置条件中的权限列表）
aws iam put-role-policy \
  --role-name AgentCoreSchedulerRole \
  --policy-name SchedulerPolicy \
  --policy-document file://iam-policy.json
```

### 2. 部署网络基础设施

```bash
cd 01-network
terraform init
terraform apply -var="environment=<env>"
```

### 3. 部署 DynamoDB 和 SSM

```bash
./scripts/deploy.sh <env>
```

### 4. 部署 AgentCore

```bash
./scripts/deploy_agentcore.sh <env>
```

## 常见问题排查

### AgentCore CLI 未找到

```
ERROR: AgentCore CLI not found
```

解决：`pip install bedrock-agentcore`

### AWS 凭证无效

```
ERROR: AWS credentials not configured
```

解决：运行 `aws configure` 或设置 `AWS_PROFILE` 环境变量。

### Entrypoint 文件未找到

```
ERROR: Entrypoint not found
```

确认 `agent_entrypoint.py` 位于项目根目录。

### Agent 创建失败

检查环境变量是否正确设置：

```bash
agentcore logs | grep "Agent creation failed"
```

常见原因：
- SSM 参数路径不存在 → 先运行 `./scripts/deploy.sh <env>` 上传配置
- Bedrock 模型 ID 错误 → 确认模型在目标区域可用
- DynamoDB 表不存在 → 先部署 CloudFormation 栈

### 认证失败

```json
{"status": "unauthorized", "message": "Invalid or expired authentication token"}
```

检查：
- `AUTH_ENDPOINT` 和 `AUTH_SECRET_KEY` 环境变量是否正确
- Token 是否过期
- 认证服务是否可达
