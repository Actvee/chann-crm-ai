output "reused_infrastructure" {
  description = "Proof of what is referenced rather than recreated or adopted."
  value = {
    network       = data.google_compute_network.vpc.name
    vpc_connector = data.google_vpc_access_connector.connector.name
    cloud_sql     = data.google_sql_database_instance.primary.name
    redis_host    = data.google_redis_instance.cache.host
    artifact_repo = data.google_artifact_registry_repository.images.repository_id
  }
}

output "created_application_resources" {
  value = {
    database = google_sql_database.application.name
    db_user  = google_sql_user.application.name
    bucket   = var.create_application_bucket ? google_storage_bucket.application[0].name : null
  }
}

output "cloud_run_service_urls" {
  description = "Service URLs when Cloud Run creation is enabled; public invocation remains intentionally unconfigured."
  value = var.enable_cloud_run_services ? {
    data         = google_cloud_run_v2_service.data[0].uri
    application  = google_cloud_run_v2_service.application[0].uri
    presentation = google_cloud_run_v2_service.presentation[0].uri
  } : null
}

output "public_invocation_status" {
  value = "BLOCKED_NOT_CONFIGURED_NO_IAM_SCOPE"
}
