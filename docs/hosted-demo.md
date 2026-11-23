# Hosted demo on GCP

Back to the [README](../README.md).

The [public dashboard](https://metabase-demo-5oxsmpk7aa-ew.a.run.app/public/dashboard/97d4f9e8-e609-42bf-baf7-c2df8759ec05) runs on the resources created by the Terraform code in `infra/terraform/`. Commands on this page run from the repository root, in PowerShell.

## Resources

| Resource | Purpose |
|---|---|
| Cloud Run service `metabase-demo` | Metabase v0.65.6.3, with the Cloud SQL Auth Proxy as a sidecar. Stops when idle |
| Cloud SQL PostgreSQL `metabase-demo` (`db-f1-micro`) | Metabase's own data: the dashboard, questions, settings, BigQuery connection. Runs all the time |
| Storage bucket `toulouse-weather-demo-metabase-demo-map` | the map page |
| Service account `metabase-demo` | runs Metabase; read-only on BigQuery |
| 2 secrets | database password, Metabase encryption key |

Region: `europe-west1`. The BigQuery datasets `raw` and `analytics` are not managed by Terraform; the demo only reads them.

Metabase stops when nobody uses it, which is why the first page load after a pause takes 1 to 2 minutes. To keep one instance running, apply with `-var min_instances=1`; this adds a permanent Cloud Run cost.

Terraform state is the local file `infra/terraform/terraform.tfstate`. It contains the database password and the BigQuery key and is ignored by Git, as are `.terraform/`, `*.tfplan` and `*.tfvars`.

## Requirements

- Terraform 1.6 or later.
- The Google Cloud CLI, after `gcloud auth login` and `gcloud auth application-default login`.
- A GCP project with billing enabled. Its ID is the default of `project_id` in `infra/terraform/variables.tf`.
- The BigQuery views built, and `uv sync` done (see the [quick start](../README.md#quick-start)).
- Microsoft Edge, for the check in step 5.

## Deploy or update

1. Create or update the infrastructure (the database takes about 10 minutes the first time):

   ```
   terraform -chdir=infra/terraform init
   terraform -chdir=infra/terraform apply
   ```

2. Point the scripts at the hosted Metabase and map. These variables last until the terminal closes; without them the scripts target the local Metabase.

   ```
   $env:METABASE_URL = terraform -chdir=infra/terraform output -raw metabase_url
   $env:MAP_URL = terraform -chdir=infra/terraform output -raw map_url
   $env:PUBLIC_DASHBOARD = "1"
   ```

3. On a new deployment only, connect Metabase to BigQuery. This also creates the admin account, whose credentials go to `.metabase/admin-credentials-<host>.json`:

   ```
   terraform -chdir=infra/terraform output -raw bigquery_key | Out-File -Encoding ascii demo-key.json
   uv run python metabase/connect_bigquery.py demo-key.json
   Remove-Item demo-key.json
   ```

4. Generate the map page, upload it, and create or update the dashboard:

   ```
   uv run python metabase/build_map.py
   gcloud storage cp .metabase/map/index.html "gs://$(terraform -chdir=infra/terraform output -raw map_bucket)/index.html" --cache-control="no-cache, max-age=0"
   uv run python metabase/build_dashboard.py
   ```

   The last command prints the public link, which stays the same from one run to the next. Run this step again after loading new data or changing the dashboard.

5. Check the public link from a fresh browser session, with no login:

   ```
   uv run python metabase/verify_hosted.py
   ```

   It opens the link in a new Microsoft Edge profile and checks, against BigQuery:

   - the Overview page and each of its links;
   - Reliability for the whole period, the June heatwave and a November period;
   - the Status filter of Station monitoring;
   - the Forecast, Observed and Error modes of the map for a June and a November hour.

   The expected figures are those of the demo's data, 1 April to 19 November 2026.

## Access

- Anyone with the public link sees the dashboard without logging in.
- Editing requires the Metabase admin account.
- The map page is readable by anyone with its address.

## Remove the demo

```
terraform -chdir=infra/terraform destroy
```

This deletes the resources listed by `terraform -chdir=infra/terraform state list`: the Cloud Run service, the Cloud SQL instance with the hosted dashboard, the bucket with the map page, the secrets, the service account and its key. It does not touch the BigQuery datasets. The four APIs it enabled stay enabled; they cost nothing by themselves.
