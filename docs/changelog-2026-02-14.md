# 变更记录 — 2026-02-14

本次变更包含三个部分：DynamoDB 查询结果增加运行时长计算、引入地理合规回退边界（fallback_groups）、以及新增大阪区域（ap-northeast-3）支持。

---

## 1. 实例运行时长计算

### 问题

Agent 在回答"计算每台实例的运行时长"时，需要对 DynamoDB 中的 `launched_at` 和 `terminated_at` 两个 ISO 时间戳做减法。LLM 在推理过程中"心算"时间差经常出错，导致返回的运行时长与实际不符。

### 方案

在 `dynamodb_query_instances` 工具的返回结果中，预计算好时长字段，Agent 直接读取即可，不再需要自行计算。

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `src/tools/dynamodb_query.py` | 新增 `_enrich_duration()` 函数，为每条查询结果自动计算并附加 `duration_seconds`（精确秒数）和 `duration_human`（可读格式，如"3分25秒"）字段 |

### 计算逻辑

- 已终止实例（有 `launched_at` + `terminated_at`）：`terminated_at - launched_at`
- 仍在运行实例（有 `launched_at`，status=launched）：`当前时间 - launched_at`，并标注"(仍在运行)"
- 时间解析失败时静默跳过，不影响原有返回结构

---

## 2. 地理合规回退边界（fallback_groups）

### 问题

原系统在目标 Region 容量不足时，会在 `regions.yaml` 中所有候选 Region 间无差别回退。这忽略了地理合规要求：

- 日本区域的数据合规要求 GPU 实例只能在日本境内启动
- 非预配置区域（如美西 us-west-2）的请求不应被接受

### 方案

引入 `fallback_groups` 配置，定义每个 consumer_region 允许回退到哪些 region。不在任何 group 中的 region 请求会被直接拒绝。

### 当前配置的回退规则

```
┌─────────────────────────────────────────────────────────────────┐
│ southeast_asia 组                                               │
│   用户请求: ap-southeast-1 (新加坡)                              │
│   允许回退: ap-south-1 → ap-northeast-1 → ap-northeast-2        │
├─────────────────────────────────────────────────────────────────┤
│ japan 组                                                        │
│   用户请求: ap-northeast-1 (东京) 或 ap-northeast-3 (大阪)       │
│   允许回退: ap-northeast-1 ↔ ap-northeast-3（仅日本境内）         │
├─────────────────────────────────────────────────────────────────┤
│ 其他区域 (如 us-west-2, eu-west-1 等)                            │
│   → 直接拒绝，返回错误信息和允许的 consumer region 列表           │
└─────────────────────────────────────────────────────────────────┘
```

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `config/regions.yaml` | 新增 `fallback_groups` 配置段 |
| `src/models/schemas.py` | 新增 `FallbackGroup` Pydantic 模型 |
| `src/config/loader.py` | `ConfigLoader` 解析 fallback_groups；新增 `get_allowed_regions()`、`is_consumer_region_allowed()`、`all_consumer_regions` 等方法；`get_ordered_regions()` 现在按 fallback group 过滤候选 region |
| `src/tools/region_order.py` | `get_region_order` 调用 `get_allowed_regions()`，不在任何 group 中的 region 返回 error dict |
| `src/agent/prompts.py` | System Prompt 新增 Geographic Compliance Boundary 章节 |
| `src/agent/main.py` | CLI 启动时将 fallback group 信息注入 Agent 上下文 |

### 向后兼容

当 `regions.yaml` 中没有 `fallback_groups` 配置时，所有行为与变更前完全一致（无地理限制）。

---

## 3. 新增大阪区域（ap-northeast-3）

### 变更文件

| 文件 | 变更内容 |
|------|----------|
| `config/regions.yaml` | 新增 ap-northeast-3 region 配置（priority=2，与东京同级），包含 ap-northeast-3a 和 ap-northeast-3c 两个 GPU 子网 |
| `01-network/main.tf` | `gpu_subnets` locals 中新增 osaka 条目（10.2.2.0/24, 10.2.3.0/24）；新增 `aws_subnet.osaka_gpu` 资源和 `aws_route_table_association.osaka_gpu` 路由表关联 |
| `01-network/outputs.tf` | `gpu_subnet_ids` 输出中新增 osaka |

### 大阪区域的回退行为

大阪（ap-northeast-3）已在 `japan` fallback group 的 `consumer_regions` 中，回退逻辑自动生效：

```
用户请求大阪启动 GPU 实例
  ↓
大阪有容量 → 在大阪启动 ✓
  ↓ (容量不足)
回退到东京 (ap-northeast-1) → 在东京启动 ✓
  ↓ (东京也不足)
日本境内所有候选耗尽 → 返回 PARTIAL/FAILED，告知原因
  ✗ 不会回退到日本以外的 region
```

---

## 部署注意事项

1. 大阪 GPU 子网需要先通过 Terraform 创建：
   ```bash
   cd 01-network
   terraform plan    # 确认新增 osaka_gpu 子网
   terraform apply
   ```

2. 确认 `regions.yaml` 中大阪的 AMI ID 和 subnet ID 已替换为真实值

3. 重新上传配置到 SSM：
   ```bash
   ./scripts/deploy.sh dev
   ```

4. 所有 128 个现有测试在变更后全部通过，无回归
