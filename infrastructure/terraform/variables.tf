variable "project_id" {
  type        = string
  description = "GCP project. Reference only; Terraform does not manage the project."
}

variable "region" {
  type    = string
  default = "asia-southeast1"
}

variable "environment" {
  type        = string
  description = "dev | stage | production"
  validation {
    condition     = contains(["dev", "stage", "production"], var.environment)
    error_message = "environment must be dev, stage, or production."
  }
}

# No defaults: these come from live preflight, never from stale documentation.
variable "network_name" {
  type        = string
  description = "Existing VPC network name — VERIFY_WITH_PREFLIGHT"
}

variable "vpc_connector_name" {
  type        = string
  description = "Existing Serverless VPC Access connector — VERIFY_WITH_PREFLIGHT"
}

variable "cloud_sql_instance" {
  type        = string
  description = "Existing Cloud SQL instance to reuse"
}

variable "redis_instance" {
  type        = string
  description = "Existing Memorystore instance to reuse"
}

variable "application_database_name" {
  type    = string
  default = "chann_crm_ai"
}

variable "application_database_user" {
  type    = string
  default = "chann_crm_ai_app"
}

variable "database_password" {
  type        = string
  description = "Application PostgreSQL password. Required at plan time; never commit it."
  sensitive   = true
}

variable "artifact_repository" {
  type        = string
  description = "Existing Artifact Registry repository"
}

variable "image_digests" {
  type        = map(string)
  description = "tier => image pinned by digest. Tags are rejected; a tag can be repointed after Stage proved it."
  default     = {}
  validation {
    condition     = alltrue([for _, v in var.image_digests : can(regex("@sha256:[0-9a-f]{64}$", v))])
    error_message = "Images must end with a full lowercase 64-hex digest (@sha256:...), never a tag."
  }
}

variable "enable_cloud_run_services" {
  type        = bool
  description = "Create the three application-tier Cloud Run services. Invocation behavior is controlled by a separate default-off DEV exception."
  default     = false
}

variable "dev_reduced_security_disable_invoker_iam_check" {
  type        = bool
  description = "DEV-only approved exception: disable the Cloud Run Invoker IAM check and rely on application-layer controls. Must remain false outside DEV."
  default     = false
}

variable "cloud_run_min_instances" {
  type    = number
  default = 0
  validation {
    condition     = var.cloud_run_min_instances >= 0
    error_message = "cloud_run_min_instances must be zero or greater."
  }
}

variable "cloud_run_max_instances" {
  type    = number
  default = 2
  validation {
    condition     = var.cloud_run_max_instances >= 1
    error_message = "cloud_run_max_instances must be at least one."
  }
}

variable "platform_version" {
  type        = string
  description = "Immutable Chann CRM AI release version."
  default     = ""
}

variable "git_commit" {
  type        = string
  description = "Full source commit SHA represented by the promoted images."
  default     = ""
  validation {
    condition     = var.git_commit == "" || can(regex("^[0-9a-f]{40}$", var.git_commit))
    error_message = "git_commit must be empty or a full 40-character lowercase hexadecimal SHA."
  }
}

variable "admin_secret" {
  type        = string
  description = "Shared Application-to-Data/admin secret for the approved reduced-security posture."
  sensitive   = true
  default     = ""
}

variable "jwt_secret" {
  type        = string
  description = "Platform Admin JWT signing secret."
  sensitive   = true
  default     = ""
}

variable "line_credentials" {
  type = map(object({
    channel_secret       = string
    channel_access_token = string
  }))
  description = "Credentials for exactly customer, sales, and technician LINE OAs."
  sensitive   = true
  default = {
    customer   = { channel_secret = "", channel_access_token = "" }
    sales      = { channel_secret = "", channel_access_token = "" }
    technician = { channel_secret = "", channel_access_token = "" }
  }
  validation {
    condition     = toset(keys(var.line_credentials)) == toset(["customer", "sales", "technician"])
    error_message = "line_credentials must contain exactly customer, sales, and technician."
  }
}

variable "liff_ids" {
  type = object({
    customer   = string
    sales      = string
    technician = string
  })
  description = "LIFF IDs for the three locked audiences. Treat as deployment-sensitive so plan evidence cannot expose them."
  sensitive   = true
  default = {
    customer   = ""
    sales      = ""
    technician = ""
  }
}

variable "openrouter_api_key" {
  type        = string
  description = "Optional in Phase 1; required when the Phase 4 AI path is enabled."
  sensitive   = true
  default     = ""
}

variable "openrouter_model" {
  type        = string
  description = "Optional in Phase 1; default model selector is locked during Phase 4."
  default     = ""
}

variable "create_application_bucket" {
  type        = bool
  description = "Create the private/versioned application bucket only when file features require it and preflight proves it absent."
  default     = false
}

variable "application_bucket_name" {
  type        = string
  description = "Globally unique bucket name, required only when create_application_bucket is true."
  default     = ""
}
