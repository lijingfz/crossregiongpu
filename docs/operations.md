# 运维手册

## 日常运维

### 查询调度记录

按 request_id 查询某次调度启动的所有实例：

```bash
aws dynamodb query \
  --table-name GpuProvisioningInstances-prod \
  --key-condition-expression "request_id = :rid" \
  --expression-attribute-values '{":rid": {"S": "req-abc123"}}'
```

### 查看实例状态

```bash
# 查看某 Region 的实例
aws ec2 describe-instances \
  --region ap-south-1 \
  --instance-ids i-0123456789abcdef0 \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,Type:InstanceType,AZ:Placement.AvailabilityZone}'
```

### 终止实例

```bash
aws ec2 terminate-instances \
  --region ap-south-1 \
  --instance-ids i-0123456789abcdef0
```

## 监控

### 关键指标

| 指标 | 来源 | 告警阈值 |
|------|------|----------|
| 调度成功率 | DynamoDB allocation_status | FAILED 比例 > 20% |
| 单次调度耗时 | 应用日志 | > 600s |
| Region 容量不足频率 | StepResult.error_code | 同一 Region 连续 5 次 NONE |
| DynamoDB 写入延迟 | CloudWatch | P99 > 500ms |

### 日志

应用使用 Python logging，日志级别通过环境配置控制：

```yaml
# config/environments/prod.yaml
log_level: WARNING
```

日志中的关键字段：
- `request_id` — 调度请求唯一标识
- `region` — 当前操作的 Region
- `remaining` — 剩余需启动数量
- `status` — 步骤结果 (FULL/PARTIAL/NONE/ERROR)

## 故障排查

### InsufficientInstanceCapacity 频繁出现

原因：目标 Region 的 GPU 容量紧张。

处理：
1. 检查 `config/regions.yaml` 中是否有足够的候选 Region
2. 考虑添加更多 Region 到候选列表
3. 确认 `region_mode` 是否为 `multi_region`（允许跨 Region 回退）

### VcpuLimitExceeded

原因：账号在该 Region 的 vCPU 配额不足。

处理：
1. 通过 AWS Service Quotas 申请提升 G 系列实例的 vCPU 限额
2. 临时将该 Region 从候选列表中移除

### DynamoDB 写入失败

原因：可能是表不存在或权限不足。

处理：
1. 确认 DynamoDB 表已创建：`aws dynamodb describe-table --table-name <table>`
2. 确认 IAM 权限包含 `dynamodb:BatchWriteItem`
3. 检查表的 billing mode 是否为 PAY_PER_REQUEST

### Agent 响应超时

原因：Bedrock 模型调用超时或返回格式异常。

处理：
1. 检查 Bedrock 服务状态
2. 确认 `bedrock_region` 配置正确
3. 检查 `max_tokens` 是否足够（默认 4096）

### SSM 参数加载失败

原因：参数不存在或权限不足。

处理：
1. 确认参数已上传：`aws ssm get-parameter --name <param>`
2. 本地开发可使用 YAML 文件替代 SSM：`ConfigLoader.from_yaml("config/regions.yaml")`

## 配置变更

### 添加新 Region

1. 编辑 `config/regions.yaml`，添加新 Region 条目（含 AZ 和 Subnet）
2. 确保该 Region 的网络基础设施已通过 Terraform 部署
3. 确保该 Region 有对应的 Key Pair 和 AMI
4. 重新上传配置：`./scripts/deploy.sh <env>`

### 调整审批阈值

编辑对应环境的 `config/environments/<env>.yaml`：

```yaml
approval:
  batch_threshold: 15          # 单次请求数量阈值
  allowed_geo_regions:         # 允许的 Region 地理范围
    - ap-south-1
    - ap-northeast-1
```

### 调整 Probe-and-Fill 参数

```yaml
batch_max: 4                   # 初始批大小
max_attempts_per_subnet: 3     # 每个 Subnet 最大尝试次数
global_timeout_seconds: 3600   # 全局超时（秒）
```
