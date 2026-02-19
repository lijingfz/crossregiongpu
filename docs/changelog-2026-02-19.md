# 变更记录 — 2026-02-19

本次变更修复了 6 个生产/开发环境问题：Agent 循环启动实例、Web Dashboard 登录失败、DynamoDB 写入失败、AgentCore Memory 权限缺失、部署脚本变量作用域错误，以及 fastapi 依赖缺失。

---

## 1. Agent 循环启动实例（LaunchGuardHook）

### 问题

用户请求"在新加坡区域启动 3 台 G6.xlarge 实例"时，Agent 会循环调用 `ec2_launch_instances` 约 18 次，每次启动 3 台，最终启动远超预期数量的实例。

### 根因

Strands Agent SDK 没有内置 `max_turns` 限制。事件循环（`event_loop_cycle` → `recurse_event_loop`）会无限递归，直到 LLM 返回 `end_turn`。当 LLM 未能识别任务已完成（`status=FULL`），就会反复调用 launch 工具。

### 方案

新增 `LaunchGuardHook`（`HookProvider`），通过 Strands 的 `BeforeToolCallEvent` / `AfterToolCallEvent` 机制实现双重防护：

1. 目标达成阻断：累计启动数 `_total_launched >= _target_count` 时，通过 `cancel_tool` 阻止后续调用
2. 硬性上限阻断：调用次数 `_launch_call_count >= max_launch_calls`（默认 8）时强制停止

同时在 `ec2_launch_instances` 工具层面增加了 `MAX_TARGET_COUNT = 20` 的硬上限防护。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `src/agent/launch_guard.py` | 新增 `LaunchGuardHook` 类，跟踪累计启动数和调用次数 |
| `src/agent/main.py` | `build_agent()` 中创建 `LaunchGuardHook` 实例并注入 Agent hooks 列表；CLI `main()` 循环中每次新 prompt 前调用 `reset()` |
| `agent_entrypoint.py` | AgentCore 入口每次新 prompt 前调用 `_reset_launch_guard()` 重置计数器 |
| `src/tools/launch.py` | 新增 `MAX_TARGET_COUNT = 20` 硬上限和 `target_count <= 0` 校验 |
| `src/agent/prompts.py` | System Prompt 新增 "target_count Integrity (CRITICAL)" 章节 |
| `tests/test_launch_guard.py` | 18 个单元测试覆盖阻断、跟踪、重置和边界情况 |
| `tests/test_tools_launch.py` | 4 个新测试覆盖 target_count 校验 |

### 配置

`max_launch_calls` 可通过环境配置文件调整：

```yaml
# config/environments/dev.yaml
max_launch_calls: 8  # 默认值，可按需调整
```

---

## 2. Web Dashboard 登录失败（AUTH_SECRET_KEY）

### 问题

使用 `admin/admin123` 登录 Web Dashboard 时失败，返回"登录失败"。

### 根因

JWT 签发和验证使用了不同的 secret key 来源：

- `web_dashboard/routers/auth.py` 的 `_create_token()` 用 `os.environ.get("AUTH_SECRET_KEY", "")` — 空字符串签名
- `src/agent/auth.py` 的 `validate_token()` 用 `os.environ.get("AUTH_SECRET_KEY")` — 拿到 `None`，直接抛出 `AUTH_SECRET_KEY not configured`

本地开发环境未设置 `AUTH_SECRET_KEY` 环境变量，导致签发和验证不一致。

### 方案

三个涉及 JWT 的位置统一增加 fallback 逻辑：当环境变量未设置时，自动从 `config/environments/dev.yaml` 的 `auth_secret_key` 字段读取。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `web_dashboard/routers/auth.py` | `_create_token()` 新增 `_get_secret_key()` 函数，支持从 dev.yaml fallback |
| `src/agent/auth.py` | `validate_token()` 在环境变量为空时从 dev.yaml 读取 fallback |
| `web_dashboard/routers/chat.py` | `_make_agent_token()` 同样增加 dev.yaml fallback |

