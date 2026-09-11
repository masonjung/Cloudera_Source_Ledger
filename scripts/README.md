# `scripts/` — developer tooling

Things you run by hand. Nothing here is part of the deployed accelerator.

## What's here

| Path | What it does |
|---|---|
| `cli.py` | The terminal interface — search, record, list, export, back up |
| `doctor.py` | Pre-demo preflight: probes every corpus and every engine, with timings |
| `new-accelerator.sh` | Clones this repo into a fresh, re-pointed, git-initialised accelerator |
| `example.py` | Smallest possible demo of the retrieval layer — text in, URLs out |
| `kernel.py` | Installs a layer's requirements into the running kernel — what `quickstart.ipynb` opens with |

## Search from the terminal

```bash
python scripts/cli.py search "GLP-1 receptor agonist adverse events" --provider openalex
python scripts/cli.py search "iceberg compaction" --backend duckduckgo --timelimit y -n 25
python scripts/cli.py list --urls
python scripts/cli.py export --format csv --out review-appendix.csv
python scripts/cli.py stats
python scripts/cli.py backup
python scripts/cli.py doctor
```

Same rows as the dashboard, written through the same
[`data/record.py`](../data/record.py). `make cli ARGS=--help` is the shortcut, but run
`cli.py` directly for anything with a query in it — a quoted string rarely survives
both make and the shell intact.

**URLs go to stdout; everything else goes to stderr.** So this leaves a file of URLs
and nothing else, while you still see what produced them:

```bash
python scripts/cli.py search "iceberg table format" > urls.txt
```

The stderr report names the options this corpus *applied* and the ones it **did not**,
which is the stored `NULL` explained before you have to infer it from the table.

| Exit | Meaning |
|---|---|
| `0` | results found, and recorded unless `--no-store` |
| `1` | the search failed — an engine errored, or the network is down |
| `2` | usage error, including a misspelled `--provider` |
| `3` | zero results — the corpus answered, and had nothing |

`3` is separate from `1` on purpose: a script has to be able to tell an empty corpus
from a dead network, which is the same distinction the dashboard's `EngineError`
branch exists to preserve. A misspelled provider is a usage error rather than a
coercion — the dashboard coerces because a form post is untrusted input arriving over
the wire, but a typed command is a typo, and silently searching the web when someone
asked for arXiv is worse than a usage message.

`export` emits one row per URL with its search's provenance denormalized onto it —
the shape a reviewer sorts by domain and filters by provider, and the shape the
lakehouse curates into. In CSV, an unsupported option is written as the literal
`NULL` and an unset one is left empty, because CSV has one empty cell and this record
needs two meanings; JSON is the faithful format.

## Start a new accelerator

```bash
make new VERTICAL=healthcare USECASE=readmission-risk
```

Creates `../cloudera-forge-healthcare-readmission-risk/` as a sibling directory:
copies the template, rewrites the accelerator name through the docs and Makefile,
clears URLvestigia's worked example out of the layer directories while keeping their
`README.md` guidance, and initialises a fresh git repo with one commit.

It refuses to overwrite an existing directory. Pass `--dry-run` to see the plan
first.

## Try the library

```bash
python scripts/example.py
```

Three lines of real usage against the retrieval layer. This hits a live search engine —
it is the fastest way to confirm the accelerator works end to end without starting
the server.

## Conventions

- **Nothing here ships.** Deployment scripts live in [`.cicd/`](../.cicd/),
  provisioning in [`infra/`](../infra/). If it runs in production it is in the wrong
  directory.
- **Dry run by default for anything destructive.** `new-accelerator.sh` writes to a
  new directory and refuses to clobber, but still supports `--dry-run` because the
  first thing anyone wants is to see what it will do.
- **POSIX shell, no dependencies.** These run on a laptop before anything is
  installed. `example.py` needs only `retrieval/requirements.txt`.
