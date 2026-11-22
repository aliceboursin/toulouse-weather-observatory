variable "project_id" {
  description = "GCP project that already holds the BigQuery data."
  type        = string
  default     = "toulouse-weather-demo"
}

variable "region" {
  description = "Region for Cloud Run and Cloud SQL."
  type        = string
  default     = "europe-west1"
}

variable "name" {
  description = "Prefix for every resource of the demo, so it can be found and removed as a whole."
  type        = string
  default     = "metabase-demo"
}

variable "metabase_image" {
  description = "Metabase image, pinned to the version used locally."
  type        = string
  default     = "docker.io/metabase/metabase:v0.65.6.3"
}

variable "cloud_sql_proxy_image" {
  description = "Cloud SQL Auth Proxy image, run next to Metabase."
  type        = string
  default     = "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.28.0"
}

variable "database_tier" {
  description = "Cloud SQL machine type. db-f1-micro is the smallest shared-core tier."
  type        = string
  default     = "db-f1-micro"
}

variable "min_instances" {
  description = "Metabase instances kept running. 0 stops it when idle (first page load then takes 1 to 2 minutes); 1 keeps it warm."
  type        = number
  default     = 0
}
