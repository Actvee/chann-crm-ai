resource "google_storage_bucket" "application" {
  count = var.create_application_bucket ? 1 : 0

  name                        = var.application_bucket_name
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.application_bucket_name != ""
      error_message = "application_bucket_name is required when create_application_bucket is true."
    }
  }
}
