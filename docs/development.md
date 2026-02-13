# 本地开发指南

## 环境要求

- Python 3.11+
- AWS CLI（用于 SSM/DynamoDB 操作，本地测试可选）
- Terraform 1.0+（仅网络基础设施部署）

## 安装

```bash
# 克隆项目
git clone <repo-url>
cd crossregiongpu

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装项目及开发依赖
pip install -e ".[dev]"
```

## 项目结构

```
├── src/
│   ├── models/schemas.py        # Pydantic 数据模型 (Plan, StepResult, NextAction)
│   ├── config/loader.py         # 配置加载器 (YAML / SSM)
│   ├── tools/                   # @tool 工具层
│   │   ├── offerings.py         # DescribeInstanceTypeOfferings 预检
│   │   ├── launch.py            # ec2_launch_instances (Probe-and-Fill 核心)
│   │   ├── describe.py          # ec2_describe_instances 补全实例信息
│   │   ├── dynamodb.py          # dynamodb_put_instances 持久化
│   │   ├── region_order.py      # get_region_order 候选 Region 排序
│   │   └── finalize.py          # finalize 结果汇总
│   ├── orchestrator/executor.py # Orchestrator 状态机循环
│   └── agent/                   # Controller Agent
│       ├── main.py              # Agent 工厂函数
│       ├── prompts.py           # System Prompt 和模板
│       ├── state.py             # Agent State 管理
│       └── approval.py          # Human-in-the-loop 审批 Hook
├── config/
│   ├── regions.yaml             # Region/AZ/Subnet 白名单
│   └── environments/            # 多环境配置 (dev/staging/prod)
├── infra/
│   └── dynamodb.yaml            # DynamoDB CloudFormation 模板
├── 01-network/                  # Terraform 网络基础设施 (VPC/TGW)
├── tests/                       # 测试套件
├── scripts/
│   └── deploy.sh                # 部署脚本
└── docs/                        # 文档
```

## 配置

### Region 配置 (config/regions.yaml)

定义候选 Region、AZ、Subnet 白名单：

```yaml
regions:
  - region: ap-south-1
    priority: 1
    key_name: gpu-key-ap-south-1
    ami_id: ami-09b041abcb4daa286
    azs:
      - az_name: ap-south-1a
        subnets:
          - subnet-0fafe7b88481648c2
```

### 环境配置 (config/environments/*.yaml)

每个环境有独立配置文件，包含 DynamoDB 表名、SSM 参数路径、审批阈值等：

```yaml
stack_name: gpu-scheduler-dev
dynamodb_table: GpuProvisioningInstances-dev
ssm_parameter: /gpu-scheduler/dev/regions
approval:
  batch_threshold: 50
  allowed_geo_regions: []
```

## 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 单个模块
python -m pytest tests/test_tools_launch.py -v

# 属性测试（Hypothesis）
python -m pytest tests/test_pbt_probe_and_fill.py -v

# 端到端测试
python -m pytest tests/test_e2e.py -v

# 带覆盖率
python -m pytest tests/ --cov=src --cov-report=term-missing
```

测试使用 moto 模拟 AWS 服务，无需真实 AWS 凭证。

## 本地调试

### 使用 ConfigLoader 加载配置

```python
from src.config.loader import ConfigLoader

loader = ConfigLoader.from_yaml("config/regions.yaml")
regions = loader.get_ordered_regions(consumer_region="ap-south-1")
for r in regions:
    print(f"{r.region} (priority={r.priority})")
```

### 使用 Orchestrator 运行调度

```python
from src.orchestrator.executor import Orchestrator, OrchestratorState, ToolCallbacks

state = OrchestratorState(
    request_id="test-001",
    instance_type="g6.xlarge",
    remaining=4,
    regions=regions,
    region_mode="multi_region",
)
callbacks = ToolCallbacks(...)  # 注入工具回调
orch = Orchestrator(state=state, callbacks=callbacks)
result = orch.run()
print(result.status, result.total_launched)
```
