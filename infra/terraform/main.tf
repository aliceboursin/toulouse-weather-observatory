# Temporary demo deployment of the Toulouse Data dashboard.
#
#   Cloud Run service   Metabase, with the Cloud SQL Auth Proxy as a sidecar
#   Cloud SQL           PostgreSQL holding Metabase's own data (users,
#                       questions, dashboards, the BigQuery connection)
#   Cloud Storage       the interactive map page, served over HTTPS
#
# The BigQuery datasets (raw, analytics) are not managed here. This code only
# grants read access to them, so `terraform destroy` cannot touch the data.

locals {
  apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  service = each.value
  # Leave the APIs enabled on destroy: disabling them is not needed to stop
  # the costs, and other things in the project may use them.
  disable_on_destroy = false
}

# ---------------------------------------------------------------- identity

resource "google_service_account" "metabase" {
  account_id   = var.name
  display_name = "Metabase demo"
  description  = "Runs the demo Metabase: connects to its Cloud SQL database and reads BigQuery."

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "metabase" {
  for_each = toset([
    "roles/cloudsql.client",     # connect to the Metabase database
    "roles/bigquery.dataViewer", # read the datasets
    "roles/bigquery.jobUser",    # run queries
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.metabase.member
}

# Metabase can only connect to BigQuery with a service-account key. The key
# is stored in the Terraform state and handed to metabase/connect_bigquery.py.
resource "google_service_account_key" "bigquery" {
  service_account_id = google_service_account.metabase.name
}

# ---------------------------------------------------------------- database

resource "google_sql_database_instance" "metabase" {
  name             = var.name
  region           = var.region
  database_version = "POSTGRES_16"

  # A demo: allow `terraform destroy` to delete it.
  deletion_protection = false

  settings {
    tier              = var.database_tier
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false

    backup_configuration {
      enabled = false
    }

    ip_configuration {
      # A public address with no authorised network: the only way in is the
      # Cloud SQL Auth Proxy, which authenticates with the service account.
      ipv4_enabled = true
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "metabase" {
  name     = "metabase"
  instance = google_sql_database_instance.metabase.name
}

resource "random_password" "database" {
  length  = 32
  special = false
}

resource "google_sql_user" "metabase" {
  name     = "metabase"
  instance = google_sql_database_instance.metabase.name
  password = random_password.database.result
}

# ----------------------------------------------------------------- secrets

# Metabase encrypts the BigQuery key it stores in its database with this.
resource "random_password" "encryption_key" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "secrets" {
  for_each = toset(["database-password", "encryption-key"])

  secret_id = "${var.name}-${each.value}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "database_password" {
  secret      = google_secret_manager_secret.secrets["database-password"].id
  secret_data = random_password.database.result
}

resource "google_secret_manager_secret_version" "encryption_key" {
  secret      = google_secret_manager_secret.secrets["encryption-key"].id
  secret_data = random_password.encryption_key.result
}

resource "google_secret_manager_secret_iam_member" "metabase" {
  for_each = google_secret_manager_secret.secrets

  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.metabase.member
}

# ---------------------------------------------------------------- Metabase

resource "google_cloud_run_v2_service" "metabase" {
  name     = var.name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.metabase.email
    timeout         = "300s"

    scaling {
      min_instance_count = var.min_instances
      # One instance is enough for a demo and keeps the database load low.
      max_instance_count = 1
    }

    containers {
      name  = "metabase"
      image = var.metabase_image

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        # Only pay for CPU while a request is being served.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "MB_DB_TYPE"
        value = "postgres"
      }
      env {
        # The proxy sidecar listens here.
        name  = "MB_DB_HOST"
        value = "127.0.0.1"
      }
      env {
        name  = "MB_DB_PORT"
        value = "5432"
      }
      env {
        name  = "MB_DB_DBNAME"
        value = google_sql_database.metabase.name
      }
      env {
        name  = "MB_DB_USER"
        value = google_sql_user.metabase.name
      }
      env {
        name = "MB_DB_PASS"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["database-password"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "MB_ENCRYPTION_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["encryption-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "MB_ANON_TRACKING_ENABLED"
        value = "false"
      }
      env {
        name  = "MB_CHECK_FOR_UPDATES"
        value = "false"
      }

      # Metabase takes 1 to 2 minutes to start, longer the first time, when
      # it creates its tables.
      startup_probe {
        initial_delay_seconds = 60
        period_seconds        = 20
        failure_threshold     = 12
        timeout_seconds       = 5
        http_get {
          path = "/api/health"
        }
      }

      depends_on = ["cloud-sql-proxy"]
    }

    containers {
      name  = "cloud-sql-proxy"
      image = var.cloud_sql_proxy_image
      args = [
        "--port=5432",
        "--health-check",
        "--http-address=0.0.0.0",
        "--http-port=9090",
        google_sql_database_instance.metabase.connection_name,
      ]

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }

      startup_probe {
        period_seconds    = 5
        failure_threshold = 12
        http_get {
          path = "/startup"
          port = 9090
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.metabase,
    google_secret_manager_secret_iam_member.metabase,
    google_secret_manager_secret_version.database_password,
    google_secret_manager_secret_version.encryption_key,
  ]
}

# Anyone with the URL can reach Metabase's login page. Seeing a dashboard
# still requires a Metabase account.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.metabase.name
  location = google_cloud_run_v2_service.metabase.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --------------------------------------------------------------------- map

resource "google_storage_bucket" "map" {
  name     = "${var.project_id}-${var.name}-map"
  location = var.region

  uniform_bucket_level_access = true
  # Lets `terraform destroy` remove the bucket with the map page in it.
  force_destroy = true
}

# The map page is loaded by the viewer's browser inside the dashboard, so it
# must be readable without a Google login. It only contains the temperature
# snapshot, which comes from public open data.
resource "google_storage_bucket_iam_member" "map_public" {
  bucket = google_storage_bucket.map.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
