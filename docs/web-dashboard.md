# GPU Scheduler Web Dashboard — 部署与使用指南

## 目录

- [1. 概述](#1-概述)
- [2. 本地开发运行](#2-本地开发运行)
- [3. EC2 生产部署](#3-ec2-生产部署)
- [4. 登录与使用](#4-登录与使用)
- [5. API 接口参考](#5-api-接口参考)
- [6. 运维管理](#6-运维管理)
- [7. 故障排查](#7-故障排查)

---

## 1. 概述

Web Dashboard 是 GPU 跨区域动态调度器的浏览器管理界面，提供：

- 类聊天对话界面，与 Agent 交互执行 GPU 实例的创建/查询/删除
- JWT 认证保障安全访问
- 人工审批流程支持
- 对话历史记录

技术栈：FastAPI 后端 + 原生 HTML/CSS/JS 前端，代码位于 `web_dashboard/` 目录。后端通过 boto3 `invoke_agent_runtime` API 调用远程 AgentCore Runtime agent，不在本地创建 Agent 实例。

---

## 2. 本地开发运行

### 2.1 前置条件

- Python 3.11+
- 已安装主项目依赖：`pip install -e ".[dev]"`
- AWS 凭证已配置（Agent 需要调用 Bedrock、EC2 等服务）

### 2.2 安装 Web Dashboard 依赖

```bash
# 确保在项目根目录下操作（不要 cd 到 web_dashboard/ 里）
pip install -r web_dashboard/requirements.txt
```

> ⚠️ `web_dashboard/` 不是独立的 Python 包，不要在其中运行 `pip install -e .`。它是主项目的子模块，通过 `from src.agent.auth import ...` 引用主项目代码，必须从项目根目录运行。

### 2.3 设置环境变量

```bash
# 必须：AgentCore Runtime agent ARN
export AGENTCORE_AGENT_ARN="arn:aws:bedrock-agentcore:us-west-2:890717383483:runtime/agent_entrypoint-n98q9x9hpo"

# 必须：AgentCore 所在区域
export AGENTCORE_REGION="us-west-2"

# 必须：JWT 签名密钥（需与 AgentCore Runtime 中的 AUTH_SECRET_KEY 一致）
export AUTH_SECRET_KEY="your-secret-key-at-least-32-chars"

# 可选：自定义登录凭据（不设置则使用默认值 admin/admin123）
export WEB_DASHBOARD_USERNAME="admin"
export WEB_DASHBOARD_PASSWORD="admin123"

# 可选：AgentCore Memory 配置（用于对话历史持久化）
export MEMORY_ID="your-memory-id"
export MEMORY_REGION="us-west-2"
```

> Web Dashboard 通过 boto3 调用远程 AgentCore Runtime agent，不在本地创建 Agent 实例。确保 AWS 凭证已配置且有 `bedrock-agentcore:InvokeAgentRuntime` 权限。

### 2.4 启动服务

```bash
# 从项目根目录运行
uvicorn web_dashboard.app:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问 `http://localhost:8000`，会自动跳转到 `/login` 登录页。

---

## 3. EC2 生产部署

### 3.1 前置条件

- Ubuntu / Amazon Linux EC2 实例
- Python 3.11+
- Nginx 已安装
- EC2 实例具有访问 Bedrock、EC2、DynamoDB 的 IAM 角色

### 3.2 一键部署

```bash
# 1. SSH 到 EC2 实例
ssh -i your-key.pem ubuntu@<ec2-public-ip>

# 2. 克隆项目代码
git clone <your-repo-url> /tmp/crossregiongpu
cd /tmp/crossregiongpu

# 3. 安装 Nginx（如未安装）
sudo apt update && sudo apt install -y nginx python3-venv

# 4. 运行部署脚本
sudo ./web_dashboard/deploy/deploy.sh
# 可选指定端口：sudo ./web_dashboard/deploy/deploy.sh --port 9000
```

### 3.3 配置环境变量

部署脚本会创建 `/etc/default/web-dashboard` 环境文件，需要手动编辑：

```bash
sudo vim /etc/default/web-dashboard
```

内容示例：

```bash
AUTH_SECRET_KEY=your-production-secret-key-at-least-32-chars
AGENTCORE_AGENT_ARN=arn:aws:bedrock-agentcore:us-west-2:YOUR_ACCOUNT:runtime/YOUR_AGENT_ID
AGENTCORE_REGION=us-west-2
MEMORY_ID=your-agentcore-memory-id
MEMORY_REGION=us-west-2
WEB_DASHBOARD_USERNAME=admin
WEB_DASHBOARD_PASSWORD=your-strong-password
```

配置完成后重启服务：

```bash
sudo systemctl restart web-dashboard
```

### 3.4 部署脚本做了什么

1. 将项目文件复制到 `/opt/web-dashboard/`
2. 创建 Python 虚拟环境并安装依赖
3. 创建环境变量模板文件 `/etc/default/web-dashboard`
4. 安装并启用 systemd 服务 `web-dashboard`
5. 配置 Nginx 反向代理（80 端口 → uvicorn 8000 端口）
6. 启动服务

### 3.5 验证部署

```bash
# 检查服务状态
sudo systemctl status web-dashboard

# 检查 Nginx 状态
sudo systemctl status nginx

# 测试 API
curl http://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

---

## 4. 登录与使用

### 4.1 登录

1. 浏览器打开 `http://<your-host>` （本地开发为 `http://localhost:8000`）
2. 自动跳转到登录页面 `/login`
3. 输入用户名和密码：
   - 默认用户名：`admin`
   - 默认密码：`admin123`
   - （可通过环境变量 `WEB_DASHBOARD_USERNAME` / `WEB_DASHBOARD_PASSWORD` 自定义）
4. 点击登录，成功后自动跳转到聊天页面

登录成功后，JWT 令牌会存储在浏览器 localStorage 中，有效期 24 小时。

### 4.2 聊天交互

登录后进入聊天界面，可以直接用自然语言与 Agent 交互：

**查询实例：**
```
帮我查看当前所有 GPU 实例的状态
```

**创建实例：**
```
在 ap-northeast-1 区域启动 2 台 g5.xlarge 实例
```

**删除实例：**
```
删除实例 i-0abc123def456
```

### 4.3 审批流程

当 Agent 判断操作需要人工审批时（如大批量创建实例）：

1. 聊天窗口会显示审批卡片，包含审批原因
2. 审批期间输入框会被禁用
3. 点击「批准」或「拒绝」按钮
4. Agent 根据你的决定继续或终止操作

### 4.4 对话历史

- 进入聊天页面时会自动加载历史对话记录
- 历史记录通过 AgentCore Memory 模块持久化
- 如果 Memory 服务不可用，页面仍可正常使用，只是不显示历史记录

### 4.5 登出

点击页面上的登出按钮，会清除本地令牌并跳转回登录页。

---

## 5. API 接口参考

所有 API 响应统一格式：

```json
{
  "status": "success" | "error",
  "data": { ... } | null,
  "message": "描述信息"
}
```

| 接口 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/api/auth/login` | POST | 否 | 用户登录，返回 JWT 令牌 |
| `/api/chat/send` | POST | Bearer Token | 发送聊天消息 |
| `/api/chat/approve` | POST | Bearer Token | 提交审批决定 |
| `/api/chat/history` | GET | Bearer Token | 获取对话历史 |

### 登录

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

### 发送消息

```bash
curl -X POST http://localhost:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{"session_id":"my-session","message":"查看所有实例"}'
```

### 提交审批

```bash
curl -X POST http://localhost:8000/api/chat/approve \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{"session_id":"my-session","interrupt_id":"xxx","decision":"approved"}'
```

### 获取历史

```bash
curl "http://localhost:8000/api/chat/history?session_id=my-session" \
  -H "Authorization: Bearer <your-token>"
```

---

## 6. 运维管理

### 服务管理

```bash
# 查看状态
sudo systemctl status web-dashboard

# 启动/停止/重启
sudo systemctl start web-dashboard
sudo systemctl stop web-dashboard
sudo systemctl restart web-dashboard

# 查看日志
sudo journalctl -u web-dashboard -f

# 查看最近 100 行日志
sudo journalctl -u web-dashboard -n 100
```

### 手动生成 JWT Token

用于 API 调试或脚本调用：

```bash
# 使用默认参数
AUTH_SECRET_KEY="your-secret" python scripts/generate_token.py

# 自定义参数
AUTH_SECRET_KEY="your-secret" python scripts/generate_token.py \
  --user-id admin01 \
  --username "Li Jing" \
  --roles admin,operator \
  --expires 72

# 只输出 token（方便脚本使用）
AUTH_SECRET_KEY="your-secret" python scripts/generate_token.py --raw
```

---

## 7. 故障排查

| 问题 | 排查方法 |
|------|----------|
| 登录返回 "Invalid username or password" | 检查环境变量 `WEB_DASHBOARD_USERNAME` / `WEB_DASHBOARD_PASSWORD` |
| API 返回 401 | 检查 `AUTH_SECRET_KEY` 是否一致，令牌是否过期 |
| Agent 调用失败 | 检查 AWS 凭证、IAM 权限（需要 `bedrock-agentcore:InvokeAgentRuntime`）和 `AGENTCORE_AGENT_ARN` 配置 |
| 对话历史为空 | 检查 `MEMORY_ID` 和 `MEMORY_REGION` 配置，Memory 不可用时会静默降级 |
| Nginx 502 Bad Gateway | 确认 web-dashboard 服务正在运行：`systemctl status web-dashboard` |
| 页面样式异常 | 检查 Nginx 静态文件路径配置，确认 `/opt/web-dashboard/web_dashboard/static/` 存在 |
