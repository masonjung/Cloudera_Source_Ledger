# `infra/` — Infrastructure as code

One-time platform provisioning. Everything the accelerator needs that is not
application code: the CDP environment, SDX, Data Engineering, the AI Workbench, and
the Iceberg database.

**This runs once per environment.** `make deploy` skips it when the stack already
exists — provisioning and shipping are different operations with very different blast
radii, and conflating them is how someone recreates a Data Lake during a demo.

## What's here

| Path | What it is |
|---|---|
| `cdp/provision.sh` | CDP CLI provisioning, dry-run by default |
| `terraform/main.tf` | The same stack declaratively — environment, Data Lake, CDE, AI Workbench |
| `terraform/variables.tf` | Inputs, with validation and demo-sized defaults |

## Two paths, same stack

The shell script and the Terraform config provision the same thing. Pick by
audience, not by preference:

- **`cdp/provision.sh`** — readable top to bottom, prints every command before
  running it, no state file to manage. Right for a workshop, a first environment, or
  anyone who wants to see exactly what CDP is being asked to do.
- **`terraform/`** — has state, plans, and drift detection. Right for anything a
  platform team will own. Uncomment the S3 backend before a second person runs it;
  state on a laptop is not state.

Do not run both against one environment.

## Run it

```bash
make provision                              # dry run — prints every CDP CLI call
./infra/cdp/provision.sh --execute          # provision for real

cd infra/terraform && terraform init && terraform plan    # the declarative path
```

Configure with environment variables (`CDP_ENV`, `CDP_REGION`, `CDP_CREDENTIAL`,
`STORAGE_BASE`) or `terraform.tfvars`. The CDP cross-account credential is **not**
created here — it is a per-cloud-account prerequisite, created once with
`cdp environments create-aws-credential`.

## What gets created

```
CDP Environment  (identity, networking, SDX)
  └── Data Lake                    → governance, Ranger policies, Atlas lineage
  └── Data Engineering service     → runs data/ingest/ and pipelines/jobs/
        └── virtual cluster "source-ledger-vc"
  └── AI Workbench                 → hosts retrieval/notebooks/ and app/ as an Application
  └── Iceberg database "source_ledger"     → from data/iceberg/ddl.sql
```

Environment creation takes roughly 60 minutes. That is CDP, not the tooling.

## Conventions

- **Dry run is the default.** Both paths show you the plan first. `--execute` and
  `terraform apply` are the only ways to change anything.
- **Re-runnable.** The script checks for an existing environment before creating one,
  and the Iceberg DDL is entirely `IF NOT EXISTS`. Safe against a half-provisioned
  environment.
- **Scale to zero.** CDE `minimum_instances = 0` so an idle accelerator costs
  nothing between demos. This matters more than peak sizing for something that runs
  a handful of jobs a day.
- **Tag everything.** The `tags` variable is applied to every resource; cost
  attribution depends on it.
- **Secrets are not here.** No credentials, keys, or endpoints in this directory.
  CDP auth comes from `~/.cdp/credentials`; CI reads masked GitLab variables.

## Before a customer deployment

`ingress_cidrs` defaults to `0.0.0.0/0`. That is fine for a sandbox and wrong
everywhere else — narrow it to the customer's ranges. `terraform/variables.tf` says
so at the variable itself, because that is where someone will actually read it.

## Which Cloudera tool automates it

The **CDP CLI** (`cdp`) and the **`cloudera/cdp` Terraform provider**. Both drive the
CDP control plane; the provider is a wrapper over the same APIs the CLI calls.
