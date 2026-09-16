"""Installing into the kernel that is running — `scripts/kernel.py`.

This is the code that runs before the dependencies exist, so every failure it can
have is a failure the reader meets in the first cell of a Run All. None of it may
raise, and all of it has to name the fix.
"""

import subprocess
import sys

import kernel
import pytest


@pytest.fixture
def installs(monkeypatch):
    """Record what would have been run, and control what is missing before and after."""
    def configure(*answers):
        remaining = iter(answers)
        monkeypatch.setattr(kernel, "missing", lambda modules: next(remaining))

    commands = []
    monkeypatch.setattr(kernel.subprocess, "check_call", commands.append)
    configure.commands = commands
    return configure


def test_nothing_is_installed_when_everything_imports(installs):
    installs([])

    assert kernel.ensure(["ddgs"], "requirements.txt", "the search library") is True
    assert installs.commands == []


def test_the_install_uses_the_running_interpreter(installs):
    """`pip` on PATH is frequently not this kernel's interpreter, and that mismatch
    is the most common reason a notebook still fails after someone installed the
    thing it said was missing."""
    installs(["ddgs"], [])

    assert kernel.ensure(["ddgs"], "retrieval/requirements.txt", "search") is True
    assert installs.commands[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert "retrieval/requirements.txt" in installs.commands[0]


def test_a_fresh_install_is_made_visible_to_the_import_system(installs, monkeypatch):
    """A package installed after the interpreter started stays invisible until the
    import system is told to look again."""
    looked = []
    monkeypatch.setattr(kernel.importlib, "invalidate_caches",
                        lambda: looked.append(True))
    installs(["ddgs"], [])

    kernel.ensure(["ddgs"], "requirements.txt", "search")

    assert looked


def test_a_user_install_is_made_visible_even_on_its_first_run(installs, monkeypatch,
                                                              tmp_path):
    """`site` puts the user site directory on `sys.path` at startup only if it
    already exists. The first `--user` install in a fresh managed session -- a new
    Cloudera AI project -- creates it, so the modules land somewhere this
    interpreter was never told to look and the check below fails an install that
    worked, and the quickstart reports a missing dependency it has just installed."""
    user_site = tmp_path / "lib" / "python3.11" / "site-packages"
    user_site.mkdir(parents=True)
    monkeypatch.setattr(kernel.site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(kernel.site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(sys, "path", list(sys.path))
    installs(["ddgs"], [])

    assert kernel.ensure(["ddgs"], "requirements.txt", "search") is True
    assert str(user_site) in sys.path


def test_a_failed_install_reports_the_command_to_run_by_hand(monkeypatch):
    """Never raises. A quickstart that dies on its dependency cell has failed at
    the one job it has, so the cell says what to type and carries on."""
    monkeypatch.setattr(kernel, "missing", lambda modules: ["ddgs"])
    monkeypatch.setattr(kernel.subprocess, "check_call",
                        lambda cmd: (_ for _ in ()).throw(
                            subprocess.CalledProcessError(1, cmd)))
    said = []

    assert kernel.ensure(["ddgs"], "requirements.txt", "search", say=said.append) is False
    assert any("pip install" in line for line in said)


def test_an_install_that_did_not_take_says_so(installs):
    """pip can exit 0 and still leave the module unimportable — a wheel for another
    platform, a name that does not match the distribution. Reporting success there
    would send the reader four cells further to an ImportError."""
    installs(["ddgs"], ["ddgs"], ["ddgs"])
    said = []

    assert kernel.ensure(["ddgs"], "requirements.txt", "search", say=said.append) is False
    assert any("Restart the kernel" in line for line in said)


def test_missing_names_only_what_cannot_be_imported():
    assert kernel.missing(["sys", "json"]) == []
    assert kernel.missing(["sys", "no_such_module_anywhere"]) == ["no_such_module_anywhere"]
