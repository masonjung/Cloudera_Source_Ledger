# Operating model — local pointer

> **This is not the canonical document.** The complete Cloudera Forge operating model
> — how accelerators are sourced, scored, built, and shipped — lives in the Forge
> accelerator template repository as `docs/OPERATING_MODEL.md`, derived from
> `docs/presentations/Cloudera_Forge_Operating_Model.pptx`.
>
> This file records only how Source Ledger sits inside that model, and is deliberately thin
> so it cannot drift into a competing account. **Read the canonical version first.**

## The four standard components

| Component | What it is | Where it shows up in this repo |
|---|---|---|
| **A way to choose** | Sourcing + weighted scoring; ≥ 4.0 / 5 advances | [`.gitlab/issue_templates/Use-Case-Candidate.md`](../.gitlab/issue_templates/Use-Case-Candidate.md), scored in [`BUSINESS_CASE.md`](BUSINESS_CASE.md) |
| **A build process** | 6-phase stage gate, one accountable owner per phase | [`GATES.md`](GATES.md) |
| **A build standard** | One reference stack: Ingest → Lakehouse → Process → AI → Serve, governed by SDX | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| **A standard deliverable** | A repo that deploys clean from the repo alone | this repository |

## The lifecycle

| Week | wk 0 | wk 1 | wk 2 | wk 3–6 | wk 7 | wk 8 |
|---|---|---|---|---|---|---|
| **Phase** | Discover | Qualify | Architect | Build | Harden | Publish → Deployed |

From selected use case to a deployed, governed solution. The Build phase scales with
complexity; the others do not.

## How Source Ledger maps onto it

Source Ledger is the template's **worked example** rather than a customer engagement, so its
path through the phases was compressed. Where it actually stands — including the
three open Harden items — is in [`GATES.md`](GATES.md#where-source-ledger-stands), stated
honestly rather than aspirationally.

The one part of the model worth re-reading in Source Ledger's own scorecard: it advances at
**4.05** on technical fit and reusability, not on market demand. A candidate with
that profile and no teaching role would be a *hold*. Saying so is what makes the
scorecard worth filling in — see [`BUSINESS_CASE.md`](BUSINESS_CASE.md#scorecard).

## If this file and the canonical document disagree

The canonical document wins. Fix this one.
