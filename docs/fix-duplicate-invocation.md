# 修复：EC2 实例重复启动问题（完整分析）

日期：2026-02-20（更新）  
初始发现：2026-02-19

---

## 1. 问题现象

用户在 Web Dashboard 输入"请在新加坡启动 3 台 g6.xlarge 类型实例"，Agent 成功完成任务后，
用户看到的最终响应不是启动结果，而是一条"我注意到您刚才已经请求过……"的提示信息。

从 CloudWatch 日志可以观察到：

- 同一个 `sessionId` 下出现两个不同的 `requestId`
- 第一次 invoke 正常完成（耗时 72 秒），成功启动了 3 台实例
- 第二次 invoke 在第一次完成后 **30ms** 内立即触发
- 第二次 invoke 中 LLM 看到对话历史已有完成记录，回复"已经启动过了"
- 用户最终看到的是第二次 invoke 的响应，而非第一次的启动结果

```
# 第一次 invoke — 正常完成（耗时 72s）
04:53:52.659  === INVOKE ENTRY === session=e54abcb9, request_id=cd773a53
04:54:04.066  Tool #4: ec2_launch_instances
04:54:50.731  === GUARD FULFILLED ARMED: launched=3, target=3 ===
04:55:04.734  Invocation completed successfully (72.075s)

# 第二次 invoke — 30ms 后立即触发
04:55:04.764  === INVOKE ENTRY === session=e54abcb9, request_id=5e7f37ac
04:55:06.947  "我注意到您刚才已经请求过在新加坡区域启动3台g6.xlarge实例..."
04:55:08.496  Invocation completed successfully (3.733s)
```

关键观察：**删除、查询等操作从未出现此问题，只有 launch 操作会触发重复调用。**

---

## 2. 排查过程

### 2.1 初始假设：AgentCore Runtime 基础设施层通用重试

最初怀疑 AgentCore Runtime 基础设施层对所有请求都有重试机制。
但用户反馈删除、查询实例等操作从未出现 re-invocation，只有 launch 有。
这排除了通用重试的可能性。

### 2.2 关键线索：耗时差异

| 操作类型 | 典型耗时 | 是否出现 re-invocation |
|----------|----------|----------------------|
| 查询实例 | 2-5 秒   | 否                   |
| 删除实例 | 3-8 秒   | 否                   |
| 启动实例 | 60-120 秒 | 是                  |

launch 操作涉及跨区域探测、多 AZ 轮询、EC2 RunInstances、DynamoDB 持久化、
finalize 等 6 个 tool call，总耗时通常在 60-120 秒。

### 2.3 定位根因：boto3 默认 read_timeout

`web_dashboard/agentcore_client.py` 中创建 boto3 客户端时没有配置超时：

```python
# ❌ 修复前 — 使用 boto3 默认配置
def _get_client():
    region = os.environ.get("AGENTCORE_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore", region_name=region)
```

boto3 的默认 `read_timeout` 是 **60 秒**。当 launch 操作耗时超过 60 秒时：

1. boto3 客户端在第 60 秒触发 read timeout，TCP 连接断开
2. AgentCore Runtime 检测到客户端连接断开
3. Runtime 在同一 session 内重新触发 `invoke()` 调用
4. 同时 boto3 可能触发自动重试（默认 retry 机制）

这完美解释了为什么只有 launch 操作会出现 re-invocation — 它是唯一耗时超过
60 秒默认超时的操作。

### 2.4 验证过程中发现的其他 Bug

在排查过程中还发现了一个独立的 Bug：

**LaunchGuardHook 无法解析 Strands 包装的 tool result**

`ec2_launch_instances` 返回 `StepResult.model_dump()`，格式为：
```python
{"status": "FULL", "launched": 3, "requested": 3, ...}
```

Strands `@tool` 装饰器的 `_wrap_tool_result()` 检查返回值：
- 如果 dict 同时有 `"status"` 和 `"content"` 键 → 直接透传为 ToolResult
- 否则 → 包装为 `{"status": "success", "content": [{"text": str(result)}]}`

