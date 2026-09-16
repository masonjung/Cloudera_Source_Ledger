# `.cicd/` — build → test → deploy

One Git-driven pipeline. Merging to `main` proves the accelerator still works;
deploying is a separate, deliberate click.

## What's here

| Path | What it is |
|---|---|
| `pipeline.yml` | GitLab CI definition — three stages, deploy gated to `main` and manual |
| `deploy.sh` | The deploy itself: SDX policies, CDE jobs, Serve layer. Dry-run by default. |
| `amp_install.py` | First task of the AMP launch — installs the runtime into `~/.local`, then proves it imports |

## Point GitLab at it

GitLab looks for `.gitlab-ci.yml` in the repo root and will not find this file on
its own. Set it once:

**Settings → CI/CD → General pipelines → CI/CD configuration file** →
`.cicd/pipeline.yml`

The root stays readable; every phase of the accelerator lives in a named directory.

## The stages

**`build`** — dependencies resolve and every layer imports the way the runtime
imports it. Catches a broken `sys.path` before the test stage buries it in a
fixture error.

**`test`** — `pytest tests -q`, the [Harden gate](../docs/GATES.md). Runs without
`--live`: CI must never fail because a search provider is rate-limiting, or a red
pipeline stops meaning "we broke something." The Spark jobs are covered here too:
`read_sqlite`, the column lists, and the CDE job definition's args checked against
the job's own parser — so a flag removed from one side surfaces in review rather
than at 02:30 in a scheduled job. Runs alongside `verify-jobs`, which parses the
Ranger JSON and the three shell scripts.

**`deploy`** — manual, `main` only, in three ordered steps.

## Deploy order is not arbitrary

```
govern → jobs → app
```

Access control lands before data moves; data lands before anything serves it. A
deploy that publishes the app first shows a stakeholder an empty dashboard and a
policy gap at the same time.

```bash
make deploy                      # dry run — prints every command
.cicd/deploy.sh all --execute    # ship everything
.cicd/deploy.sh app --execute    # ship only the Serve layer
```

## Conventions

- **Dry run is the default.** `deploy.sh` prints what it would do and changes
  nothing until `--execute`. `make deploy` is the dry run; there is no way to
  deploy by accident.
- **Deploying is manual.** `when: manual` on `main`. Merging means "this works,"
  not "this is live."
- **Idempotent everywhere.** Ranger import uses `updateIfExists=true`, the Iceberg
  DDL is `IF NOT EXISTS` throughout, and `cde job import` replaces a definition
  rather than duplicating it. Re-running a deploy is always safe.
- **Retry infrastructure, never tests.** The `retry` block covers runner failures
  only. Retrying a real test failure is how a flaky suite gets trusted for the
  wrong reason.
- **No credentials in this repo.** `CDP_ACCESS_KEY_ID` and `CDP_PRIVATE_KEY` are
  masked GitLab CI variables, written to `~/.cdp/credentials` in `before_script`
  and never logged.
- **Provisioning is not deploying.** `infra/` creates the environment, once.
  `.cicd/` ships code to an environment that already exists. Conflating them is how
  someone recreates a Data Lake during a demo.

## Which Cloudera tool automates it

`cdp` for the control plane (AI Applications, workspaces), `cde` for Data
Engineering jobs and Spark submits, and the Ranger REST API for policy import. All
three are driven from `deploy.sh`, so the same script runs in CI and by hand — the
only difference is who typed `--execute`.
