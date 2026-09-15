# Stage gates — what "done" means

Six phases, one accountable owner each, an explicit bar at every handoff. A phase is
not done because the work feels finished; it is done when the checklist below is
true and the owner of the next phase accepts it.

The point of a gate is to make the *no* cheap. A use case killed at Qualify costs a
week. The same use case killed at Harden costs two months and a customer
conversation.

| Phase | Week | Owner accountable for |
|---|---|---|
| [Discover](#discover) | wk 0 | Finding candidates worth scoring |
| [Qualify](#qualify) | wk 1 | Scoring honestly and killing the weak ones |
| [Architect](#architect) | wk 2 | A design that fits the reference stack |
| [Build](#build) | wk 3–6 | Working code across every layer |
| [Harden](#harden) | wk 7 | Tests, governance, and a defensible security posture |
| [Publish](#publish) | wk 8 | A repo someone else can deploy without you |

---

## Discover

**Goal:** a candidate written down well enough to score.

Open an issue with the
[Use-Case-Candidate template](../.gitlab/issue_templates/Use-Case-Candidate.md).

- [ ] The use case is one sentence, and it names who does what differently
- [ ] The problem today is described with at least one number
- [ ] "Why Cloudera?" is answered — and if the honest answer is "it runs fine as a
      laptop script," that is a **valid, cheap disqualification**. Take it.
- [ ] Data sources identified
- [ ] A proposed owner exists for each phase — a name, not a team

**Exit:** the issue is complete enough that someone who was not in the room can
score it.

---

## Qualify

**Goal:** a scored decision, made once, with the reasoning visible.

- [ ] Scorecard filled in: market demand (25%), revenue impact (25%), technical fit
      (20%), reusability (20%), effort (10%)
- [ ] Every score has a justification. A number with no sentence next to it is not a
      score.
- [ ] **Weighted total ≥ 4.0 / 5** to advance
- [ ] Can the data leave the customer's environment? **Answer this now.** It has
      killed accelerators at Harden that were fine on every other axis — Source Ledger has a
      real constraint here (see
      [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md#third-party-disclosure))
- [ ] Owner assigned for Architect

Reusability is the criterion people inflate. A demo rebuilt from scratch for every
customer is not an accelerator, whatever else it scores.

**Exit:** advance, hold, or decline — recorded on the issue with a reason.

---

## Architect

**Goal:** a design that maps onto the reference stack, before anyone writes code.

- [ ] `docs/ARCHITECTURE.md` written: every layer from Ingest → Serve either has a
      component or an explicit "not used, because…"
- [ ] `docs/BUSINESS_CASE.md` written
- [ ] Data model drafted — the table shapes, not just the sources
- [ ] Sensitive fields identified and classified in
      `governance/DATA_CLASSIFICATION.md`. **Classify before you store, not after.**
- [ ] Infra sizing sketched in `infra/` — demo footprint, not production
- [ ] Known constraints and gaps written down while they are still cheap to state

**Exit:** a build owner can start without asking what the tables look like.

---

## Build

**Goal:** the thing works, end to end, on real data.

Weeks 3–6, and the phase that scales with complexity.

- [ ] Every layer directory has code, or a README saying why it is empty
- [ ] Schema exists in **both** tiers: `data/schema.sql` and `data/iceberg/ddl.sql`
- [ ] Pipelines are **idempotent** — safe to re-run over an overlapping window
- [ ] The Serve layer runs locally with one command (`make dev`)
- [ ] `make -n deploy` dry-runs cleanly through every step
- [ ] Each directory's `README.md` describes what is actually there now

**Exit:** someone else can clone, `make dev`, and see it work.

---

## Harden

**Goal:** it survives contact with a customer's security review.

This is the gate that gets skipped and the one that costs the most to skip.

- [ ] `make test` green, in CI, on the default branch
- [ ] Data quality checks exist for anything derived
- [ ] AI eval harness exists and has been run — with **dated** results in the model
      card. Undated measurement is not evidence.
- [ ] Model card complete for every AI component: intended use, **out of scope**,
      known limitations, evaluation
- [ ] SDX policies written and importable; **no policy grants `public`**
- [ ] Data classification complete, including retention and third-party disclosure
- [ ] Known gaps stated in `governance/README.md` — discovered by you, not by the
      customer's reviewer
- [ ] Secrets audited: nothing in the repo, CI variables masked
- [ ] Ingress narrowed from `0.0.0.0/0`

**Exit:** you would be comfortable handing this to a security team you have not met.

---

## Publish

**Goal:** it deploys clean from the repo alone.

- [ ] `docs/EXAMPLE.md` walks the whole accelerator end to end
- [ ] Every directory `README.md` is current — the naming conventions and the
      Cloudera tool that automates it
- [ ] Root `README.md` explains what it is, how to run it, and what is already built
- [ ] `make new` produces a working scaffold from this template
- [ ] Provisioning verified on a **clean** environment, from zero
- [ ] Handoff: someone who has never seen the repo deploys it without asking you a
      question

**Exit:** deployed, governed, and reusable. The last box is the real test — if they
had to ask you something, the gap is a documentation defect, not a training issue.

---

## Where Source Ledger stands

Honest status, not aspiration:

| Gate | Status |
|---|---|
| Discover | Cleared |
| Qualify | Cleared |
| Architect | Cleared — [`ARCHITECTURE.md`](ARCHITECTURE.md), [`BUSINESS_CASE.md`](BUSINESS_CASE.md) |
| Build | Cleared for the web edge — app, retrieval, and SQLite storage all run (`make dev`, `make test`). The CDP platform layers are written and import clean, awaiting an environment to connect to |
| **Harden** | Open — the suite is green and the capability is solid, but three items stand before this is served to anyone: no authentication or CSRF protection, no dated eval run on the model card, and `ingress_cidrs` at `0.0.0.0/0` |
| Publish | Follows Harden |

The three open items are tracked in
[`governance/README.md`](../governance/README.md#known-gaps). None is hard; all three
are unfinished.
