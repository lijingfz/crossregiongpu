terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

# Provider配置
provider "aws" {
  alias  = "singapore"
  region = "ap-southeast-1"
}
provider "aws" {
  alias  = "tokyo"
  region = "ap-northeast-1"
}
provider "aws" {
  alias  = "osaka"
  region = "ap-northeast-3"
}
provider "aws" {
  alias  = "mumbai"
  region = "ap-south-1"
}
provider "aws" {
  alias  = "seoul"
  region = "ap-northeast-2"
}

locals {
  regions = {
    singapore = { cidr = "10.0.0.0/16", az = "ap-southeast-1a" }
    tokyo     = { cidr = "10.1.0.0/16", az = "ap-northeast-1a" }
    osaka     = { cidr = "10.2.0.0/16", az = "ap-northeast-3a" }
    mumbai    = { cidr = "10.3.0.0/16", az = "ap-south-1a" }
    seoul     = { cidr = "10.4.0.0/16", az = "ap-northeast-2a" }
  }

  # GPU可用区子网规划
  gpu_subnets = {
    tokyo = {
      "ap-northeast-1a" = "10.1.2.0/24"
      "ap-northeast-1c" = "10.1.3.0/24"
    }
    osaka = {
      "ap-northeast-3a" = "10.2.2.0/24"
      "ap-northeast-3c" = "10.2.3.0/24"
    }
    mumbai = {
      "ap-south-1a" = "10.3.2.0/24"
      "ap-south-1b" = "10.3.3.0/24"
    }
    seoul = {
      "ap-northeast-2a" = "10.4.2.0/24"
      "ap-northeast-2b" = "10.4.3.0/24"
      "ap-northeast-2c" = "10.4.4.0/24"
      "ap-northeast-2d" = "10.4.5.0/24"
    }
  }
}

# ==================== VPC ====================
resource "aws_vpc" "singapore" {
  provider             = aws.singapore
  cidr_block           = local.regions.singapore.cidr
  enable_dns_hostnames = true
  tags                 = { Name = "crossregiongpu-vpc-singapore" }
}

resource "aws_vpc" "tokyo" {
  provider             = aws.tokyo
  cidr_block           = local.regions.tokyo.cidr
  enable_dns_hostnames = true
  tags                 = { Name = "crossregiongpu-vpc-tokyo" }
}

resource "aws_vpc" "osaka" {
  provider             = aws.osaka
  cidr_block           = local.regions.osaka.cidr
  enable_dns_hostnames = true
  tags                 = { Name = "crossregiongpu-vpc-osaka" }
}

resource "aws_vpc" "mumbai" {
  provider             = aws.mumbai
  cidr_block           = local.regions.mumbai.cidr
  enable_dns_hostnames = true
  tags                 = { Name = "crossregiongpu-vpc-mumbai" }
}

resource "aws_vpc" "seoul" {
  provider             = aws.seoul
  cidr_block           = local.regions.seoul.cidr
  enable_dns_hostnames = true
  tags                 = { Name = "crossregiongpu-vpc-seoul" }
}

# ==================== Subnets (基础子网) ====================
resource "aws_subnet" "singapore" {
  provider                = aws.singapore
  vpc_id                  = aws_vpc.singapore.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = local.regions.singapore.az
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-subnet-singapore" }
}

resource "aws_subnet" "tokyo" {
  provider                = aws.tokyo
  vpc_id                  = aws_vpc.tokyo.id
  cidr_block              = "10.1.1.0/24"
  availability_zone       = local.regions.tokyo.az
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-subnet-tokyo" }
}

resource "aws_subnet" "osaka" {
  provider                = aws.osaka
  vpc_id                  = aws_vpc.osaka.id
  cidr_block              = "10.2.1.0/24"
  availability_zone       = local.regions.osaka.az
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-subnet-osaka" }
}

resource "aws_subnet" "mumbai" {
  provider                = aws.mumbai
  vpc_id                  = aws_vpc.mumbai.id
  cidr_block              = "10.3.1.0/24"
  availability_zone       = local.regions.mumbai.az
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-subnet-mumbai" }
}

