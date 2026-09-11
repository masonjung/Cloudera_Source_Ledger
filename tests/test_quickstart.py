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


# --- the dashboard cells --------------------------------------------------

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


def test_the_dashboard_reuses_a_server_already_on_the_port(cell_source):
    """VS Code's tasks.json starts uvicorn on folderOpen, so for this repo's own
    developers a server on 8000 is the normal case, not the exception. Re-running
    the cell must reuse it rather than fight it for the port.

    The process handle is checked before the port because a socket can still answer
    for a moment after the server owning it was told to stop.
    """
    source = cell_source("dashboard")

    assert "starting = not mine and ADOPTED is None and not hosting.held()" in source
    assert "DASHBOARD.poll()" in source
    assert "atexit.register" in source  # no orphan holding the port after the kernel exits


def test_the_dashboard_asks_what_is_on_the_port_not_only_whether_it_answers(cell_source):
    """A dashboard left behind by a restarted kernel probes identically to a fresh
    one and is the likeliest thing to be on the port when the notebook is re-run.
    Reported as a plain "already running" it hands the reader a link to the code as
    it was two edits ago, out of a process the notebook cannot stop."""
    source = cell_source("dashboard")

    assert "hosting.identify()" in source
    assert "hosting.edited_since(" in source  # or a stale server goes unmentioned


def test_the_stop_cell_can_stop_a_server_it_did_not_start(cell_source):
    """Otherwise section 7's instruction is false in the one case it is most often
    read in: the kernel that owned the process is gone, and the pid /healthz
    reports is the only handle left."""
    source = cell_source("stop")

    assert "hosting.identify()" in source
    assert 'os.kill(adopted["pid"], signal.SIGTERM)' in source


def test_the_dashboard_does_not_use_reload(cell_source):
    """--reload runs the server in a grandchild process, which survives terminate()
    and keeps the port after the notebook has said it stopped."""
    source = cell_source("dashboard")

    assert "uvicorn" in source
    # As a string literal, i.e. actually passed as an argument. The cell's comment
    # is allowed to say the word while explaining why it is not used.
    assert '"--reload"' not in source
    assert "'--reload'" not in source


def test_the_notebook_installs_with_the_kernels_own_interpreter(cell_source):
    """`pip` on PATH is frequently not this kernel's interpreter, and that mismatch
    is the most common reason a "but I installed it" notebook still fails."""
    source = cell_source("deps")

    assert "sys.executable" in source
    assert "importlib.invalidate_caches()" in source  # or the install stays invisible


def test_the_bootstrap_needs_more_than_one_marker(run_bootstrap, tmp_path):
    """scripts/new-accelerator.sh copies METADATA.yaml into a fresh accelerator, so
    matching on it alone would resolve to the wrong repository."""
    (tmp_path / "METADATA.yaml").write_text("name: not-urlvestigia\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        run_bootstrap(tmp_path)
