# Human-in-the-loop 审批机制说明

## 概述

本文档描述 GPU 跨 Region 动态调度系统中的人工审批（Human-in-the-loop）机制的设计、触发时机、生效范围和扩展方式。修改审批逻辑前请先阅读本文档。

## 核心文件

| 文件 | 职责 |
|------|------|
| `src/agent/approval.py` | 审批 Hook 实现（`ApprovalHook` + `ApprovalConfig`） |
| `src/agent/main.py` | Agent 工厂函数，`hooks` 参数注入审批 Hook |
| `src/orchestrator/executor.py` | Orchestrator 确定性循环，当前不经过 Agent Hook |
| `tests/test_agent_approval.py` | 审批逻辑的单元测试 |

## 触发机制

### Strands BeforeToolCallEvent

审批基于 Strands SDK 的 Hook 机制实现。`ApprovalHook` 实现了 `HookProvider` 接口，在 `register_hooks` 中注册了一个 `BeforeToolCallEvent` 回调。

```
Agent 准备调用 @tool
       │
       ▼
Strands 触发 BeforeToolCallEvent
       │
       ▼
ApprovalHook._check_approval(event)
       │
       ├── 命中阈值 → event.cancel_tool = "原因描述"
       │                  → Strands 中止该工具调用，返回错误给 Agent
       │
       └── 未命中 → 不修改 event
                      → Strands 正常执行工具调用
```

### 触发粒度：每次工具调用

审批检查发生在 **每次 Agent 调用任何 @tool 之前**，不是按 Region 或按步骤。一次调度流程中 Agent 可能调用多次工具（`get_region_order`、`describe_instance_type_offerings`、`ec2_launch_instances` 等），每次都会经过审批检查。

实际上只有携带 `target_count` 或 `region` 参数的工具调用才可能命中阈值，主要是 `ec2_launch_instances`。

## 三种审批规则

### 1. 批量阈值（Requirement 9.2）

当工具入参中 `target_count` 超过 `batch_threshold` 时拦截。

```python
ApprovalConfig(batch_threshold=20)
# target_count=25 的调用会被拦截
# target_count=10 的调用正常通过
```

### 2. 地理边界（Requirement 9.3）

当工具入参中 `region` 不在 `allowed_geo_regions` 集合中时拦截。

```python
ApprovalConfig(allowed_geo_regions={"us-east-1", "us-west-2", "ap-northeast-1"})
# region="eu-west-1" 的调用会被拦截
# region="us-east-1" 的调用正常通过
# allowed_geo_regions 为空集时不做地理检查
```

### 3. 工具黑名单（始终审批）

指定的工具名无论参数如何都需要审批。

```python
ApprovalConfig(always_approve_tools={"ec2_launch_instances"})
# 所有 ec2_launch_instances 调用都会被拦截
```

### 优先级

检查按以下顺序执行，命中任一条件即拦截并返回，不继续检查后续条件：

1. `always_approve_tools`（工具名匹配）
2. `batch_threshold`（数量超限）
3. `allowed_geo_regions`（地理越界）

## 使用方式

```python
from src.agent.approval import ApprovalConfig, ApprovalHook
from src.agent.main import create_agent

config = ApprovalConfig(
    batch_threshold=20,
    allowed_geo_regions={"ap-south-1", "ap-northeast-1", "us-east-1"},
)
hook = ApprovalHook(config=config)

agent = create_agent(hooks=[hook])
```

## 当前限制与注意事项

### Orchestrator 路径不经过 Hook

当前系统有两条工具调用路径：

```
路径 A（Agent 自主调用）：
  Agent → Strands dispatch → BeforeToolCallEvent → ApprovalHook → @tool
  ✅ 审批生效

路径 B（Orchestrator 确定性循环）：
  Orchestrator → ToolCallbacks.launch_instances → 直接调用
  ❌ 审批不生效
```

`executor.py` 中的 `Orchestrator` 通过 `ToolCallbacks` 直接调用工具函数，绕过了 Strands 的 Hook 机制。这意味着：

- 如果调度流程完全由 Orchestrator 驱动（当前实现），审批 Hook 不会被触发
- 如果调度流程由 Agent 自主驱动（Agent 自己决定调用哪个 tool），审批 Hook 正常工作

### 如何让 Orchestrator 也支持审批

有两种方案：

**方案 A：在 Orchestrator 中手动检查**

在 `executor.py` 的 `_launch_with_retry` 方法中，调用工具前手动执行审批逻辑：

```python
# executor.py 中添加
from src.agent.approval import ApprovalConfig, ApprovalHook

class Orchestrator:
    def __init__(self, ..., approval_config=None):
        self.approval_config = approval_config

    def _launch_with_retry(self, region, subnets):
        if self.approval_config:
            if self.state.remaining > self.approval_config.batch_threshold:
                raise ApprovalRequired(f"需要审批：{self.state.remaining} 台")
            if (self.approval_config.allowed_geo_regions
                    and region not in self.approval_config.allowed_geo_regions):
                raise ApprovalRequired(f"需要审批：Region {region} 超出地理边界")
        # ... 继续原有逻辑
```

**方案 B：让 Orchestrator 通过 Agent dispatch 工具**

将 Orchestrator 的工具调用改为通过 Agent 的 `tool.ec2_launch_instances(...)` 方式调用，这样 Strands 会自动触发 Hook。这需要较大的架构调整。

### 推荐

短期用方案 A（改动小，逻辑清晰），长期如果 Agent 自主性增强可以迁移到方案 B。

## 测试

审批逻辑的测试在 `tests/test_agent_approval.py`，覆盖了：

- 批量阈值：低于/高于阈值
- 地理边界：允许/不允许的 Region、空集合
- 工具黑名单：命中/未命中

运行测试：

```bash
python -m pytest tests/test_agent_approval.py -v
```

## 修改指南

| 需求 | 修改位置 |
|------|----------|
| 调整默认阈值 | `ApprovalConfig` 的默认值 |
| 新增审批规则（如按时间段限制） | `ApprovalHook._check_approval` 中添加新的检查分支 |
| 让 Orchestrator 也走审批 | 参考上方"方案 A"修改 `executor.py` |
| 审批后自动恢复执行 | 需要处理 Strands 的 Interrupt 响应机制 |
| 接入外部审批系统（Slack/钉钉） | 在 `_check_approval` 中发送审批请求，阻塞等待结果 |
