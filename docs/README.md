# GPU Cross-Region Dynamic Scheduler

跨 Region GPU 动态调度系统，基于 Strands Agent SDK + Bedrock Claude 构建。

系统解决 AWS GPU 实例（G5/G6/G6e）启动时 `InsufficientInstanceCapacity` 容量不足问题，通过 Probe-and-Fill 策略在多个候选 Region 间按就近优先级逐个尝试启动，直到满足需求或候选耗尽。

## 目录

- [本地开发](./development.md) — 环境搭建、运行测试
- [AgentCore 部署指南](./agentcore-deployment.md) — 详细部署文档
- [运维手册](./operations.md) — 日常运维、监控、故障排查
- [审批机制](./approval-flow.md) — Human-in-the-loop 审批说明

## 架构概览

```
用户输入 → Controller Agent (Strands/Bedrock Claude)
              ↓
         Orchestrator (状态机循环)
              ↓
         Tools (EC2 Launch / Describe / Delete / DynamoDB / Offerings)
              ↓
         Agent State (plan / remaining / cursor / results)
```

核心策略 Probe-and-Fill：
1. 分批启动（chunked launch，默认 batch_max=4）
2. 二分退让（InsufficientCapacity 时 batch 减半）
3. 多 AZ/Subnet 轮转
4. 跨 Region 逐级回退

## 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试
python -m pytest tests/ -v

# 部署到 AgentCore（dev 环境）
./scripts/deploy_agentcore.sh dev
```

---

## AgentCore 部署与测试

### 前置条件

| 工具 | 安装方式 |
|------|---------|
| Python 3.11+ | https://www.python.org/downloads/ |
| AWS CLI 2.x | `brew install awscli` |
| AgentCore CLI | `pip install bedrock-agentcore-starter-toolkit` |

> **注意**：AgentCore CLI 的包名是 `bedrock-agentcore-starter-toolkit`，不是 `bedrock-agentcore`（后者只是 SDK）。

### 首次部署

```bash
# 1. 配置 AgentCore（指定入口文件、区域、运行时）
agentcore configure \
  --entrypoint agent_entrypoint.py \
  --region us-west-2 \
  --runtime PYTHON_3_12 \
  --disable-memory \
  --non-interactive

# 2. 创建 Memory 资源（可选，用于保存对话历史）
agentcore memory create gpu_scheduler_memory \
  --region us-west-2 \
  --event-expiry-days 30 \
  --wait

# 3. 部署启动（带 Memory）
agentcore launch \
  --env SCHEDULER_ENV=dev \
  --env SSM_PARAMETER=/gpu-scheduler/dev/regions \
  --env DYNAMODB_TABLE=GpuProvisioningInstances-dev \
  --env BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0 \
  --env BEDROCK_REGION=us-west-2 \
  --env AUTH_SECRET_KEY=<your-jwt-secret> \
  --env MEMORY_ID=<memory-id> \
  --env MEMORY_REGION=us-west-2
```

> Memory 名称只能包含字母、数字和下划线，不能用连字符。
> 如果不需要 Memory，省略 `MEMORY_ID` 和 `MEMORY_REGION` 环境变量即可。

### 更新部署

代码修改后，使用 `--auto-update-on-conflict` 避免冲突：

```bash
agentcore launch --auto-update-on-conflict \
  --env SCHEDULER_ENV=dev \
  --env SSM_PARAMETER=/gpu-scheduler/dev/regions \
  --env DYNAMODB_TABLE=GpuProvisioningInstances-dev \
  --env BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0 \
  --env BEDROCK_REGION=us-west-2 \
  --env AUTH_SECRET_KEY=<your-jwt-secret> \
  --env MEMORY_ID=<memory-id> \
  --env MEMORY_REGION=us-west-2
