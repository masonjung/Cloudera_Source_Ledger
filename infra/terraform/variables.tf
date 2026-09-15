# Source Ledger — input variables for the CDP stack.
#
# Defaults describe a small demo footprint. Size up from measured load, not from
# optimism — see pipelines/README.md.

variable "env_name" {
  description = "CDP environment name. Prefixes every resource created here."
  type        = string
  default     = "source-ledger-dev"

  validation {
    # CDP environment names surface in DNS entries and resource ARNs.
    condition     = can(regex("^[a-z][a-z0-9-]{2,27}$", var.env_name))
    error_message = "env_name must be 3-28 chars, lowercase alphanumeric or hyphen, starting with a letter."
  }
}

variable "region" {
  description = "Cloud region for the environment and its storage."
  type        = string
  default     = "us-west-2"
}

variable "credential_name" {
  description = "Pre-existing CDP cross-account credential. Created once per cloud account, outside this stack."
  type        = string
  default     = "source-ledger-cross-account"
}

variable "storage_base" {
  description = "Base storage location for the data lake, logs, and Iceberg warehouse."
  type        = string
  default     = "s3a://source-ledger-dev"
}

variable "datalake_scale" {
  description = "Data Lake sizing. LIGHT_DUTY is correct for Source Ledger's volume; MEDIUM_DUTY_HA only when an SLA requires it."
  type        = string
  default     = "LIGHT_DUTY"

  validation {
    condition     = contains(["LIGHT_DUTY", "MEDIUM_DUTY_HA"], var.datalake_scale)
    error_message = "datalake_scale must be LIGHT_DUTY or MEDIUM_DUTY_HA."
  }
}

variable "cde_instance_type" {
  description = "Instance type for Data Engineering workers."
  type        = string
  default     = "m5.2xlarge"
}

variable "cde_max_instances" {
  description = "Autoscaling ceiling for CDE. Minimum is 0 so an idle cluster costs nothing."
  type        = number
  default     = 4
}

variable "ai_instance_type" {
  description = "Instance type for the AI Workbench hosting the Serve layer."
  type        = string
  default     = "m5.xlarge"
}

variable "ingress_cidrs" {
  description = "CIDR ranges permitted to reach the environment endpoints. The default is open and MUST be narrowed before any customer deployment."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "tags" {
  description = "Tags applied to every resource. Cost attribution depends on these."
  type        = map(string)
  default = {
    accelerator = "source_ledger"
    owner       = "cloudera-forge"
    managed_by  = "terraform"
  }
}
