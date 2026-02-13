# GPU Cross-Region Dynamic Scheduler

跨 Region GPU 动态调度系统，基于 Strands Agent SDK + Bedrock Claude 构建。

系统解决 AWS GPU 实例（G5/G6）启动时 `InsufficientInstanceCapacity` 容量不足问题，通过 Probe-and-Fill 策略在多个候选 Region 间按就近优先级逐个尝试启动，直到满足需求或候选耗尽。

## 目录

- [本地开发](./development.md) — 环境搭建、运行测试
- [部署指南](./deployment.md) — 多环境部署流程
- [运维手册](./operations.md) — 日常运维、监控、故障排查
- [审批机制](./approval-flow.md) — Human-in-the-loop 审批说明

## 架构概览

```
用户输入 → Controller Agent (Strands/Bedrock Claude)
              ↓
         Orchestrator (状态机循环)
              ↓
         Tools (EC2 Launch / Describe / DynamoDB / Offerings)
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

# 部署（dev 环境）
./scripts/deploy.sh dev
```
