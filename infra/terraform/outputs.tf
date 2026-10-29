output "url" {
  value = "https://${var.hostname != "" ? var.hostname : "${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"}"
}

output "instance_id" {
  value = aws_instance.app.id
}

output "grafana_tunnel" {
  description = "Grafana is not public; forward it over SSM"
  value       = "aws ssm start-session --target ${aws_instance.app.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=3000,localPortNumber=3000"
}