`StepResult` 有 `"status"` 但没有 `"content"`，所以 Strands 将其包装为文本格式。
而 `_after_launch` 原来只查找 `content[].json.launched`，永远解析不到数据，
导致 `_fulfilled` 标志永远不会被设置。

此 Bug 已独立修复（见 3.2 节）。

---

## 3. 修复方案（两层防护）

### 3.1 根因修复：boto3 客户端超时配置（`web_dashboard/agentcore_client.py`）

将 `read_timeout` 设为 500 秒，并禁用 boto3 自动重试，从源头消除 re-invocation。

```python
# ✅ 修复后
from botocore.config import Config

_BOTO_CONFIG = Config(
    read_timeout=500,                     # 足够覆盖最坏情况的多区域启动
    retries={"max_attempts": 0},          # 禁用自动重试，避免重复调用
)

def _get_client():
    region = os.environ.get("AGENTCORE_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore", region_name=region, config=_BOTO_CONFIG)
```

### 3.2 Bug 修复：LaunchGuardHook 结果解析（`src/agent/launch_guard.py`）

新增 `_extract_from_content()` 方法，同时支持两种 Strands 返回格式：

- **Format 1**：`content[].json` — 预格式化的 ToolResult（直接透传时）
- **Format 2**：`content[].text` — Strands 默认的 `str(dict)` 包装

```python
def _extract_from_content(self, result: dict) -> tuple[int, int]:
    for content_block in result.get("content", []):
        # Format 1: json content block
        json_data = content_block.get("json")
        if isinstance(json_data, dict):
            launched, requested = self._parse_step_result_dict(json_data)
            if launched > 0 or requested > 0:
                return launched, requested

        # Format 2: text content block (Strands 包装的 str(dict))
        text_data = content_block.get("text")
        if isinstance(text_data, str) and "launched" in text_data:
            parsed = self._try_parse_text_as_dict(text_data)  # ast.literal_eval + json.loads
            if parsed is not None:
                launched, requested = self._parse_step_result_dict(parsed)
                if launched > 0 or requested > 0:
                    return launched, requested
    return 0, 0
```

修复后，`_after_launch` 能正确提取 launched 数量，`_fulfilled` 标志在目标达成时
立即被设置为 True，阻止同一 invocation 内的后续 launch 调用。

### 3.3 防御性缓存：Re-invocation 响应缓存（`agent_entrypoint.py`）

作为第二道防线，在 `invoke()` 中实现响应缓存。即使因为其他原因仍然发生
re-invocation，entrypoint 会返回缓存的第一次响应，用户看到正确的启动结果。

```python
# 缓存结构：(session_id, prompt_hash) → (response_dict, timestamp, request_id)
_response_cache: dict[tuple[str, str], tuple[dict, float, str]] = {}
REINVOKE_WINDOW_SECONDS = 10  # re-invocation 通常在 <1s 内到达
```

检测逻辑：
- `agent(prompt)` 完成后，缓存 `(session_id, prompt_hash) → (response, timestamp, request_id)`
- 下次 `invoke()` 进来时，如果同一 session + 同一 prompt 在 10 秒内被**不同的 request_id** 调用 → 判定为 re-invocation → 返回缓存响应
- 同一 request_id 不会命中缓存（不可能发生，但作为安全检查）
- 超过 10 秒窗口的请求视为用户的新请求，正常执行

```python
# invoke() 中的关键逻辑
cached_resp = _check_reinvoke_cache(session_id, prompt, current_request_id)
if cached_resp is not None:
    return cached_resp          # 直接返回缓存，不调用 agent(prompt)

result = agent(prompt)
resp = _build_response(result, session_id, user_id)
_store_reinvoke_cache(session_id, prompt, resp, current_request_id)
return resp
```

**用户重复命令不受影响**：用户发送两次"启动3台g6.xlarge"期望得到 6 台。
第二次请求到达时，第一次的响应已经返回给客户端，entrypoint 正常执行
`agent(prompt)` 并用新的 response 覆盖缓存。

### 3.4 Stream 完全消费（`web_dashboard/agentcore_client.py`）

