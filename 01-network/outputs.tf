output "vpc_ids" {
  value = {
    singapore = aws_vpc.singapore.id
    tokyo     = aws_vpc.tokyo.id
    osaka     = aws_vpc.osaka.id
    mumbai    = aws_vpc.mumbai.id
    seoul     = aws_vpc.seoul.id
  }
}

output "subnet_ids" {
  value = {
    singapore = aws_subnet.singapore.id
    tokyo     = aws_subnet.tokyo.id
    osaka     = aws_subnet.osaka.id
    mumbai    = aws_subnet.mumbai.id
    seoul     = aws_subnet.seoul.id
  }
}

output "gpu_subnet_ids" {
  value = {
    tokyo  = { for az, subnet in aws_subnet.tokyo_gpu : az => subnet.id }
    mumbai = { for az, subnet in aws_subnet.mumbai_gpu : az => subnet.id }
    seoul  = { for az, subnet in aws_subnet.seoul_gpu : az => subnet.id }
  }
}

output "tgw_ids" {
  value = {
    singapore = aws_ec2_transit_gateway.singapore.id
    tokyo     = aws_ec2_transit_gateway.tokyo.id
    osaka     = aws_ec2_transit_gateway.osaka.id
    mumbai    = aws_ec2_transit_gateway.mumbai.id
    seoul     = aws_ec2_transit_gateway.seoul.id
  }
}

output "route_table_ids" {
  value = {
    singapore = aws_route_table.singapore.id
    tokyo     = aws_route_table.tokyo.id
    osaka     = aws_route_table.osaka.id
    mumbai    = aws_route_table.mumbai.id
    seoul     = aws_route_table.seoul.id
  }
}

output "key_names" {
  value = {
    singapore = aws_key_pair.singapore.key_name
    tokyo     = aws_key_pair.tokyo.key_name
    osaka     = aws_key_pair.osaka.key_name
    mumbai    = aws_key_pair.mumbai.key_name
    seoul     = aws_key_pair.seoul.key_name
  }
}
