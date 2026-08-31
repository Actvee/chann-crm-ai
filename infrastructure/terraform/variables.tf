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

variable "application_public_base_url" {
  type        = string
  description = "The Application tier's own externally reachable base URL, used to build document links sent into LINE. Empty means such links are omitted rather than emitted broken."
  default     = ""
}

variable "reminder_sweep_secret" {
  type        = string
  description = "Static machine-to-machine secret for Cloud Scheduler to call POST /api/v1/platform/reminders/sweep. Deliberately not the platform-admin JWT/session flow — see application/chann_app/routers_admin.py:require_scheduler for why."
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

variable "line_login_channel_id" {
  type        = string
  description = "Expected LINE Login channel ID (ID-token audience), not a full LIFF app ID."
  sensitive   = true
  default     = ""
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

variable "openrouter_model_reasoning" {
  type        = string
  default     = ""
  description = "Reasoning-tier model slug (thinking ON) for ad-hoc reports. Phase 17 uses it; empty is fine until then."
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

# Phase 10 — SmartBrowz OAuth token refresh (application/chann_app/services/
# smartbrowz_auth.py). All default to "" — the automatic-refresh code
# raises a clear SmartBrowzAuthError rather than silently doing nothing
# when they're unset, so an empty default here is safe: nothing that
# needs SmartBrowz has been built into the runtime path yet (see
# docs/SESSION_HANDOFF.md), only the token-refresh mechanism itself.
variable "smartbrowz_accounts_url" {
  type        = string
  description = "Datacenter-specific Zoho accounts host for the OAuth token exchange (e.g. https://accounts.zoho.com, https://accounts.zoho.eu) — must match wherever the Catalyst project actually lives; the wrong one rejects the refresh_token outright."
  default     = "https://accounts.zoho.com"
}

variable "smartbrowz_client_id" {
  type        = string
  description = "From the Catalyst API Console's Self Client."
  sensitive   = true
  default     = ""
}

variable "smartbrowz_client_secret" {
  type        = string
  description = "From the Catalyst API Console's Self Client."
  sensitive   = true
  default     = ""
}

variable "smartbrowz_refresh_token" {
  type        = string
  description = "One-time grant token already exchanged for a refresh_token via the Self Client's Generate Code flow. The long-lived secret. Used directly by the zcatalyst-sdk's own RefreshTokenCredential (which handles its own per-instance access-token caching/refresh) — not the Data-tier cache SmartBrowzTokenManager uses, a separate, lower-level utility kept for now but not wired into the actual render adapter."
  sensitive   = true
  default     = ""
}

# Phase 10 — the rest of what zcatalyst-sdk's ICatalystOptions requires to
# initialize from OUTSIDE Catalyst (confirmed via Zoho's own "Integrate SDK
# in Third-Party Apps" doc, and by inspecting
# zcatalyst_sdk.types.ICatalystOptions.__required_keys__ directly): project
# ID, ZAID ("project_key" in the SDK's own naming — a separate per-
# environment identifier, NOT the same as project_id, obtained from
# Project Settings -> Environments -> General in the Catalyst console),
# API domain, and environment (Development/Production).
variable "catalyst_project_id" {
  type        = string
  description = "Catalyst project ID (Project Settings -> Environments -> General)."
  default     = ""
}

variable "catalyst_zaid" {
  type        = string
  description = "ZAID (Zoho Account ID) — a per-environment identifier distinct from project_id; use the Development environment's ZAID to match catalyst_environment's default."
  default     = ""
}

variable "catalyst_api_domain" {
  type        = string
  description = "Catalyst's own API domain (distinct from smartbrowz_accounts_url, which is Zoho Accounts' OAuth token endpoint, not Catalyst's API)."
  default     = "https://api.catalyst.zoho.com"
}

variable "catalyst_environment" {
  type        = string
  description = "Development or Production — must match which environment's ZAID (catalyst_zaid) was provided."
  default     = "Development"
}
