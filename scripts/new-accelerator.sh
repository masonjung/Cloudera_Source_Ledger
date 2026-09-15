#!/usr/bin/env bash
#
# Start a new Cloudera Forge accelerator from this template.
#
#   make new VERTICAL=healthcare USECASE=readmission-risk
#   ./scripts/new-accelerator.sh --vertical healthcare --usecase readmission-risk
#
# Creates ../cloudera-forge-<vertical>-<usecase>/ as a sibling directory:
# copies the layout, re-points the name through docs and the Makefile, clears
# this accelerator's worked example out of the layer directories while keeping
# their README.md guidance, and initialises a fresh git repo.
#
# Refuses to overwrite an existing directory. --dry-run prints the plan.

set -euo pipefail

VERTICAL=""
USECASE=""
DEST_PARENT=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vertical) VERTICAL="$2"; shift 2 ;;
    --usecase)  USECASE="$2";  shift 2 ;;
    --dest)     DEST_PARENT="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERTICAL" || -z "$USECASE" ]]; then
  echo "usage: $0 --vertical <vertical> --usecase <usecase> [--dest DIR] [--dry-run]" >&2
  echo "   eg: $0 --vertical healthcare --usecase readmission-risk" >&2
  exit 2
fi

# Both become part of a repo name, a directory name, and eventually a CDP
# environment name — all of which are lowercase-hyphen or nothing.
for value in "$VERTICAL" "$USECASE"; do
  if [[ ! "$value" =~ ^[a-z][a-z0-9-]*$ ]]; then
    echo "error: '$value' must be lowercase alphanumeric with hyphens, starting with a letter" >&2
    exit 2
  fi
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_PARENT="${DEST_PARENT:-$(dirname "$ROOT")}"
NAME="cloudera-forge-${VERTICAL}-${USECASE}"
DEST="${DEST_PARENT}/${NAME}"

# Directories whose contents are this accelerator's worked example. The README.md
# in each is the standard and is always kept; everything else is Source Ledger's own code.
EXAMPLE_DIRS=(retrieval app data pipelines governance infra tests)

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
run()  { if $DRY_RUN; then echo "    $*"; else "$@"; fi; }

if $DRY_RUN; then
  cat <<'BANNER'
new-accelerator — DRY RUN
=========================
Nothing will be written. The plan follows.
BANNER
fi

echo
echo "template     ${ROOT}"
echo "new repo     ${DEST}"
echo "vertical     ${VERTICAL}"
echo "use case     ${USECASE}"

if [[ -e "$DEST" ]]; then
  echo >&2
  echo "error: ${DEST} already exists — refusing to overwrite." >&2
  echo "Remove it or pass --dest to choose another parent directory." >&2
  exit 1
fi

# --- 1. Copy ---------------------------------------------------------------
# git archive rather than cp -r: it copies exactly what is tracked, so no .venv,
# no __pycache__, no stray database file.
step "1/5  Copy the template (tracked files only)"
run mkdir -p "$DEST"
if $DRY_RUN; then
  echo "    git -C ${ROOT} archive HEAD | tar -x -C ${DEST}"
else
  git -C "$ROOT" archive HEAD | tar -x -C "$DEST"
fi

# --- 2. Clear the worked example -------------------------------------------
# The new accelerator inherits the structure and the guidance, not Source Ledger's code.
step "2/5  Clear the worked example, keep every README.md"
for dir in "${EXAMPLE_DIRS[@]}"; do
  if $DRY_RUN; then
    echo "    clear ${dir}/ (keeping ${dir}/README.md)"
  else
    find "${DEST}/${dir}" -mindepth 1 -not -name 'README.md' -delete 2>/dev/null || true
  fi
done
# Both are this accelerator's own: example.py demonstrates text_to_urls(), and
# doctor.py probes Source Ledger's four providers by name and imports the retrieval
# modules step 2 just deleted. Left in place they would not run at all.
run rm -f "${DEST}/scripts/example.py" "${DEST}/scripts/doctor.py"

# --- 3. Re-point the name --------------------------------------------------
step "3/5  Re-point the accelerator name"
if $DRY_RUN; then
  echo "    rewrite Source Ledger → ${NAME} across *.md, Makefile, .cicd/, .gitlab/"
  echo "    rewrite source-ledger-dev → ${VERTICAL}-${USECASE}-dev"
else
  # -print0/-0 so a path with a space cannot split the argument list.
  # No \b word boundaries below: that is a GNU extension, and BSD sed (the
  # default on macOS) does not support it -- the rename would silently no-op
  # there and the new repo would keep this template's name. Plain
  # substitution is safe because no identifier contains these as a substring.
  find "$DEST" -type f \( -name '*.md' -o -name 'Makefile' -o -name '*.yml' \
       -o -name '*.sh' -o -name '*.tf' \) -print0 \
    | xargs -0 sed -i.bak \
        -e "s/cloudera-forge-<vertical>-<usecase>/${NAME}/g" \
        -e "s/source-ledger-dev/${VERTICAL}-${USECASE}-dev/g" \
        -e "s/Source Ledger/${NAME}/g"
  find "$DEST" -name '*.bak' -delete
