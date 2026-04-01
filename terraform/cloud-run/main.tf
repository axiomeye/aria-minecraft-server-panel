resource "google_service_account" "frontend_sa" {
  account_id   = "minecraft-frontend-sa"
  display_name = "Cloud Run Frontend Service Account"
}

resource "google_cloud_run_v2_service" "frontend" {
  name     = "aria-mc-server"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # Native IAP integration
  # iap_enabled = true

  template {
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    service_account       = google_service_account.frontend_sa.email

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
        name  = "ALLOWED_EMAILS"
        value = var.allowed_emails
      }
      env {
        name  = "FLASK_SECRET_KEY"
        value = var.flask_secret_key
      }
      env {
        name  = "GOOGLE_CLIENT_ID"
        value = var.google_client_id
      }
      env {
        name  = "GOOGLE_CLIENT_SECRET"
        value = var.google_client_secret
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
