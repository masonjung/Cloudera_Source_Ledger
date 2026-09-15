# `.gitlab/` — GitLab templates

Process, in the place where the work happens. These templates are how the operating
model shows up in someone's day rather than in a deck they read once.

## What's here

| Path | When it appears |
|---|---|
| `merge_request_templates/Default.md` | Pre-filled in the description of every new merge request |
| `issue_templates/Use-Case-Candidate.md` | Selectable from the *Description template* dropdown on a new issue |

GitLab picks these up automatically from `.gitlab/` on the default branch — no
configuration. (The CI file is the exception: it lives in `.cicd/pipeline.yml` and
must be pointed at manually. See [`.cicd/README.md`](../.cicd/README.md).)

## The merge request template

Three things it asks for that are easy to skip and expensive to skip:

- **Which layers changed.** Reviewers use it to know whose eyes are needed. A tick
  on `governance/` means the accelerator owner reviews too.
- **Conditional checklists.** Changing a retrieval default requires a dated eval
  run. Adding a schema column requires updating *both* `schema.sql` and the
  migration list. Storing anything a user typed requires a classification entry in
  the same merge request — not afterwards, when it is someone else's problem.
- **"Anything a reviewer should know."** Known gaps and close judgement calls, said
  out loud rather than discovered in review.

Delete the sections that do not apply. A template that is tedious to fill in gets
filled in dishonestly.

## The use-case candidate template

The Discover → Qualify artifact. Carries the weighted scorecard — market demand
(25%), revenue impact (25%), technical fit (20%), reusability (20%), effort (10%) —
and **≥ 4.0 / 5 advances**.

Two questions on it do most of the work:

- *"Why Cloudera?"* If the answer is that it runs fine as a laptop script, that is a
  legitimate answer and a fast, cheap disqualification.
- *"Can the data leave the customer's environment?"* Answer it during Qualify. It
  has killed accelerators at the Harden gate that were fine on every other axis —
  Source Ledger itself has a real constraint here, in
  [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md).

Reusability is the criterion people inflate. A demo rebuilt from scratch for every
customer is not an accelerator, whatever else it scores.

## Conventions

- **One accountable owner per phase**, named on the issue. Not a team.
- **Labels track the phase**: `forge::discover` → `forge::qualify` → `forge::architect`
  → `forge::build` → `forge::harden` → `forge::publish`. The template applies the
  first one.
- **The gate criteria live in [`docs/GATES.md`](../docs/GATES.md)**, not in these
  templates. Templates prompt; the gates decide.

## Adding a template

Drop a `.md` file in `issue_templates/` or `merge_request_templates/` and merge it to
the default branch. It appears in the dropdown immediately. `Default.md` is special —
it is pre-filled without anyone choosing it, so keep it the one that applies to most
merge requests.
