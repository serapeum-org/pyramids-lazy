"""Resolve a usable `bash` for the CI-script tests, and the marker that gates them.

Two problems this directory had, both of which bit silently:

- `shutil.which("bash")` returns the first match on `PATH`, and on Windows that can be
  `C:\\Windows\\System32\\bash.exe` -- the WSL launcher. It cannot write a `C:/...`-style
  `GITHUB_OUTPUT` path, so a test harness handing it one fails for a reason that has nothing to do
  with the code under test. Git Bash winning is `PATH`-order luck: on this machine `which -a bash`
  lists `/usr/bin/bash`, `/bin/bash` and `/c/Windows/System32/bash`.
- `pytest.mark.skipif(_BASH is None, ...)` folds "bash is missing" into a green run with a skip
  count. The repo's `CLAUDE.md` forbids exactly that: a test needing something absent is marked and
  deselected explicitly, never auto-skipped, because a silent skip hides a broken environment
  behind a passing line.

So the gate here is the registered `bash` marker plus a fixture that FAILS when `bash` is absent.
An environment that genuinely has no `bash` deselects with `-m "not bash"`, which is a choice
someone makes on the command line rather than an accident of the machine.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

#: Marker for a test that drives a real `bash`. Registered in `[tool.pytest.ini_options].markers`.
requires_bash = pytest.mark.bash


def _is_wsl_launcher(candidate: Path) -> bool:
    """Whether `candidate` is the Windows WSL `bash.exe` rather than a real bash.

    Args:
        candidate: A path to a `bash` executable found on `PATH`.

    Returns:
        bool: True for anything under a `System32` directory.

    Examples:
        - The WSL launcher is rejected. Forward slashes on purpose: a backslash string is a
          SINGLE path component on POSIX, so `.parts` holds one element, this answers False,
          and a backslash example passes on Windows while failing every Linux and macOS leg
          of the test matrix:
            ```python
            >>> _is_wsl_launcher(Path("C:/Windows/System32/bash.exe"))
            True

            ```
        - Git Bash is not:
            ```python
            >>> _is_wsl_launcher(Path("C:/Program Files/Git/usr/bin/bash.exe"))
            False

            ```
    """
    return any(part.lower() == "system32" for part in candidate.parts)


def find_bash() -> str | None:
    """Return a `bash` that can run the CI scripts, preferring a real one over the WSL launcher.

    Walks `PATH` in order and returns the first `bash` that is not under `System32`, so the result
    does not depend on whether Git Bash happens to precede the WSL launcher. Falls back to
    `shutil.which` for platforms where the `PATH` walk finds nothing (and so stays correct if a
    distribution installs `bash` somewhere this loop does not look).

    Returns:
        str | None: The executable's path, or None when no usable `bash` exists.

    Examples:
        - The answer is machine dependent, so the contract is what can be shown: either None,
          or a file that exists right now:
            ```python
            >>> resolved = find_bash()
            >>> resolved is None or Path(resolved).is_file()
            True

            ```
        - Whatever it answers, it is never the WSL launcher -- the filter applies to the
          `shutil.which` fallback as well as to the `PATH` walk:
            ```python
            >>> resolved = find_bash()
            >>> resolved is None or not _is_wsl_launcher(Path(resolved))
            True

            ```
    """
    names = ("bash.exe", "bash") if os.name == "nt" else ("bash",)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for name in names:
            candidate = Path(entry) / name
            if candidate.is_file() and not _is_wsl_launcher(candidate):
                return str(candidate)
    found = shutil.which("bash")
    if found is not None and not _is_wsl_launcher(Path(found)):
        return found
    return None


#: The `bash` the tests in this directory drive, or None when the machine has none.
BASH = find_bash()
