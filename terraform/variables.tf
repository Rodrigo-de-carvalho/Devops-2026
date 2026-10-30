variable "aiven_api_token" {
  description = "Token de API da Aiven (Aiven Console > Profile > Authentication tokens)"
  type        = string
  sensitive   = true
}

variable "aiven_project" {
  description = "Nome do projeto na Aiven"
  type        = string
}

variable "cloud_name" {
  description = "Região/nuvem da Aiven, ex: google-southamerica-east1, aws-sa-east-1"
  type        = string
  default     = "google-southamerica-east1"
}

variable "pg_plan" {
  description = "Plano do serviço PostgreSQL (free tier disponível em algumas contas: 'hobbyist')"
  type        = string
  default     = "hobbyist"
}

variable "service_name" {
  description = "Nome do serviço PostgreSQL"
  type        = string
  default     = "dashboard-pg"
}

variable "database_name" {
  description = "Nome do banco de dados"
  type        = string
  default     = "dashboard"
}

variable "db_username" {
  description = "Usuário de aplicação para o Postgres"
  type        = string
  default     = "dashboard_app"
}
