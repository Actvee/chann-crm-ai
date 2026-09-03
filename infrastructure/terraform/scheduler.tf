# Cloud Scheduler — the platform's clock (Master Spec 6.7 reminders, 10
# quote expiry, 15.4 chat SLA/timeout).
#
# Until 4 Sep 2026 the three sweep endpoints existed with nothing calling
# them: the reminder digest never went out on its own, an overdue quote
# stayed "sent", and a chat conversation was only swept when a message
# or a dashboard visit happened to tick it. These jobs are that clock.
#
# Auth is the shared `X-Sweep-Secret` header the endpoints already check
# (require_scheduler) — deliberately not OIDC/service-account, per the
# reduced-security posture in CLAUDE.md §5. With the secret unset the
# endpoints refuse every call, so the jobs are harmless until it is set.

locals {
  application_url = var.enable_cloud_run_services ? google_cloud_run_v2_service.application[0].uri : ""
  sweep_jobs = {
    reminders = {
      description = "Daily follow-up digest to each owner (Master Spec 6.7)"
      schedule    = "0 8 * * *" # 08:00 Asia/Bangkok
      path        = "/api/v1/platform/reminders/sweep"
    }
    quotes-expire = {
      description = "Quotes past valid_until become expired (Master Spec 10)"
      schedule    = "30 0 * * *" # 00:30 Asia/Bangkok
      path        = "/api/v1/platform/quotes/expire-overdue"
    }
    chat-sweep = {
      description = "Live chat SLA escalation/parking and quiet-close (Master Spec 15.4)"
      schedule    = "*/5 * * * *"
      path        = "/api/v1/platform/chat/sweep"
    }
  }
}

resource "google_cloud_scheduler_job" "sweep" {
  # Not gated on the secret: a sensitive value may not shape for_each keys.
  # With the secret blank the endpoints refuse every call (require_scheduler),
  # so the jobs are inert, not insecure.
  for_each = var.enable_cloud_run_services ? local.sweep_jobs : {}

  name        = "chann-crm-ai-${var.environment}-${each.key}"
  description = each.value.description
  project     = var.project_id
  region      = var.region
  schedule    = each.value.schedule
  time_zone   = "Asia/Bangkok"

  attempt_deadline = "180s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "${local.application_url}${each.value.path}"
    headers = {
      "X-Sweep-Secret" = var.reminder_sweep_secret
      "Content-Type"   = "application/json"
    }
    body = base64encode("{}")
  }
}
