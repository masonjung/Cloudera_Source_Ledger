# Source Ledger — CDP stack as code.
#
# The declarative equivalent of infra/cdp/provision.sh. Use whichever fits the
# customer: the shell script is easier to read in a workshop, Terraform is what
# survives contact with a platform team.
#
#   terraform init
#   terraform plan     # → what make provision dry-runs
#   terraform apply
#
# Environment creation takes roughly an hour. That is CDP, not Terraform.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cdp = {
      source  = "cloudera/cdp"
      version = "~> 0.6"
    }
  }

  # State must not live on a laptop. Uncomment and point at the team's bucket
  # before anyone else runs this.
  # backend "s3" {
  #   bucket = "source-ledger-terraform-state"
  #   key    = "cdp/terraform.tfstate"
  #   region = "us-west-2"
  # }
}

provider "cdp" {
  # Reads ~/.cdp/credentials, the same profile the CDP CLI uses.
}

locals {
  datalake_name = "${var.env_name}-dl"
  cde_service   = "${var.env_name}-cde"
  ai_workbench  = "${var.env_name}-ai"
}

# --- Foundation ------------------------------------------------------------
# Identity, storage, and SDX. Everything else attaches to this.

resource "cdp_environments_aws_environment" "source_ledger" {
  environment_name = var.env_name
  credential_name  = var.credential_name
  region           = var.region

  security_access = {
    cidr = join(",", var.ingress_cidrs)
  }

  log_storage = {
    storage_location_base = "${var.storage_base}/logs"
  }

  tags = var.tags
}

resource "cdp_datalake_aws_datalake" "source_ledger" {
  datalake_name    = local.datalake_name
  environment_name = cdp_environments_aws_environment.source_ledger.environment_name
  scale            = var.datalake_scale

  cloud_provider_configuration = {
    storage_bucket_location = "${var.storage_base}/datalake"
  }

  tags = var.tags
}

# --- Process layer ---------------------------------------------------------
# Runs data/ingest/load_to_iceberg.py and pipelines/jobs/url_enrichment.py.

resource "cdp_de_service" "source_ledger" {
  name        = local.cde_service
  environment = cdp_environments_aws_environment.source_ledger.environment_name

  instance_type     = var.cde_instance_type
  minimum_instances = 0 # an idle cluster should cost nothing
  maximum_instances = var.cde_max_instances
  initial_instances = 1

  enable_workload_analytics = true
  tags                      = var.tags

  # CDE cannot come up until SDX is ready, and the dependency is not implied by
  # any attribute reference above.
  depends_on = [cdp_datalake_aws_datalake.source_ledger]
}

resource "cdp_de_virtual_cluster" "source_ledger" {
  name       = "source-ledger-vc"
  cluster_id = cdp_de_service.source_ledger.cluster_id

  cpu_requests    = "4"
  memory_requests = "16Gi"
  spark_version   = "SPARK3"
}

# --- AI + Serve layers -----------------------------------------------------
# Hosts retrieval/notebooks/ as sessions and app/ as a long-running Application.

resource "cdp_ml_workspace" "source_ledger" {
  workspace_name   = local.ai_workbench
  environment_name = cdp_environments_aws_environment.source_ledger.environment_name

  instance_type = var.ai_instance_type
  min_instances = 1
  max_instances = 3

  # Required for Atlas lineage on the AI layer — see governance/README.md.
  enable_governance = true

  tags = var.tags

  depends_on = [cdp_datalake_aws_datalake.source_ledger]
}

# --- Outputs ---------------------------------------------------------------
# Consumed by .cicd/deploy.sh, which needs the endpoints but should not have to
# know how they were created.

output "environment_crn" {
  description = "CRN of the CDP environment."
  value       = cdp_environments_aws_environment.source_ledger.crn
}

output "cde_virtual_cluster_id" {
  description = "Target for `cde job import` in .cicd/deploy.sh."
  value       = cdp_de_virtual_cluster.source_ledger.vc_id
}

output "ai_workbench_url" {
  description = "Where the Serve layer is published."
  value       = cdp_ml_workspace.source_ledger.workspace_url
}

output "iceberg_warehouse" {
  description = "Warehouse path backing the tables in data/iceberg/ddl.sql."
  value       = "${var.storage_base}/datalake/warehouse/tablespace/external/hive"
}