fi

# --- 4. A README and catalog metadata that say what to do next --------------
# METADATA.yaml is rewritten rather than sed-re-pointed: the step above only
# rewrites the *name*, and a catalog entry that inherited Source Ledger's slug,
# description, tags, and GitHub link while carrying a new name is worse than an
# empty one — it looks filled in.
step "4/5  Write a starting README and METADATA.yaml"
if $DRY_RUN; then
  echo "    ${DEST}/README.md"
  echo "    ${DEST}/METADATA.yaml"
else
  cat > "${DEST}/README.md" <<EOF
# ${NAME}

A Cloudera Forge accelerator: **${VERTICAL} · ${USECASE}**.

Generated from the Forge accelerator template. Every directory has a
\`README.md\` explaining what belongs there, the naming conventions, and which
Cloudera tool automates it — **read those first**, then fill each one in.

## Where to start

1. \`docs/\` — write the business case and the architecture before any code.
2. \`data/\` — model the tables. Everything downstream depends on this shape.
3. \`retrieval/\` — the capability that makes this an accelerator rather than a dashboard.
4. \`app/\` — the Serve layer a stakeholder actually opens.
5. \`governance/\` — classify the data **before** you store it, not after.
6. \`tests/\` — the Harden gate. Nothing ships until this is green.

## Commands

\`\`\`bash
make help          # every target
make dev           # run the Serve layer
make test          # the Harden gate
make -n deploy     # dry-run the standard deploy
\`\`\`

## The bar

\`docs/GATES.md\` defines what "done" means at each of the six phases. The
lifecycle is ~8 weeks from selected use case to a deployed, governed solution.
EOF
  echo "    ${DEST}/README.md"

  # Identity fields are blanked rather than inherited. A catalog entry carrying
  # the template's slug, description, tags, GitHub link, and maintainer while
  # showing a new name is worse than an empty one -- it looks filled in, and the
  # first person to trust it publishes someone else's repo under this name.
  cat > "${DEST}/METADATA.yaml" <<EOF
# Cloudera Blueprint catalog metadata
# Every field below is a placeholder. Fill them in before catalog submission.

# --- Identity ---
name: ""                                     # Human-readable title
slug: "${NAME}"                              # URL-safe repo / catalog identifier
description: ""                              # One sentence: what goes in, what comes out
reprise_link: ""                             # Public reprise link

# --- Classification (website filters) ---
catalog_classification:
  - Enterprise Blueprint                     # Enterprise Blueprint | Research | Launchable (AMP) | Developer Example
launchable: false
lifecycle_status: Draft                      # Draft until it clears the Harden gate -- see docs/GATES.md

# --- Industry & alignment ---
industry: General
industry_alignment: ""                       # Horizontal | Vertical
target_vertical: "${VERTICAL}"

# --- Tags (website search & browse) ---
tags:
  - ${VERTICAL}
  - ${USECASE}

# --- Relationships ---
product_mapping: []                          # Cloudera products actually used -- see docs/ARCHITECTURE.md

# --- Provenance ---
publisher: cloudera                          # cloudera | partner-name
source: Internal                             # Internal | Partner | NVIDIA Adaptation | Customer

# --- Repository & assets ---
public_github_link: ""

# --- Ownership ---
maintainer_name: ""

# --- Dates ---
date_created: ""
date_updated: ""
published_date: ""
EOF
  echo "    ${DEST}/METADATA.yaml"
fi

# --- 5. Fresh git history --------------------------------------------------
# A new accelerator starts at commit 1. Inheriting the template's history makes
# every later `git log` a lie about where the code came from.
step "5/5  Initialise git"
if ! $DRY_RUN; then
  git -C "$DEST" init -q
  git -C "$DEST" add -A
  git -C "$DEST" -c user.name="Cloudera Forge" \
                 -c user.email="forge@cloudera.com" \
                 commit -qm "Scaffold ${NAME} from the Forge accelerator template"
  echo "    initialised with one commit"
else
  echo "    git init && git add -A && git commit"
fi

if $DRY_RUN; then
  printf '\n\033[1mDry run complete.\033[0m Re-run without --dry-run to create it.\n'
else
  printf '\n\033[1m%s created.\033[0m\n' "$NAME"
  echo
  echo "  cd ${DEST}"
  echo "  make help"
  echo
  echo "Then work directory by directory, using each README.md as the guide and"
  echo "docs/GATES.md as the bar for each handoff."
fi