`_parse_response()` 在检测到第一个完整 JSON 后继续 drain 剩余 stream，
确保 stream 被完全消费（此修复在根因修复之前已实施，作为额外保障保留）。

---

## 4. 因果链总结

```
boto3 默认 read_timeout=60s
    → launch 操作耗时 72s，超过 60s 超时
        → 客户端 TCP 连接断开
            → AgentCore Runtime 检测到断开，重新 invoke
                → 同一条 prompt 被追加到 Agent 消息历史
                    → LLM 看到已完成记录，回复"已经启动过了"
                        → 用户看到错误的响应
```

同时，LaunchGuardHook 的 `_after_launch` 无法解析 Strands 包装的文本格式结果，
导致 `_fulfilled` 标志永远不会被设置，Guard 无法作为防线阻止重复启动。

---

## 5. 变更文件清单

| 文件 | 变更内容 |
|------|----------|
| `web_dashboard/agentcore_client.py` | `read_timeout=500`，`retries={"max_attempts": 0}`，stream 完全消费 |
| `src/agent/launch_guard.py` | 新增 `_extract_from_content()`、`_try_parse_text_as_dict()` 支持文本格式解析；`_fulfilled` 自动设置机制 |
| `agent_entrypoint.py` | 新增 re-invocation 响应缓存（`_response_cache`、`_check_reinvoke_cache`、`_store_reinvoke_cache`）；通过 `BedrockAgentCoreContext.get_request_id()` 获取 request_id |
| `tests/test_launch_guard.py` | 30 个测试覆盖 Guard 的阻止、跟踪、重置、re-invocation 防护、边界情况 |
| `tests/test_reinvoke_cache.py` | 10 个测试覆盖缓存命中/未命中、窗口过期、用户重复命令场景 |

---

## 6. 验证

```bash
$ python -m pytest tests/test_launch_guard.py tests/test_reinvoke_cache.py -v
# 40 passed in 0.34s
```

部署后日志确认：
- 不再出现第二次 `INVOKE ENTRY`（根因修复生效）
- 如果万一仍有 re-invocation，会看到 `=== REINVOKE CACHE HIT ===`（防御层生效）

---

## 7. 配置参数

| 参数 | 位置 | 值 | 说明 |
|------|------|-----|------|
| `read_timeout` | `web_dashboard/agentcore_client.py` | 500s | boto3 读超时，需覆盖最坏情况的多区域启动 |
| `max_attempts` | `web_dashboard/agentcore_client.py` | 0 | 禁用 boto3 自动重试 |
| `REINVOKE_WINDOW_SECONDS` | `agent_entrypoint.py` | 10s | 防御性缓存窗口，re-invocation 通常在 <1s 内到达 |
| `max_launch_calls` | `src/agent/launch_guard.py` | 8 | 单次 invocation 内 launch 调用次数硬上限 |

---

## 8. 经验总结

1. **boto3 默认超时是隐性杀手**：`read_timeout=60s` 对于短操作足够，但长时间运行的
   agent 操作（多 tool call 链式执行）很容易超过。使用 AgentCore Runtime 时，
   客户端必须根据最坏情况设置足够的 `read_timeout`，并禁用自动重试。

2. **只在特定操作出现的 bug 要关注操作特性差异**：re-invocation 只在 launch 出现
   而删除/查询没有，这个线索直接指向了耗时差异，进而定位到 read_timeout 根因。
   如果一开始就假设是"AgentCore Runtime 通用行为"，会走很多弯路。

3. **Strands SDK 的 ToolResult 包装规则需要了解**：`@tool` 装饰器对返回值的包装
   取决于返回 dict 是否同时有 `"status"` 和 `"content"` 键。如果只有 `"status"`
   没有 `"content"`（如 StepResult），返回值会被包装为文本格式。Hook 中解析
   tool result 时必须同时处理两种格式。

4. **防御性编程的价值**：即使根因已修复（read_timeout），防御性缓存层仍然有价值。
   它能应对未知的 re-invocation 触发条件，且实现成本很低（~40 行代码）。
