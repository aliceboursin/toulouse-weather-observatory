output "metabase_url" {
  description = "HTTPS address of the demo Metabase."
  value       = google_cloud_run_v2_service.metabase.uri
}

output "map_bucket" {
  description = "Bucket that holds the map page."
  value       = google_storage_bucket.map.name
}

output "map_url" {
  description = "HTTPS address of the map page, once index.html is uploaded."
  value       = "https://storage.googleapis.com/${google_storage_bucket.map.name}/index.html"
}

output "bigquery_key" {
  description = "Service-account key for Metabase's BigQuery connection (JSON)."
  value       = base64decode(google_service_account_key.bigquery.private_key)
  sensitive   = true
}
