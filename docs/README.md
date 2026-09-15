# `docs/` — architecture and business case

The written half of the accelerator. If the code answers *how*, this directory
answers *what*, *why*, and *is it done*.

## Read in this order

| # | Document | Read it when |
|---|---|---|
| 1 | [`EXAMPLE.md`](EXAMPLE.md) | **Start here.** A 20-minute walkthrough of one search across all five layers, on a laptop. |
| 2 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | You need the layer map and the decisions worth defending |
| 3 | [`GATES.md`](GATES.md) | You need to know what "done" means — and where Source Ledger actually stands |
| 4 | [`BUSINESS_CASE.md`](BUSINESS_CASE.md) | You are scoring this, or writing the case for your own accelerator |
| 5 | [`OPERATING_MODEL.md`](OPERATING_MODEL.md) | A pointer to the canonical Forge operating model, plus how Source Ledger maps onto it |
| 6 | [`architecture/DESIGN_TEMPLATE.md`](architecture/DESIGN_TEMPLATE.md) | You are changing the UI |

## Conventions

- **Write it before you build it.** `ARCHITECTURE.md` and `BUSINESS_CASE.md` are
  Architect-phase artifacts. A design written after the code is a description, not a
  decision.
- **State gaps here, not in a review.** [`GATES.md`](GATES.md#where-source-ledger-stands)
  says the Harden gate is open and names all three reasons. A known gap you wrote down
  is a plan; the same gap found by a customer's reviewer is a problem.
- **Mark estimates as estimates.** `BUSINESS_CASE.md` tags unmeasured figures
  _(estimate)_. Numbers presented as fact are how a scorecard stops being useful.
- **One canonical source per fact.** The design tokens live in
  `app/templates/index.html`; `DESIGN_TEMPLATE.md` describes them. The operating
  model lives in the template repo; `OPERATING_MODEL.md` points at it. When a copy
  and its source disagree, the source wins and the copy is the bug.
- **Directory READMEs cover their own directory.** What belongs in `pipelines/` is
  documented in `pipelines/README.md`, not here. This directory holds what spans
  layers.

## What is not here

The template's `docs/presentations/` — the Forge operating-model deck and the
Cloudera + NVIDIA deck toolkit — is not reproduced in this accelerator. Those live in
the Forge template repository. Source Ledger carries only the pointer in
[`OPERATING_MODEL.md`](OPERATING_MODEL.md).