resource "aws_subnet" "seoul" {
  provider                = aws.seoul
  vpc_id                  = aws_vpc.seoul.id
  cidr_block              = "10.4.1.0/24"
  availability_zone       = local.regions.seoul.az
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-subnet-seoul" }
}

# ==================== GPU子网 (多AZ) ====================
# 东京 GPU子网
resource "aws_subnet" "tokyo_gpu" {
  for_each                = local.gpu_subnets.tokyo
  provider                = aws.tokyo
  vpc_id                  = aws_vpc.tokyo.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-gpu-subnet-tokyo-${each.key}" }
}

# 大阪 GPU子网
resource "aws_subnet" "osaka_gpu" {
  for_each                = local.gpu_subnets.osaka
  provider                = aws.osaka
  vpc_id                  = aws_vpc.osaka.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-gpu-subnet-osaka-${each.key}" }
}

# 孟买 GPU子网
resource "aws_subnet" "mumbai_gpu" {
  for_each                = local.gpu_subnets.mumbai
  provider                = aws.mumbai
  vpc_id                  = aws_vpc.mumbai.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-gpu-subnet-mumbai-${each.key}" }
}

# 首尔 GPU子网
resource "aws_subnet" "seoul_gpu" {
  for_each                = local.gpu_subnets.seoul
  provider                = aws.seoul
  vpc_id                  = aws_vpc.seoul.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags                    = { Name = "crossregiongpu-gpu-subnet-seoul-${each.key}" }
}

# ==================== Internet Gateways ====================
resource "aws_internet_gateway" "singapore" {
  provider = aws.singapore
  vpc_id   = aws_vpc.singapore.id
  tags     = { Name = "crossregiongpu-igw-singapore" }
}

resource "aws_internet_gateway" "tokyo" {
  provider = aws.tokyo
  vpc_id   = aws_vpc.tokyo.id
  tags     = { Name = "crossregiongpu-igw-tokyo" }
}

resource "aws_internet_gateway" "osaka" {
  provider = aws.osaka
  vpc_id   = aws_vpc.osaka.id
  tags     = { Name = "crossregiongpu-igw-osaka" }
}

resource "aws_internet_gateway" "mumbai" {
  provider = aws.mumbai
  vpc_id   = aws_vpc.mumbai.id
  tags     = { Name = "crossregiongpu-igw-mumbai" }
}

resource "aws_internet_gateway" "seoul" {
  provider = aws.seoul
  vpc_id   = aws_vpc.seoul.id
  tags     = { Name = "crossregiongpu-igw-seoul" }
}

# ==================== Transit Gateways ====================
resource "aws_ec2_transit_gateway" "singapore" {
  provider    = aws.singapore
  description = "TGW Singapore"
  tags        = { Name = "crossregiongpu-tgw-singapore" }
}

resource "aws_ec2_transit_gateway" "tokyo" {
  provider    = aws.tokyo
  description = "TGW Tokyo"
  tags        = { Name = "crossregiongpu-tgw-tokyo" }
}

resource "aws_ec2_transit_gateway" "osaka" {
  provider    = aws.osaka
  description = "TGW Osaka"
  tags        = { Name = "crossregiongpu-tgw-osaka" }
}

resource "aws_ec2_transit_gateway" "mumbai" {
  provider    = aws.mumbai
  description = "TGW Mumbai"
  tags        = { Name = "crossregiongpu-tgw-mumbai" }
}

resource "aws_ec2_transit_gateway" "seoul" {
  provider    = aws.seoul
  description = "TGW Seoul"
  tags        = { Name = "crossregiongpu-tgw-seoul" }
}

