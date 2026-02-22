# 变更记录 — 2026-02-22

本次变更涵盖 5 个功能改进和问题修复：Web Dashboard SSE 流式工具调用进度展示、最近对话历史修复、Agent 自动跨区域回退修正、AgentCore Memory 配置回退、以及部署包体积超限问题。

---

## 1. SSE 流式工具调用进度展示

### 问题

用户在 Web Dashboard 发送消息后，前端只显示"正在思考..."，无法看到后台 Agent 正在调用哪些工具，体验上像是盲等。

### 方案

实现 Server-Sent Events (SSE) 流式推送，前端实时展示工具调用过程。

**后端：**
- `web_dashboard/agentcore_client.py` 新增 `invoke_agent_stream()` 生成器，解析 AgentCore Runtime 的流式响应，通过正则匹配 `toolUse` / `toolResult` JSON 片段，逐步 yield 事件：
  - `tool_start` — 工具开始调用
  - `tool_end` — 工具调用完成
  - `result` — 最终 AgentResponse
  - `error` — 异常信息
- `web_dashboard/routers/chat.py` 新增 `GET /api/chat/stream` SSE 端点，使用 FastAPI `StreamingResponse` 推送事件。原有 `POST /send` 保留作为降级方案。

**前端：**
- `web_dashboard/static/js/app.js` 中 `sendMessage()` 改用 `fetch()` + `ReadableStream` 读取 SSE 流。收到 `tool_start` 时显示 `🔧 调用 xxx ...` 工具卡片（带脉冲动画），收到 `tool_end` 时标记完成 ✓。
- `web_dashboard/static/css/style.css` 新增 `.tool-card` 样式，包含脉冲动画和完成态。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `web_dashboard/agentcore_client.py` | 新增 `invoke_agent_stream()` 生成器及流解析逻辑 |
| `web_dashboard/routers/chat.py` | 新增 `GET /api/chat/stream` SSE 端点 |
| `web_dashboard/static/js/app.js` | `sendMessage()` 改用 SSE 流；新增 `showToolCard()` / `finishToolCard()` |
| `web_dashboard/static/css/style.css` | 新增 `.tool-card` 样式和脉冲动画 |
| `web_dashboard/templates/chat.html` | CSS/JS 引用加 cache-buster `v=20260222` |

---

## 2. 最近对话历史修复

### 问题

Web Dashboard 侧边栏"最近对话"在以下场景不工作：
1. 页面刷新后历史消息丢失
2. 退出登录再重新进入后，点击历史对话条目无反应
3. Memory API 返回的数据格式解析失败

### 根因

三个独立问题：

1. **前端缓存仅在内存中** — `messageCache` 是 JS 对象，页面刷新即丢失
2. **Memory API 数据格式不匹配** — 后端 `/api/chat/history` 用 `event.get("messages", [])` 解析，但 AgentCore Memory 实际存储格式为 `event["payload"]`，内部结构是 `[{"conversational": {"content": {"text": "..."}, "role": "USER"}}]`
3. **MEMORY_ID 只从环境变量读取** — 本地开发环境未设置 `MEMORY_ID`，导致 Memory 客户端无法连接

### 方案

**前端：**
- 将消息缓存改为 `localStorage` 持久化（key: `gpu_msg_cache`）
- 切换会话时优先从 localStorage 恢复，回退到服务端 Memory API

**后端：**
- `/api/chat/history` 解析逻辑支持三种数据格式：
  - Format 1: AgentCore Memory `{"conversational": {"content": {"text": ...}, "role": ...}}`
  - Format 2: 扁平 dict `{"content": ..., "role": ...}`
  - Format 3: tuple `(content, role)`

**Memory 配置：**
- `src/agent/memory.py` 新增 `_get_memory_id()` 和 `_get_memory_region()` 辅助函数
- 环境变量为空时自动从 `config/environments/dev.yaml` 读取 `memory_id` / `memory_region`

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `web_dashboard/static/js/app.js` | 消息缓存改用 localStorage；新增 `_loadCache()` / `_saveCache()` / `restoreFromCache()` |
| `web_dashboard/routers/chat.py` | `/api/chat/history` 支持多种 Memory 数据格式解析 |
| `src/agent/memory.py` | 新增 `_get_memory_id()` / `_get_memory_region()`，三处 `os.environ.get("MEMORY_ID")` 替换为 fallback 函数 |

---

## 3. Agent 自动跨区域回退修正

### 问题

当用户请求在新加坡区域启动 GPU 实例，而该区域不支持所请求的实例类型时，Agent 会停下来询问用户"您希望如何调整？"，而不是自动回退到下一个候选区域。这违背了系统的核心设计原则 — Probe-and-Fill 自动跨区域调度。

### 根因

System Prompt 中缺少对 `describe_instance_type_offerings` 返回 `supported=false` 场景的明确指令。LLM 在遇到不支持的情况时默认询问用户。

### 方案

