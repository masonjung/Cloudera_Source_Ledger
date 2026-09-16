"""Install what a notebook needs into the kernel that is running it.

`pip` on PATH is frequently not the interpreter a Jupyter kernel is running, and
that mismatch is the most common reason a notebook still fails after someone has
installed the thing it says is missing. Everything here goes through
`sys.executable`.

Standard library only, deliberately: this is the code that runs before the
dependencies exist.
"""

import importlib
import importlib.util
import os
import site
import subprocess
import sys


def _see_the_user_site():
    """Put a just-created user site directory on `sys.path`.

    `site` adds `~/.local/lib/pythonX.Y/site-packages` at startup only if it
    already exists, so the first `--user` install in a fresh managed session --
    a new Cloudera AI project, say -- creates a directory this interpreter will
    never look in. `invalidate_caches()` cannot rescue that: the path is not on
    `sys.path` at all, and the check below would then fail an install that
    actually worked.
    """
    if not site.ENABLE_USER_SITE:
        return
    user_site = site.getusersitepackages()
    if os.path.isdir(user_site) and user_site not in sys.path:
        site.addsitedir(user_site)


def missing(modules):
    """Which of `modules` cannot be imported here."""
    return [name for name in modules if importlib.util.find_spec(name) is None]


def ensure(modules, requirements, what, say=print):
    """Make sure `modules` import here, installing `requirements` if they do not.

    True when they are usable. Never raises: a quickstart that dies on its
    dependency cell has failed at the one job it has.
    """
    absent = missing(modules)
    if not absent:
        return True

    say(f"Installing {what} ({', '.join(absent)} missing)...")
    install = [sys.executable, "-m", "pip", "install", "-q", "-r", str(requirements)]
    # A managed runtime image — a Cloudera AI session, say — usually has a
    # site-packages the session user cannot write to, and there the install has to
    # go to the user site instead. Not offered inside a virtualenv, where pip
    # rejects --user outright because the user site is not on its path.
    attempts = [install] if sys.prefix != sys.base_prefix else [install, install + ["--user"]]
    for attempt in attempts:
        try:
            subprocess.check_call(attempt)
            break
        except (subprocess.CalledProcessError, OSError) as exc:
            failure = exc
    else:
        say(f"  Could not install it automatically ({failure}). Run this yourself:\n")
        say(f"    {sys.executable} -m pip install -r {requirements}\n")
        say("  Then restart the kernel and Run All again.")
        return False

    # A package installed after the interpreter started is invisible until the
    # import system is told to look again -- and, when pip had to fall back to
    # `--user`, until the directory it created is on `sys.path` at all.
    _see_the_user_site()
    importlib.invalidate_caches()
    if missing(modules):
        say(f"  Installed, but {', '.join(missing(modules))} is still not importable.")
        say("  Restart the kernel and Run All again.")
        return False
    say("  Installed.")
    return True