# ==================== TGW VPC Attachments ====================
resource "aws_ec2_transit_gateway_vpc_attachment" "singapore" {
  provider           = aws.singapore
  transit_gateway_id = aws_ec2_transit_gateway.singapore.id
  vpc_id             = aws_vpc.singapore.id
  subnet_ids         = [aws_subnet.singapore.id]
  tags               = { Name = "crossregiongpu-tgw-attach-singapore" }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "tokyo" {
  provider           = aws.tokyo
  transit_gateway_id = aws_ec2_transit_gateway.tokyo.id
  vpc_id             = aws_vpc.tokyo.id
  subnet_ids         = [aws_subnet.tokyo.id]
  tags               = { Name = "crossregiongpu-tgw-attach-tokyo" }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "osaka" {
  provider           = aws.osaka
  transit_gateway_id = aws_ec2_transit_gateway.osaka.id
  vpc_id             = aws_vpc.osaka.id
  subnet_ids         = [aws_subnet.osaka.id]
  tags               = { Name = "crossregiongpu-tgw-attach-osaka" }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "mumbai" {
  provider           = aws.mumbai
  transit_gateway_id = aws_ec2_transit_gateway.mumbai.id
  vpc_id             = aws_vpc.mumbai.id
  subnet_ids         = [aws_subnet.mumbai.id]
  tags               = { Name = "crossregiongpu-tgw-attach-mumbai" }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "seoul" {
  provider           = aws.seoul
  transit_gateway_id = aws_ec2_transit_gateway.seoul.id
  vpc_id             = aws_vpc.seoul.id
  subnet_ids         = [aws_subnet.seoul.id]
  tags               = { Name = "crossregiongpu-tgw-attach-seoul" }
}

# ==================== TGW Peering (Singapore as Hub) ====================
resource "aws_ec2_transit_gateway_peering_attachment" "sg_to_tokyo" {
  provider                = aws.singapore
  transit_gateway_id      = aws_ec2_transit_gateway.singapore.id
  peer_transit_gateway_id = aws_ec2_transit_gateway.tokyo.id
  peer_region             = "ap-northeast-1"
  tags                    = { Name = "tgw-peering-sg-tokyo" }
}

resource "aws_ec2_transit_gateway_peering_attachment_accepter" "tokyo_accept" {
  provider                      = aws.tokyo
  transit_gateway_attachment_id = aws_ec2_transit_gateway_peering_attachment.sg_to_tokyo.id
  tags                          = { Name = "tgw-peering-sg-tokyo-accept" }
}

resource "aws_ec2_transit_gateway_peering_attachment" "sg_to_osaka" {
  provider                = aws.singapore
  transit_gateway_id      = aws_ec2_transit_gateway.singapore.id
  peer_transit_gateway_id = aws_ec2_transit_gateway.osaka.id
  peer_region             = "ap-northeast-3"
  tags                    = { Name = "tgw-peering-sg-osaka" }
}

resource "aws_ec2_transit_gateway_peering_attachment_accepter" "osaka_accept" {
  provider                      = aws.osaka
  transit_gateway_attachment_id = aws_ec2_transit_gateway_peering_attachment.sg_to_osaka.id
  tags                          = { Name = "tgw-peering-sg-osaka-accept" }
}

resource "aws_ec2_transit_gateway_peering_attachment" "sg_to_mumbai" {
  provider                = aws.singapore
  transit_gateway_id      = aws_ec2_transit_gateway.singapore.id
  peer_transit_gateway_id = aws_ec2_transit_gateway.mumbai.id
  peer_region             = "ap-south-1"
  tags                    = { Name = "tgw-peering-sg-mumbai" }
}

resource "aws_ec2_transit_gateway_peering_attachment_accepter" "mumbai_accept" {
  provider                      = aws.mumbai
  transit_gateway_attachment_id = aws_ec2_transit_gateway_peering_attachment.sg_to_mumbai.id
  tags                          = { Name = "tgw-peering-sg-mumbai-accept" }
}

resource "aws_ec2_transit_gateway_peering_attachment" "sg_to_seoul" {
  provider                = aws.singapore
  transit_gateway_id      = aws_ec2_transit_gateway.singapore.id
  peer_transit_gateway_id = aws_ec2_transit_gateway.seoul.id
  peer_region             = "ap-northeast-2"
  tags                    = { Name = "tgw-peering-sg-seoul" }
}

resource "aws_ec2_transit_gateway_peering_attachment_accepter" "seoul_accept" {
  provider                      = aws.seoul
  transit_gateway_attachment_id = aws_ec2_transit_gateway_peering_attachment.sg_to_seoul.id
  tags                          = { Name = "tgw-peering-sg-seoul-accept" }
}

# ==================== TGW Route Tables ====================
resource "aws_ec2_transit_gateway_route" "sg_to_tokyo" {
  provider                       = aws.singapore
  destination_cidr_block         = local.regions.tokyo.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_tokyo.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.singapore.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.tokyo_accept]
}

resource "aws_ec2_transit_gateway_route" "sg_to_osaka" {
  provider                       = aws.singapore
  destination_cidr_block         = local.regions.osaka.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_osaka.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.singapore.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.osaka_accept]
}

resource "aws_ec2_transit_gateway_route" "sg_to_mumbai" {
  provider                       = aws.singapore
  destination_cidr_block         = local.regions.mumbai.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_mumbai.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.singapore.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.mumbai_accept]
}

resource "aws_ec2_transit_gateway_route" "sg_to_seoul" {
  provider                       = aws.singapore
  destination_cidr_block         = local.regions.seoul.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_seoul.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.singapore.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.seoul_accept]
}