```

### IAM 权限配置

AgentCore 自动创建执行角色（如 `AmazonBedrockAgentCoreSDKRuntime-us-west-2-xxxxxxxx`），需要手动添加业务权限：

```bash
aws iam put-role-policy \
  --role-name <AgentCore执行角色名> \
  --policy-name GpuSchedulerResourcesPolicy \
  --policy-document '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2Operations",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstanceTypeOfferings",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:GetItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:DescribeTable"
      ],
      "Resource": "arn:aws:dynamodb:*:<account-id>:table/GpuProvisioningInstances-*"
    },
    {
      "Sid": "SSM",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter"],
      "Resource": "arn:aws:ssm:*:<account-id>:parameter/gpu-scheduler/*"
    },
    {
      "Sid": "AgentCoreMemory",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:RetrieveMemories",
        "bedrock-agentcore:SearchMemories",
        "bedrock-agentcore:ListBranches",
        "bedrock-agentcore:ListBranchEvents"
      ],
      "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>"
    }
  ]
}'
```

### 生成测试 Token

```bash
python3 -c "
import jwt
token = jwt.encode(
    {'user_id': 'test_user', 'username': 'yourname', 'roles': ['admin']},
    '<your-jwt-secret>',
    algorithm='HS256'
)
print(token)
"
```

### 测试命令

**查询实例：**

```bash
agentcore invoke '{"prompt": "查询当前所有运行中的GPU实例", "token": "<token>"}'
```

**启动实例：**

```bash
agentcore invoke '{"prompt": "在东京区域启动2台g6.xlarge GPU实例", "token": "<token>"}'
```

**删除实例（两步审批流程）：**

```bash
# 第一步：发起删除请求，返回 approval_required + interrupt_id
agentcore invoke '{"prompt": "请删除所有当前正在运行的GPU实例", "token": "<token>"}'

# 第二步：用返回的 interrupt_id 确认删除
agentcore invoke '{"approval_responses":[{"interrupt_id":"<返回的interrupt_id>","decision":"approved"}],"token":"<token>"}'
```

**查看日志：**

```bash
# 实时日志
aws logs tail /aws/bedrock-agentcore/runtimes/<agent-name>-DEFAULT \
  --log-stream-name-prefix "$(date +%Y/%m/%d)/[runtime-logs" --follow

# 最近1小时日志
aws logs tail /aws/bedrock-agentcore/runtimes/<agent-name>-DEFAULT \
  --log-stream-name-prefix "$(date +%Y/%m/%d)/[runtime-logs" --since 1h
```

---

## AgentCore Memory（对话历史）

### 创建 Memory 资源

```bash
# 名称只能包含字母、数字、下划线，不能用连字符
agentcore memory create gpu_scheduler_memory \
  --region us-west-2 \
  --event-expiry-days 30 \
  --wait
```

创建完成后返回 `memory_id`（如 `gpu_scheduler_memory-1az3i38LW2`）。

### 管理命令

```bash
# 查看 Memory 状态
agentcore memory get <memory-id> --region us-west-2

# 列出所有 Memory 资源
agentcore memory list

# 删除 Memory（慎用，不可恢复）
agentcore memory delete <memory-id> --wait
```

### 查询对话记录

使用 `scripts/query_memory.py` 脚本查询：

```bash
# 列出账号下所有 Memory 资源
python scripts/query_memory.py list

# 查询指定 session 的对话记录（user 和 session 都是必填的）
python scripts/query_memory.py events --user test_user --session <session_id>