在 `src/agent/prompts.py` 的 `SYSTEM_PROMPT` 中新增 "Automatic Cross-Region Fallback (CRITICAL)" 章节，明确规定：

- `describe_instance_type_offerings` 返回 `supported=false` 时，视为 NONE，静默跳到下一候选区域
- 在 `multi_region` 模式下，**绝不**询问用户"选哪个区域"或"如何调整"
- 只有在所有候选区域都耗尽后才向用户报告失败
- 错误处理表新增 "OFFERINGS unsupported → skip Region immediately" 条目

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `src/agent/prompts.py` | `SYSTEM_PROMPT` 新增自动跨区域回退规则和错误处理条目 |

---

## 4. 部署依赖同步

### 问题

`./scripts/deploy.sh dev` 的 smoke test 失败：`test_deployment_config.py::TestRequirementsTxt::test_contains_all_pyproject_deps`。

### 根因

`pyproject.toml` 新增了 `fastapi`、`uvicorn`、`jinja2` 依赖，但 `requirements.txt` 未同步更新。部署测试会校验两者一致性。

### 方案

在 `requirements.txt` 中补充缺失的依赖项。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `pyproject.toml` | 新增 `fastapi>=0.110.0`、`uvicorn[standard]>=0.29.0`、`jinja2>=3.1.0` |
| `requirements.txt` | 同步新增上述三个依赖 |

---

## 5. AgentCore 部署包体积超限（750MB）

### 问题

`./scripts/deploy_agentcore.sh dev` 部署失败，错误信息：
```
✓ Deployment package ready: 178.08 MB
❌ Launch failed: Agent endpoint update failed: The extracted artifact size
   exceeds the allowed limit of 750MB. Please reduce the size of your artifact.
```

部署包 178MB，解压后超过 750MB 限制。

### 根因

两个问题叠加：

1. **`01-network/` 被打包** — 该目录包含 Terraform provider 二进制文件（`.terraform/`），未压缩约 683MB。AgentCore CLI 使用内置的 `dockerignore.template` 决定打包范围，其中有 `terraform/` 排除项但没有 `01-network/`
2. **缓存未失效** — AgentCore CLI 只检查 `requirements.txt` 的 hash 变化来决定是否重建依赖包。修改 `.gitignore` 不会触发重建，日志显示 "Using cached dependencies (no changes detected)"

### 技术细节

AgentCore CLI (`bedrock-agentcore-starter-toolkit`) 的打包逻辑位于 `utils/runtime/package.py`：
- `_build_direct_code_deploy()` 方法遍历 `source_path` 目录
- `_get_ignore_patterns()` 从内置 `dockerignore.template` 加载排除模式
- **不读取** 项目的 `.gitignore` 或 `.dockerignore`
- 缓存存储在 `.bedrock_agentcore/agent_entrypoint/`（`dependencies.zip` + `dependencies.hash`）

### 方案

修改 `scripts/deploy_agentcore.sh`，在 `agentcore launch` 前后增加三个步骤：

**Step 3 — 临时 Patch dockerignore 模板：**
- 通过 Python `importlib.resources` 定位 CLI 安装包中的 `dockerignore.template`
- 备份原始文件，追加项目特定排除项（`01-network/`、`infra/`、`scripts/`、`.hypothesis/`、`.kiro/`）
- 部署完成后自动恢复原始模板

**Step 4 — 清除部署缓存：**
- 删除 `.bedrock_agentcore/agent_entrypoint/` 目录，强制 CLI 重建部署包

**Step 6 — 恢复模板：**
- 无论部署成功或失败，都将 `dockerignore.template` 恢复为原始版本

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `scripts/deploy_agentcore.sh` | 新增 Step 3（patch 模板）、Step 4（清缓存）、Step 6（恢复模板）；捕获 `agentcore launch` 退出码 |
| `.gitignore` | `01-network/` 加入排除；`.terraform/` 改为 `**/.terraform/` 递归匹配 |

### 预期效果

部署包从 ~178MB 降至 ~30MB（仅包含 `src/`、`config/`、`agent_entrypoint.py`、`web_dashboard/` 和 Python 依赖）。

---

## 变更文件汇总

| 文件 | 涉及的改动项 |
|------|-------------|
| `web_dashboard/agentcore_client.py` | #1 SSE 流式 |
| `web_dashboard/routers/chat.py` | #1 SSE 端点, #2 History 解析 |
| `web_dashboard/static/js/app.js` | #1 SSE 前端, #2 localStorage 缓存 |
| `web_dashboard/static/css/style.css` | #1 工具卡片样式 |
| `web_dashboard/templates/chat.html` | #1 cache-buster |
| `src/agent/memory.py` | #2 Memory 配置 fallback |
| `src/agent/prompts.py` | #3 自动跨区域回退 |
| `pyproject.toml` | #4 依赖同步 |
| `requirements.txt` | #4 依赖同步 |
| `scripts/deploy_agentcore.sh` | #5 部署包瘦身 |
| `.gitignore` | #5 排除 01-network/ |
