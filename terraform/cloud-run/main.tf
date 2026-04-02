resource "google_cloud_run_v2_service" "frontend" {
  name        = "aria-mc-server"
  location    = var.region
  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = true

  template {
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    service_account       = "minecraft-frontend-sa@${var.project_id}.iam.gserviceaccount.com"

    containers {
      image = "axiomeye/minecraft-frontend:latest"

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_ZONE"
        value = var.zone
      }
      env {
        name  = "INSTANCE_NAME"
        value = var.instance_name
      }
      env {
        name  = "GITHUB_REPO_OWNER"
        value = "axiomeye"
      }
      env {
        name  = "GITHUB_REPO_NAME"
        value = "aria-minecraft-server-iac"
      }
env {
        name  = "GH_APP_ID"
        value = var.gh_app_id
      }
      env {
        name  = "GH_APP_INSTALLATION_ID"
        value = var.gh_app_installation_id
      }
      env {
        name  = "GH_APP_PRIVATE_KEY"
        value = var.gh_app_private_key
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }
}

# Allow IAP service agent to invoke the Cloud Run service
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.frontend.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# Grant IAP access to the Google Group
resource "google_iap_web_cloud_run_service_iam_member" "allowed_group" {
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.frontend.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "group:${var.iap_group_email}"
}

data "google_project" "project" {
  project_id = var.project_id
}