resource "aws_ec2_transit_gateway_route" "tokyo_to_sg" {
  provider                       = aws.tokyo
  destination_cidr_block         = local.regions.singapore.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_tokyo.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.tokyo.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.tokyo_accept]
}

resource "aws_ec2_transit_gateway_route" "osaka_to_sg" {
  provider                       = aws.osaka
  destination_cidr_block         = local.regions.singapore.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_osaka.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.osaka.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.osaka_accept]
}

resource "aws_ec2_transit_gateway_route" "mumbai_to_sg" {
  provider                       = aws.mumbai
  destination_cidr_block         = local.regions.singapore.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_mumbai.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.mumbai.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.mumbai_accept]
}

resource "aws_ec2_transit_gateway_route" "seoul_to_sg" {
  provider                       = aws.seoul
  destination_cidr_block         = local.regions.singapore.cidr
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_peering_attachment.sg_to_seoul.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway.seoul.association_default_route_table_id
  depends_on                     = [aws_ec2_transit_gateway_peering_attachment_accepter.seoul_accept]
}

# ==================== VPC Route Tables ====================
resource "aws_route_table" "singapore" {
  provider = aws.singapore
  vpc_id   = aws_vpc.singapore.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.singapore.id
  }
  route {
    cidr_block         = local.regions.tokyo.cidr
    transit_gateway_id = aws_ec2_transit_gateway.singapore.id
  }
  route {
    cidr_block         = local.regions.osaka.cidr
    transit_gateway_id = aws_ec2_transit_gateway.singapore.id
  }
  route {
    cidr_block         = local.regions.mumbai.cidr
    transit_gateway_id = aws_ec2_transit_gateway.singapore.id
  }
  route {
    cidr_block         = local.regions.seoul.cidr
    transit_gateway_id = aws_ec2_transit_gateway.singapore.id
  }
  tags       = { Name = "crossregiongpu-rt-singapore" }
  depends_on = [aws_ec2_transit_gateway_vpc_attachment.singapore]
}

resource "aws_route_table" "tokyo" {
  provider = aws.tokyo
  vpc_id   = aws_vpc.tokyo.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.tokyo.id
  }
  route {
    cidr_block         = local.regions.singapore.cidr
    transit_gateway_id = aws_ec2_transit_gateway.tokyo.id
  }
  tags       = { Name = "crossregiongpu-rt-tokyo" }
  depends_on = [aws_ec2_transit_gateway_vpc_attachment.tokyo]
}

resource "aws_route_table" "osaka" {
  provider = aws.osaka
  vpc_id   = aws_vpc.osaka.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.osaka.id
  }
  route {
    cidr_block         = local.regions.singapore.cidr
    transit_gateway_id = aws_ec2_transit_gateway.osaka.id
  }
  tags       = { Name = "crossregiongpu-rt-osaka" }
  depends_on = [aws_ec2_transit_gateway_vpc_attachment.osaka]
}

