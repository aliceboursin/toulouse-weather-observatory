terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State is kept in a local file, terraform.tfstate, next to this code. It
  # holds the database password and the BigQuery key, and is ignored by Git.
}

provider "google" {
  project = var.project_id
  region  = var.region
}
