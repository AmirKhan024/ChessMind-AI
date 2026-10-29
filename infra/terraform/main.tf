# One EC2 VM running the same docker compose stack as local, behind Caddy (HTTPS).
# Why a VM and not ECS Fargate: the stack is already compose; Stockfish needs steady CPU,
# which is cheapest as a plain instance; one box keeps cost and moving parts minimal.
# No SSH: shell access is through SSM Session Manager. The Groq key lives in SSM Parameter
# Store (SecureString) created by hand, so it never enters Terraform state or the repo.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "chessmind-ai" }
  }
}

data "aws_caller_identity" "me" {}

data "aws_vpc" "default" {
  default = true
}

# Ubuntu 24.04 LTS, x86_64 (the Stockfish binary in the image is x86-64).
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_security_group" "web" {
  name        = "chessmind-web"
  description = "HTTP/HTTPS in; everything out"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (Caddy redirects to HTTPS and answers ACME challenges)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "vm" {
  name = "chessmind-vm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.vm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read only the app's own parameters (the Groq key).
resource "aws_iam_role_policy" "params" {
  name = "chessmind-read-params"
  role = aws_iam_role.vm.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter/chessmind/*"
    }]
  })
}

resource "aws_iam_instance_profile" "vm" {
  name = "chessmind-vm"
  role = aws_iam_role.vm.name
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.vm.name
  vpc_security_group_ids = [aws_security_group.web.id]

  root_block_device {
    volume_size = var.disk_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region           = var.region
    repo_url         = var.repo_url
    repo_ref         = var.repo_ref
    analysis_workers = var.analysis_workers
    games_cap        = var.games_cap
    # sslip.io resolves <ip>.sslip.io to <ip>, so Caddy can get a real certificate
    # without buying a domain.
    hostname = var.hostname != "" ? var.hostname : "${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"
  })
  user_data_replace_on_change = true

  tags = { Name = "chessmind-app" }
}

# Stable public IP, so the sslip.io hostname and certificate survive instance replacement.
resource "aws_eip" "app" {
  domain = "vpc"
}

resource "aws_eip_association" "app" {
  instance_id   = aws_instance.app.id
  allocation_id = aws_eip.app.id
}

# Billing alarm: email when actual or forecast monthly spend passes the limit.
resource "aws_budgets_budget" "monthly" {
  name         = "chessmind-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
