output "pg_service_uri" {
  description = "URI de conexão completa para o PostgreSQL na Aiven"
  value       = aiven_pg.dashboard_db.service_uri
  sensitive   = true
}

output "pg_host" {
  description = "Host do PostgreSQL"
  value       = aiven_pg.dashboard_db.service_host
}

output "pg_port" {
  description = "Porta do PostgreSQL"
  value       = aiven_pg.dashboard_db.service_port
}

output "pg_user" {
  description = "Usuário da aplicação"
  value       = aiven_pg_user.app_user.username
}

output "pg_password" {
  description = "Senha do usuário da aplicação"
  value       = aiven_pg_user.app_user.password
  sensitive   = true
}
