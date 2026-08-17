terraform {
  required_version = ">= 1.6"
  backend "gcs" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# EXISTING INFRASTRUCTURE — read only.
#
# These are `data`, not `resource`, deliberately. If this configuration is
# ever destroyed by accident, the tenant databases survive. Importing them
# would put Production Cloud SQL one `terraform destroy` away from deletion,
# which is not a risk worth taking for the convenience of managing settings
# we do not intend to change.
# ---------------------------------------------------------------------------

data "google_compute_network" "vpc" {
  name    = var.network_name
  project = var.project_id
}

data "google_vpc_access_connector" "connector" {
  name    = var.vpc_connector_name
  region  = var.region
  project = var.project_id
}

data "google_sql_database_instance" "primary" {
  name    = var.cloud_sql_instance
  project = var.project_id
}

data "google_redis_instance" "cache" {
  name    = var.redis_instance
  region  = var.region
  project = var.project_id
}

data "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.artifact_repository
  project       = var.project_id
}

# ---------------------------------------------------------------------------
# NEW RESOURCES — genuinely ours.
#
# The application database is created here; the old `chann1` database was
# removed by intent and nothing is migrated from it.
# ---------------------------------------------------------------------------

resource "google_sql_database" "application" {
  name            = var.application_database_name
  instance        = data.google_sql_database_instance.primary.name
  project         = var.project_id
  deletion_policy = "ABANDON"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_sql_user" "application" {
  name            = var.application_database_user
  instance        = data.google_sql_database_instance.primary.name
  project         = var.project_id
  password        = var.database_password
  deletion_policy = "ABANDON"

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(var.database_password) >= 24
      error_message = "database_password must contain at least 24 characters. Never commit it."
    }
  }
}
