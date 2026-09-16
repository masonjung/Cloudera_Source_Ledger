"""Install the accelerator's runtime dependencies into an AMP session.

The first task in `.project-metadata.yaml`. A Cloudera AI runtime ships neither the
web framework nor the search library, and an AMP has no other way to install them:
every later task — the preflight, the jobs, the application — imports code that does
not exist until this has run.

`app/requirements.txt` is the whole closure, because the Serve layer pulls in the AI
layer and the Lakehouse layer needs nothing (sqlite3 is stdlib). Test dependencies are
deliberately not installed: nothing the application does needs pytest.

The install lands in `~/.local` on project storage, which is what makes it visible to
the jobs and to the application — but only for the runtime image it was installed
from. Keep one image across every task in the AMP.

Standard library only, like the rest of the code that runs before the dependencies
exist: the pip fallback this needs in a managed runtime already lives in
`scripts/kernel.py`, so this asks for it rather than restating it.

    python .cicd/amp_install.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import kernel  # noqa: E402

REQUIREMENTS = ROOT / "app" / "requirements.txt"

# What has to import afterwards, by module name rather than by distribution name —
# python-multipart installs as `python_multipart`, and a check against the wrong name
# is a check that always passes. FastAPI cannot parse a form post without it, which is
# every write route in the dashboard. Its older `multipart` spelling still resolves,
# but only through a compatibility shim the project has deprecated: checking that name
# would fail this task, and so the whole launch, on an upgrade that broke nothing.
MODULES = ("fastapi", "uvicorn", "jinja2", "python_multipart", "ddgs")


def main():
    """Install, then prove it. Non-zero exit stops the AMP here.

    A half-installed environment is the one outcome worth failing the launch for: it
    surfaces later as an application that will not start, or — worse — as a dashboard
    that serves every page until someone runs a search.
    """
    print(f"Installing {REQUIREMENTS.relative_to(ROOT).as_posix()} for {sys.executable}")
    if not kernel.ensure(MODULES, REQUIREMENTS, "the Source Ledger runtime"):
        return 1

    missing = kernel.missing(MODULES)
    if missing:
        print(f"Still not importable after the install: {', '.join(missing)}")
        return 1
    print(f"Ready: {', '.join(MODULES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
