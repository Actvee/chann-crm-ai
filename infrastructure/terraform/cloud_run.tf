locals {
  service_names = {
    data         = "chann-crm-ai-${var.environment}-data"
    application  = "chann-crm-ai-${var.environment}-application"
    presentation = "chann-crm-ai-${var.environment}-presentation"
  }

  common_runtime_env = {
    APP_ENV          = var.environment
    PLATFORM_VERSION = var.platform_version
    GIT_COMMIT       = var.git_commit
  }

  data_runtime_env = merge(local.common_runtime_env, {
    DATABASE_URL = "postgresql+psycopg://${urlencode(google_sql_user.application.name)}:${urlencode(var.database_password)}@${data.google_sql_database_instance.primary.private_ip_address}:5432/${google_sql_database.application.name}"
    REDIS_URL    = "redis://${data.google_redis_instance.cache.host}:${data.google_redis_instance.cache.port}/0"
    ADMIN_SECRET = var.admin_secret
  })

  application_runtime_env = merge(local.common_runtime_env, {
    DATA_BASE_URL                        = var.enable_cloud_run_services ? google_cloud_run_v2_service.data[0].uri : ""
    ADMIN_SECRET                         = var.admin_secret
    JWT_SECRET                           = var.jwt_secret
    LINE_CUSTOMER_CHANNEL_SECRET         = var.line_credentials.customer.channel_secret
    LINE_CUSTOMER_CHANNEL_ACCESS_TOKEN   = var.line_credentials.customer.channel_access_token
    LINE_SALES_CHANNEL_SECRET            = var.line_credentials.sales.channel_secret
    LINE_SALES_CHANNEL_ACCESS_TOKEN      = var.line_credentials.sales.channel_access_token
    LINE_TECHNICIAN_CHANNEL_SECRET       = var.line_credentials.technician.channel_secret
    LINE_TECHNICIAN_CHANNEL_ACCESS_TOKEN = var.line_credentials.technician.channel_access_token
    LINE_LOGIN_CHANNEL_ID                = var.line_login_channel_id
    OPENROUTER_API_KEY                   = var.openrouter_api_key
    OPENROUTER_MODEL                     = var.openrouter_model
  })

  presentation_runtime_env = merge(local.common_runtime_env, {
    APPLICATION_BASE_URL           = var.enable_cloud_run_services ? google_cloud_run_v2_service.application[0].uri : ""
    NEXT_PUBLIC_LIFF_CUSTOMER_ID   = var.liff_ids.customer
    NEXT_PUBLIC_LIFF_SALES_ID      = var.liff_ids.sales
    NEXT_PUBLIC_LIFF_TECHNICIAN_ID = var.liff_ids.technician
  })

  required_image_tiers   = toset(["data", "application", "presentation"])
  configured_image_tiers = toset(keys(var.image_digests))
}

# No IAM policy, Service Account, or Secret Manager resource is declared here.
# The default remains authenticated. DEV may explicitly use the approved
# reduced-security Invoker-IAM-check exception; a lifecycle precondition blocks
# that exception outside DEV. Application-layer authentication stays required.

resource "google_cloud_run_v2_service" "data" {
  count = var.enable_cloud_run_services ? 1 : 0

  name                 = local.service_names.data
  location             = var.region
  project              = var.project_id
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = var.dev_reduced_security_disable_invoker_iam_check
  deletion_protection  = true

  template {
    max_instance_request_concurrency = 80
    timeout                          = "300s"

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      connector = data.google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image_digests["data"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "env" {
        for_each = nonsensitive(toset(keys(local.data_runtime_env)))
        content {
          name  = env.value
          value = local.data_runtime_env[env.value]
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(setsubtract(local.required_image_tiers, local.configured_image_tiers)) == 0
      error_message = "image_digests must contain data, application, and presentation digest-pinned images."
    }
    precondition {
      condition     = data.google_sql_database_instance.primary.private_ip_address != ""
      error_message = "The reused Cloud SQL instance must expose a private IP for connector-based access."
    }
    precondition {
      condition     = var.platform_version != "" && var.git_commit != ""
      error_message = "platform_version and the full git_commit are required for build-once release identity."
    }
    precondition {
      condition     = var.cloud_run_min_instances <= var.cloud_run_max_instances
      error_message = "cloud_run_min_instances cannot exceed cloud_run_max_instances."
    }
    precondition {
      condition     = !var.dev_reduced_security_disable_invoker_iam_check || var.environment == "dev"
      error_message = "Disabling the Cloud Run Invoker IAM check is an approved DEV-only reduced-security exception."
    }
  }
}

resource "google_cloud_run_v2_service" "application" {
  count = var.enable_cloud_run_services ? 1 : 0

  name                 = local.service_names.application
  location             = var.region
  project              = var.project_id
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = var.dev_reduced_security_disable_invoker_iam_check
  deletion_protection  = true

  template {
    max_instance_request_concurrency = 80
    timeout                          = "300s"

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      connector = data.google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image_digests["application"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "env" {
        for_each = nonsensitive(toset(keys(local.application_runtime_env)))
        content {
          name  = env.value
          value = local.application_runtime_env[env.value]
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = alltrue([
        length(var.admin_secret) >= 32,
        length(var.jwt_secret) >= 32,
        alltrue(flatten([
          for _, credentials in var.line_credentials : [
            credentials.channel_secret != "",
            credentials.channel_access_token != "",
          ]
        ])),
        var.line_login_channel_id != "",
      ])
      error_message = "Application runtime requires ADMIN/JWT secrets, all three LINE credentials, and the LINE Login channel ID."
    }
  }

  depends_on = [google_cloud_run_v2_service.data]
}

resource "google_cloud_run_v2_service" "presentation" {
  count = var.enable_cloud_run_services ? 1 : 0

  name                 = local.service_names.presentation
  location             = var.region
  project              = var.project_id
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = var.dev_reduced_security_disable_invoker_iam_check
  deletion_protection  = true

  template {
    max_instance_request_concurrency = 80
    timeout                          = "300s"

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      connector = data.google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image_digests["presentation"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "env" {
        for_each = nonsensitive(toset(keys(local.presentation_runtime_env)))
        content {
          name  = env.value
          value = local.presentation_runtime_env[env.value]
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = alltrue([for _, id in var.liff_ids : id != ""])
      error_message = "Presentation runtime requires all three LIFF IDs."
    }
  }

  depends_on = [google_cloud_run_v2_service.application]
}
