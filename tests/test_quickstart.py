"""The Harden gate for `quickstart.ipynb` — the Run All promise, made testable.

Nothing here executes the notebook: it calls live search engines, and CI must never
depend on a third party being reachable. What it does check is everything that can
break a Run All *before* a single search happens — the bootstrap resolving the
repository from whatever directory the kernel started in, and the file being
committed in a state a reviewer can read.

The bootstrap cell is duplicated in the notebook rather than imported, because an
importable bootstrap would need the import path it exists to establish. These tests
are what make that duplication safe: they run the notebook's own cell, verbatim.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QUICKSTART = ROOT / "quickstart.ipynb"
EVAL = ROOT / "retrieval" / "notebooks" / "eval.ipynb"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def code_cells(notebook):
    return [c for c in notebook["cells"] if c["cell_type"] == "code"]


@pytest.fixture(scope="module")
def quickstart():
    return load(QUICKSTART)


@pytest.fixture(scope="module")
def bootstrap(quickstart):
    """The notebook's own bootstrap cell, as source."""
    for cell in code_cells(quickstart):
        source = "".join(cell["source"])
        if source.startswith("# --- bootstrap ---"):
            return source
    pytest.fail("quickstart.ipynb has no cell marked '# --- bootstrap ---'")


# --- the file itself -------------------------------------------------------

def test_the_notebook_is_valid_nbformat_4(quickstart):
    assert quickstart["nbformat"] == 4
    assert quickstart["metadata"]["kernelspec"]["name"] == "python3"
    assert code_cells(quickstart)


@pytest.mark.parametrize("path", [QUICKSTART, EVAL], ids=["quickstart", "eval"])
def test_notebooks_are_committed_without_outputs(path):
    """Diffs stay reviewable, and no real query text or result URL is ever
    committed inside an output blob."""
    for cell in code_cells(load(path)):
        assert cell["execution_count"] is None, f"{path.name} carries an execution count"
        assert cell["outputs"] == [], f"{path.name} carries committed output"


def test_every_quickstart_cell_carries_an_id(quickstart):
    """nbformat 4.5 specifies cell ids and Jupyter writes them on first save, so
    omitting them would make the first person to open the file produce a diff they
    did not make. (eval.ipynb predates this and is left alone.)"""
    for cell in quickstart["cells"]:
        assert cell.get("id"), f"cell without an id: {cell['cell_type']}"


@pytest.mark.parametrize("writer", [
    r"\bopen\s*\(",        # \b so subprocess.Popen( is not a false positive
    r"\.write_text\s*\(",
    r"\.to_csv\s*\(",
    r"\.mkdir\s*\(",
    r"\bshutil\.",
])
def test_the_notebook_writes_no_files(quickstart, writer):
    """Run All must leave the working tree clean apart from the gitignored store.

    The export cell prints the command that writes a file rather than writing one,
    and the dashboard cell sends the server's output to DEVNULL rather than to a
    log — so nothing here should be opening anything for writing.
    """
    for cell in code_cells(quickstart):
        source = "".join(cell["source"])
        assert not re.search(writer, source), f"cell {cell['id']} matches {writer}"


# --- the bootstrap, run for real -------------------------------------------

@pytest.fixture
def run_bootstrap(bootstrap, monkeypatch):
    """Execute the bootstrap cell from `cwd`, leaving sys.path as it was found."""
    def run(cwd):
        monkeypatch.chdir(cwd)
        original = list(sys.path)
        namespace = {}
        try:
            exec(compile(bootstrap, "quickstart.ipynb[bootstrap]", "exec"), namespace)
            # Captured before the restore below, or the assertion would be made
            # against a sys.path this fixture had already put back — which passed
            # only when an earlier test happened to leave `scripts` on it.
            namespace["observed_sys_path"] = list(sys.path)
        finally:
            sys.path[:] = original
        return namespace
    return run


@pytest.mark.parametrize("start", ["", "data", "scripts", "retrieval/notebooks"])
def test_the_bootstrap_finds_the_root_from_any_working_directory(run_bootstrap, start):
    """A kernel starts wherever it starts: Jupyter uses the notebook's directory,
    VS Code's jupyter.notebookFileRoot can override it to the workspace folder, and
    `jupyter lab` inherits the shell's. All of them have to work."""
    namespace = run_bootstrap(ROOT / start if start else ROOT)

    assert namespace["ROOT"] == ROOT


def test_the_bootstrap_puts_every_layer_on_the_path(run_bootstrap):
    """ROOT included, or `from app import hosting` fails in the dashboard cell."""
    namespace = run_bootstrap(ROOT)
    layers = {str(ROOT / layer) for layer in ("retrieval", "data", "scripts")} | {str(ROOT)}

    assert layers <= set(namespace["observed_sys_path"])


def test_the_bootstrap_fails_readably_outside_the_repository(run_bootstrap, tmp_path):
    """The one failure a first-time reader will actually hit, so it has to name
    the fix rather than raise ImportError three cells later."""
    with pytest.raises(RuntimeError) as exc_info:
        run_bootstrap(tmp_path)

    assert "quickstart.ipynb" in str(exc_info.value)


# --- the cells that drive the modules --------------------------------------
#
# What each cell *does* is tested where the code lives: app/hosting.py for the
# dashboard, data/present.py for the record, scripts/kernel.py for the installs.
# What is left here is the wiring — that the cells still call those modules, and
# still call them in the arrangement the prose around them promises.

@pytest.fixture(scope="module")
def cell_source(quickstart):
    def source(cell_id):
        for cell in quickstart["cells"]:
            if cell["id"] == cell_id:
                return "".join(cell["source"])
        pytest.fail(f"quickstart.ipynb has no cell {cell_id!r}")
    return source


def test_run_all_does_not_stop_the_dashboard_it_started(cell_source):
    """Run All runs *every* cell, so a stop cell that acted unconditionally would
    kill the server two cells after starting it and hand the reader a dead link."""
    assert "STOP_DASHBOARD = False" in cell_source("stop")


def test_the_dashboard_cell_keeps_its_handle_across_re_runs(cell_source):
    """`hosting.dashboard()` decides whether to start one, but only if the cell
    hands it what the last run left behind — otherwise every re-run looks like a
    first run to it, and a second server races the first for the port."""
    source = cell_source("dashboard")

    assert 'hosting.dashboard(globals().get("DASHBOARD"), ROOT)' in source


def test_the_stop_cell_can_stop_a_server_this_kernel_never_started(cell_source):
    """hosting.stop() finds an adopted server by itself, so the cell must call it
    even when it has no handle to pass — the usual case after a kernel restart."""
    source = cell_source("stop")

    assert "hosting.stop(DASHBOARD)" in source
    assert "hosting.status(DASHBOARD)" in source


def test_reading_the_record_is_not_gated_on_the_search_library(cell_source):
    """Reading needs no provider and no network — the point of keeping the record
    in a file rather than behind a service. Gating this on READY would make an
    offline read impossible for a reason that has nothing to do with it."""
    assert "READY" not in cell_source("record")


def test_the_record_is_rendered_through_present(cell_source):
    """Not pandas, and not markup written in the cell: data/present.py is where
    the NULL-is-not-unset rule survives into what the reader sees."""
    assert "present.searches(" in cell_source("record")