---

## 3. DynamoDB 写入失败（Region 默认值错误）

### 问题

Agent 调用 `dynamodb_put_instances` 写入实例记录时失败，日志显示"DynamoDB 表访问出现问题"。

### 根因

`dynamodb_put_instances` 的 `dynamodb_region` 参数默认值为 `us-east-1`，但 DynamoDB 表 `GpuProvisioningInstances-dev` 实际部署在 `us-west-2`。当 Agent（LLM）调用时未显式传递 `dynamodb_region` 参数，就会去 `us-east-1` 查找表，触发 `ResourceNotFoundException`。

### 方案

将 `dynamodb_put_instances` 的默认 region 从 `us-east-1` 改为 `us-west-2`，与 `dynamodb_query_instances` 保持一致。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `src/tools/dynamodb.py` | `dynamodb_region` 默认值从 `us-east-1` 改为 `us-west-2` |
| `tests/test_tools_dynamodb.py` | 测试显式传递 `dynamodb_region="us-east-1"` 以匹配 moto mock 的 region |

---

## 4. AgentCore Memory 权限缺失

### 问题

AgentCore Runtime 日志报错：`AccessDeniedException: User is not authorized to perform bedrock-agentcore:RetrieveMemoryRecords`。

### 根因

AgentCore 执行角色 `AmazonBedrockAgentCoreSDKRuntime-us-west-2-f9b98d41bd` 缺少 `bedrock-agentcore:*MemoryRecord*` 相关 IAM 权限。部署脚本中没有管理 IAM 权限的步骤。

### 方案

在 `deploy_agentcore.sh` 中新增 Step 2，自动从 `.bedrock_agentcore.yaml` 读取执行角色名称，通过 `aws iam put-role-policy` 附加 Memory 访问策略。确保新账号部署时自动处理。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `scripts/deploy_agentcore.sh` | 新增 Step 2：自动为执行角色附加 `BedrockAgentCoreMemoryAccess` inline policy；`MEMORY_ID` 读取提前到 Step 2 之前；`PYTHON` 变量提升到全局作用域 |

### 附加的 IAM 策略

```json
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
      "Resource": "arn:aws:bedrock-agentcore:<region>:<account>:memory/<memory-id>"
    }
  ]
}
```

---

## 5. 部署脚本 PYTHON 变量未定义

### 问题

运行 `./scripts/deploy_agentcore.sh dev` 报错：`PYTHON: unbound variable`。

### 根因

脚本使用 `set -u`（nounset），`PYTHON` 变量原本在 `read_config()` 函数内部赋值。新增的 Step 2 在函数外部引用了 `$PYTHON`，但此时尚未调用过 `read_config()`，导致变量未定义。

### 方案

将 `PYTHON` 变量定义从 `read_config()` 函数内部提升到全局作用域。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `scripts/deploy_agentcore.sh` | `PYTHON` 赋值移到 `read_config()` 函数定义之前 |

---

## 6. Web Dashboard 缺少 fastapi 依赖

### 问题

运行 `uvicorn web_dashboard.app:app` 报错：`ModuleNotFoundError: No module named 'fastapi'`。

### 根因

重建 `.venv` 后只安装了 `pyproject.toml` 中的依赖，`fastapi` 未列在项目依赖中。

### 方案

手动安装 `pip install fastapi`。`fastapi` 是 Web Dashboard 的运行时依赖，建议后续将其加入 `pyproject.toml` 的可选依赖组。

---

## 测试结果

所有修改完成后，测试套件通过情况：

- `test_launch_guard.py`: 18 passed
- `test_tools_dynamodb.py`: 2 passed
- `test_tools_launch.py`: 全部通过（含 4 个新增测试）
- 非 PBT 测试总计: 84 passed, 0 failed
- `test_auth.py`: 3 passed（验证 JWT fallback 未破坏现有逻辑）