# 限制返回数量
python scripts/query_memory.py events --user test_user --session <session_id> --limit 10
```

> `session_id` 就是每次 `agentcore invoke` 返回的 Session 值。
> `list_events` API 要求 `actor_id` 和 `session_id` 都是必填参数。

---

## 部署踩坑记录

以下是从首次部署到功能完全可用过程中遇到的问题及解决方案：

### 1. AgentCore CLI 包名混淆

**问题**：`pip install bedrock-agentcore` 安装的是 SDK，不包含 `agentcore` CLI 命令。

**解决**：正确的包名是 `bedrock-agentcore-starter-toolkit`：
```bash
pip install bedrock-agentcore-starter-toolkit
```

### 2. requirements.txt 中不必要的依赖

**问题**：`strands-agents-tools` 包引入了大量不需要的依赖（sympy、pillow 等），导致部署包过大且可能引发兼容性问题。

**解决**：从 `requirements.txt` 中移除 `strands-agents-tools`。项目源码中并未直接使用该包的工具，所有工具都是自定义的 `@tool` 函数。

### 3. DynamoDB 跨区域权限不足

**问题**：DynamoDB 表部署在 `ap-northeast-1`，但 IAM policy 的 Resource 只限定了 `us-west-2` 区域的表，导致跨区域访问被拒绝。

**解决**：将 DynamoDB 权限的 Resource 改为 `arn:aws:dynamodb:*:<account-id>:table/GpuProvisioningInstances-*`，使用 `*` 通配所有区域。

### 4. DynamoDB UpdateItem 权限缺失

**问题**：删除实例后，更新 DynamoDB 记录状态为 `terminated` 时失败。IAM policy 中有 `PutItem`/`Query`/`Scan` 但缺少 `UpdateItem`。

**解决**：在 IAM policy 的 DynamoDB Statement 中添加 `dynamodb:UpdateItem` 和 `dynamodb:DeleteItem`。

### 5. 删除审批流程跨请求中断状态丢失

**问题**：删除操作的两步审批流程中，第一步返回 `approval_required`，第二步发送 `approval_responses` 时，Agent 返回"删除操作已取消"而非执行删除。

**根因**：Strands SDK 的 `_InterruptState` 在 AgentCore 的两次 HTTP 请求之间可能丢失 `activated=True` 状态。`resume()` 方法检查 `self.activated`，如果为 `False` 则直接跳过，导致审批响应无法传递到工具。

**解决**：在 `agent_entrypoint.py` 中实现中断状态的保存/恢复机制：
- 返回 `approval_required` 时，将 `agent._interrupt_state.to_dict()` 序列化保存到 `_session_interrupt_cache`
- 收到 `approval_responses` 时，检查 agent 的 `_interrupt_state.activated`，如果为 `False`，从缓存中用 `_InterruptState.from_dict()` 恢复
- 如果状态仍然存活（`activated=True`），跳过恢复，走正常 resume 流程

### 6. approval_responses 中 decision 到工具响应的映射

**问题**：delete tool 检查 `str(approval).strip().lower() not in ("y", "yes")`，但 API 传入的是 `decision: "approved"`。

**解决**：在 entrypoint 中将 `decision=="approved"` 映射为 `"y"`，`decision!="approved"` 映射为 `"n"`。

### 7. AgentCore Memory 名称不能包含连字符

**问题**：`agentcore memory create gpu-scheduler-memory` 报 `ValidationException`，名称不符合正则 `[a-zA-Z][a-zA-Z0-9_]{0,47}`。

**解决**：Memory 名称只能用字母、数字和下划线，改为 `gpu_scheduler_memory`：
```bash
agentcore memory create gpu_scheduler_memory --region us-west-2 --event-expiry-days 30 --wait
```

### 8. AgentCore Memory IAM 权限 action 名称错误

**问题**：启用 Memory 后，对话记录未保存。CloudWatch 日志显示 `AccessDeniedException: bedrock-agentcore:CreateEvent`。

**根因**：IAM policy 中使用了错误的 action 名称（如 `CreateMemoryEvent`），实际 API 使用的是 `CreateEvent`、`ListEvents` 等不带 `Memory` 前缀的名称。

**解决**：IAM policy 中 Memory 相关的 action 应为：
```json
{
  "Sid": "AgentCoreMemory",
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:GetMemory",
    "bedrock-agentcore:RetrieveMemories",
    "bedrock-agentcore:SearchMemories",
    "bedrock-agentcore:ListBranches",
    "bedrock-agentcore:ListBranchEvents"
  ],
  "Resource": "arn:aws:bedrock-agentcore:<region>:<account-id>:memory/<memory-id>"
}
```

**排查方法**：查看 CloudWatch 日志中的 `AccessDeniedException` 错误，日志会明确指出缺少哪个 action：
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/<agent-name>-DEFAULT \
  --log-stream-name-prefix "$(date +%Y/%m/%d)/[runtime-logs" --since 10m \
  --format short | grep -i "denied\|error\|failed"
```

### 9. Memory 的 list_events API 要求 actor_id 和 session_id 必填

**问题**：调用 `MemoryClient.list_events()` 时不传 `actor_id` 或 `session_id` 会报 `TypeError`。

**解决**：这两个参数都是必填的，无法只按 user 或只按 session 查询。查询时需要同时提供：
```bash
python scripts/query_memory.py events --user test_user --session <session_id>
```
`session_id` 来自 `agentcore invoke` 返回结果中的 `session_id` 字段。