resource "aws_route_table" "mumbai" {
  provider = aws.mumbai
  vpc_id   = aws_vpc.mumbai.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.mumbai.id
  }
  route {
    cidr_block         = local.regions.singapore.cidr
    transit_gateway_id = aws_ec2_transit_gateway.mumbai.id
  }
  tags       = { Name = "crossregiongpu-rt-mumbai" }
  depends_on = [aws_ec2_transit_gateway_vpc_attachment.mumbai]
}

resource "aws_route_table" "seoul" {
  provider = aws.seoul
  vpc_id   = aws_vpc.seoul.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.seoul.id
  }
  route {
    cidr_block         = local.regions.singapore.cidr
    transit_gateway_id = aws_ec2_transit_gateway.seoul.id
  }
  tags       = { Name = "crossregiongpu-rt-seoul" }
  depends_on = [aws_ec2_transit_gateway_vpc_attachment.seoul]
}

resource "aws_route_table_association" "singapore" {
  provider       = aws.singapore
  subnet_id      = aws_subnet.singapore.id
  route_table_id = aws_route_table.singapore.id
}

resource "aws_route_table_association" "tokyo" {
  provider       = aws.tokyo
  subnet_id      = aws_subnet.tokyo.id
  route_table_id = aws_route_table.tokyo.id
}

resource "aws_route_table_association" "osaka" {
  provider       = aws.osaka
  subnet_id      = aws_subnet.osaka.id
  route_table_id = aws_route_table.osaka.id
}

resource "aws_route_table_association" "mumbai" {
  provider       = aws.mumbai
  subnet_id      = aws_subnet.mumbai.id
  route_table_id = aws_route_table.mumbai.id
}

resource "aws_route_table_association" "seoul" {
  provider       = aws.seoul
  subnet_id      = aws_subnet.seoul.id
  route_table_id = aws_route_table.seoul.id
}

# GPU子网路由表关联
resource "aws_route_table_association" "tokyo_gpu" {
  for_each       = aws_subnet.tokyo_gpu
  provider       = aws.tokyo
  subnet_id      = each.value.id
  route_table_id = aws_route_table.tokyo.id
}

resource "aws_route_table_association" "osaka_gpu" {
  for_each       = aws_subnet.osaka_gpu
  provider       = aws.osaka
  subnet_id      = each.value.id
  route_table_id = aws_route_table.osaka.id
}

resource "aws_route_table_association" "mumbai_gpu" {
  for_each       = aws_subnet.mumbai_gpu
  provider       = aws.mumbai
  subnet_id      = each.value.id
  route_table_id = aws_route_table.mumbai.id
}

resource "aws_route_table_association" "seoul_gpu" {
  for_each       = aws_subnet.seoul_gpu
  provider       = aws.seoul
  subnet_id      = each.value.id
  route_table_id = aws_route_table.seoul.id
}

# ==================== SSH Key Pairs ====================
resource "tls_private_key" "gpu_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "local_file" "private_key" {
  content         = tls_private_key.gpu_key.private_key_pem
  filename        = "${path.module}/gpu-key.pem"
  file_permission = "0600"
}

resource "aws_key_pair" "singapore" {
  provider   = aws.singapore
  key_name   = "gpu-key-ap-southeast-1"
  public_key = tls_private_key.gpu_key.public_key_openssh
}

resource "aws_key_pair" "tokyo" {
  provider   = aws.tokyo
  key_name   = "gpu-key-ap-northeast-1"
  public_key = tls_private_key.gpu_key.public_key_openssh
}

resource "aws_key_pair" "osaka" {
  provider   = aws.osaka
  key_name   = "gpu-key-ap-northeast-3"
  public_key = tls_private_key.gpu_key.public_key_openssh
}

resource "aws_key_pair" "mumbai" {
  provider   = aws.mumbai
  key_name   = "gpu-key-ap-south-1"
  public_key = tls_private_key.gpu_key.public_key_openssh
}

resource "aws_key_pair" "seoul" {
  provider   = aws.seoul
  key_name   = "gpu-key-ap-northeast-2"
  public_key = tls_private_key.gpu_key.public_key_openssh
}
