# 部署指南

## 前置条件

- AWS CLI 已配置，具有以下权限：
  - CloudFormation（创建/更新 Stack）
  - DynamoDB（创建表）
  - SSM Parameter Store（读写参数）
  - EC2（RunInstances, DescribeInstances, DescribeInstanceTypeOfferings）
  - Bedrock（InvokeModel）
- Python 3.11+
- Terraform 1.0+（仅首次部署网络基础设施）

## 环境说明

| 环境 | 配置文件 | DynamoDB 表 | 审批阈值 |
|------|----------|-------------|----------|
| dev | `config/environments/dev.yaml` | GpuProvisioningInstances-dev | 50（宽松） |
| staging | `config/environments/staging.yaml` | GpuProvisioningInstances-staging | 20 |
| prod | `config/environments/prod.yaml` | GpuProvisioningInstances-prod | 10（严格） |

## 部署步骤

### 1. 网络基础设施（首次）

网络层使用 Terraform 管理，包含多 Region VPC、GPU 子网、Transit Gateway 互联：

```bash
cd 01-network
terraform init
terraform plan
terraform apply
```

这会在 ap-south-1、ap-northeast-1、ap-northeast-2 等 Region 创建：
- VPC + GPU 子网（多 AZ）
- Internet Gateway
- Transit Gateway + 跨 Region Peering（以 Singapore 为 Hub）
- SSH Key Pair

### 2. 应用部署

使用部署脚本一键完成 DynamoDB 表创建、SSM 配置上传、依赖安装：

```bash
# dev 环境
./scripts/deploy.sh dev

# staging 环境
./scripts/deploy.sh staging

# prod 环境
./scripts/deploy.sh prod
```

部署脚本执行流程：
1. 验证环境名称和 AWS 凭证
2. 通过 CloudFormation 部署 DynamoDB 表
3. 将 `config/regions.yaml` 上传到 SSM Parameter Store
4. 安装 Python 依赖
5. 运行冒烟测试（prod 环境跳过）

### 3. 验证部署

```bash
# 检查 DynamoDB 表
aws dynamodb describe-table --table-name GpuProvisioningInstances-dev

# 检查 SSM 参数
aws ssm get-parameter --name /gpu-scheduler/dev/regions

# 检查 CloudFormation Stack
aws cloudformation describe-stacks --stack-name gpu-scheduler-dev
```

## 更新 Region 配置

Region 配置支持动态更新，无需重新部署应用（Requirement 8.4）：

```bash
# 编辑 config/regions.yaml，然后重新上传到 SSM
./scripts/deploy.sh dev
```

或手动更新 SSM：

```bash
aws ssm put-parameter \
  --name /gpu-scheduler/dev/regions \
  --type String \
  --value "$(python3 -c 'import yaml,json; print(json.dumps(yaml.safe_load(open("config/regions.yaml"))))')" \
  --overwrite
```

## IAM 权限参考

调度器运行所需的最小 IAM 权限：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypeOfferings",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:BatchWriteItem",
        "dynamodb:Query",
        "dynamodb:GetItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/GpuProvisioningInstances-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:*:*:parameter/gpu-scheduler/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": "*"
    }
  ]
}
```
