variable "region" {
  type    = string
  default = "us-east-1"
}

# c7i-flex.large: 2 vCPU / 4 GiB, free-tier eligible for AWS accounts created after
# 2025-07-15 (paid from credits). Analysis speed scales with vCPUs; see docs/runbook.md.
variable "instance_type" {
  type    = string
  default = "c7i-flex.large"
}

variable "disk_gb" {
  type    = number
  default = 30
}

variable "analysis_workers" {
  type    = number
  default = 2 # one single-thread Stockfish per vCPU
}

variable "games_cap" {
  type    = number
  default = 100 # 300 games on 2 vCPUs takes too long for a demo; the early report helps too
}

variable "repo_url" {
  type    = string
  default = "https://github.com/AmirKhan024/ChessMind-AI.git"
}

variable "repo_ref" {
  type    = string
  default = "main"
}

variable "hostname" {
  type        = string
  default     = ""
  description = "Public hostname; empty = <elastic-ip>.sslip.io"
}

variable "monthly_budget_usd" {
  type    = string
  default = "15"
}

variable "alert_email" {
  type        = string
  description = "Where budget alerts go (AWS sends a confirmation email first)"
}
