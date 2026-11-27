"""Guard `tests/ci/_bash.py`, which decides WHICH `bash` the CI-script tests drive.

The module exists because two silent failures met here, and both of them are failures of
selection rather than of logic -- which is why neither showed up as a red test:

- `shutil.which("bash")` returns the first match on `PATH`, and on Windows that can be
  `C:\\Windows\\System32\\bash.exe`, the WSL launcher. It cannot write a `C:/...`-style
  `GITHUB_OUTPUT` path, so every test that hands it one fails for a reason unrelated to the code
  under test. Which binary wins is `PATH`-order luck, so a test suite that is green on one machine
  is red on the next.
- a `skipif` on a missing `bash` folds the 16 tests that are `test_bundle_pr_arm.py`'s whole point
  into a skip count, which the repo's `CLAUDE.md` forbids.

Both decisions are made once, at import, by `find_bash()` -- so on any single machine exactly one
of its branches runs and the rest are invisible. That is what this module is for: it drives each
branch against planted executables under `tmp_path` instead of against whatever the machine
happens to have, so the WSL filter, the empty-`PATH`-entry skip, the per-platform name tuple and
the `shutil.which` fallback are all exercised wherever the suite runs.

Nothing here touches the network or the real `PATH`, and nothing here is Windows-only: a
`System32` path is built with `Path` so the filter is exercised on linux and macOS too, and the
one genuinely platform-dependent branch -- the `("bash.exe", "bash")` name tuple -- is reached by
monkeypatching `os.name` rather than by skipping elsewhere.
"""

from __future__ import annotations

import doctest
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.ci import _bash
from tests.ci._bash import BASH, _is_wsl_launcher, find_bash, requires_bash

pytestmark = pytest.mark.core

# What `shutil.which` is stubbed to return in the tests where the `PATH` walk must be the only
# thing that answers. It is deliberately NOT a `System32` path, so `find_bash` would return it if
# it ever reached the fallback -- which is what makes it a usable sentinel rather than a value
# that merely happens to be filtered out.
_NEVER_REACHED = "/never/reached/bash"


def _plant_bash(directory: Path, name: str = "bash") -> Path:
    """Create a file that `find_bash`'s `is_file()` test will accept as an executable.

    The contents are irrelevant -- nothing here ever runs the planted file -- and no execute bit
    is set, because `find_bash` tests `is_file()` rather than `os.access(..., os.X_OK)`. Planting
    a real file rather than mocking `Path.is_file` keeps the test honest about which path string
    the function builds and returns.

    Args:
        directory: Directory to create (with parents) and plant the file in.
        name: File name to plant, `bash` or `bash.exe`.

    Returns:
        Path: The planted file, for comparing against `find_bash`'s return value.
    """
    directory.mkdir(parents=True, exist_ok=True)
    planted = directory / name
    planted.write_text("#!/bin/sh\n", encoding="utf-8")
    return planted


def _simulate_os_name(monkeypatch, platform: str) -> None:
    """Make `_bash` alone see `os.name == platform`, leaving everyone else's `os` intact.

    `monkeypatch.setattr(os, "name", ...)` is the obvious spelling and it does not work:
    `pathlib` reads `os.name` when it instantiates a path, so claiming to be POSIX on Windows
    makes `Path(entry) / name` -- the very line under test -- raise
    `UnsupportedOperation: cannot instantiate 'PosixPath' on your system`. Replacing the `os`
    the module itself looked up keeps the substitution inside `find_bash`, where the only three
    attributes it reaches for are `name`, `environ` and `pathsep`.

    `environ` is the real mapping rather than a copy, so a `monkeypatch.setenv` in either order
    is still visible through it.

    Args:
        monkeypatch: The pytest fixture, so the substitution is undone with the test.
        platform: The `os.name` value to simulate, `nt` or `posix`.
    """
    monkeypatch.setattr(
        _bash,
        "os",
        SimpleNamespace(name=platform, environ=os.environ, pathsep=os.pathsep),
    )


class TestIsWslLauncher:
    """The predicate that keeps `C:\\Windows\\System32\\bash.exe` out of the result."""

    @pytest.mark.parametrize(
        "candidate, expected",
        [
            ("C:/Windows/System32/bash.exe", True),
            ("C:/Windows/SYSTEM32/bash.exe", True),
            ("C:/windows/system32/bash.exe", True),
            ("C:/Windows/System32/downlevel/bash.exe", True),
            ("C:/Program Files/Git/usr/bin/bash.exe", False),
            ("/usr/bin/bash", False),
            ("/opt/not-system32-really/bin/bash", False),
            ("/opt/system32x/bash", False),
            ("/opt/xsystem32/bash", False),
        ],
    )
    def test_it_matches_a_whole_path_component_case_insensitively(
        self, candidate, expected
    ):
        """Only a path component that IS `system32`, in any casing, is the WSL launcher.

        Args:
            candidate: A `bash` path, written with forward slashes so `Path` splits it into the
                same components on every platform.
            expected: Whether the path must be rejected as the WSL launcher.

        Test scenario:
            Two properties the two docstring examples cannot distinguish. The casing rows pin
            the `.lower()`: Windows paths are reported in whatever case the `PATH` entry used,
            so a case-sensitive comparison would let `SYSTEM32` through. The
            `not-system32-really`, `system32x` and `xsystem32` rows pin the `==`: a substring or
            prefix test would reject a real `bash` that merely lives in a directory whose name
            contains the word, and reporting "no bash on this machine" for a usable one is the
            same class of failure in the other direction.
        """
        assert _is_wsl_launcher(Path(candidate)) is expected, (
            f"{candidate} should {'' if expected else 'not '}read as the WSL launcher"
        )


class TestFindBash:
    """The `PATH` walk, the WSL filter applied to it, and the `shutil.which` fallback."""

    def test_it_returns_the_first_usable_bash_on_path(self, monkeypatch, tmp_path):
        """`PATH` order decides, so the walk must stop at the first match rather than the last.

        Args:
            monkeypatch: Sets `PATH` to two planted directories and stubs the fallback.
            tmp_path: Root for the planted executables, from pytest.

        Test scenario:
            Two directories, each holding a `bash`, in a known `PATH` order. The fallback is
            stubbed to a sentinel it must not return, so a pass also proves the walk answered
            rather than `shutil.which`.
        """
        first = _plant_bash(tmp_path / "first")
        _plant_bash(tmp_path / "second")
        monkeypatch.setenv(
            "PATH",
            os.pathsep.join([str(tmp_path / "first"), str(tmp_path / "second")]),
        )
        monkeypatch.setattr(shutil, "which", lambda _name: _NEVER_REACHED)

        assert find_bash() == str(first), (
            "the walk must return the first PATH entry's bash, not a later one"
        )

    def test_it_skips_the_wsl_launcher_even_when_it_comes_first(
        self, monkeypatch, tmp_path
    ):
        """The regression the module exists for: Git Bash winning must not be `PATH`-order luck.

        Args:
            monkeypatch: Puts a `System32` directory ahead of a real one on `PATH`.
            tmp_path: Root for the planted executables, from pytest.

        Test scenario:
            The hostile ordering -- the WSL launcher first, a real `bash` second -- which is
            exactly what `shutil.which` would resolve to and what made the suite machine
            dependent. The directories are built with `Path`, so the `System32` component is
            there on linux and macOS too and this is not a Windows-only assertion.
        """
        wsl = _plant_bash(tmp_path / "Windows" / "System32", "bash.exe")
        real = _plant_bash(tmp_path / "Git" / "usr" / "bin")
        monkeypatch.setenv("PATH", os.pathsep.join([str(wsl.parent), str(real.parent)]))
        monkeypatch.setattr(shutil, "which", lambda _name: _NEVER_REACHED)

        assert find_bash() == str(real), (
            "a System32 bash earlier on PATH must be skipped, not preferred"
        )

    def test_an_empty_path_entry_is_not_read_as_the_current_directory(
        self, monkeypatch, tmp_path
    ):
        """An empty `PATH` entry means "nothing", and `Path("")` would make it mean "here".

        Args:
            monkeypatch: Empties `PATH`, moves into a directory holding a `bash`, and stubs the
                fallback to None.
            tmp_path: The working directory, which holds the planted `bash`.

        Test scenario:
            `PATH=""` splits to a single empty entry, and `Path("") / "bash"` is the RELATIVE
            `bash` -- so without the `continue` the walk would resolve a file in whatever
            directory the suite happens to run from, and return the bare name `bash` rather than
            a path. A checkout carrying a `bash` file would then silently win over every real
            one. The fallback is stubbed to None so only the walk can answer.
        """
        _plant_bash(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert find_bash() is None, (
            "an empty PATH entry must be skipped, not resolved against the current directory"
        )

    def test_an_unset_path_is_not_an_error(self, monkeypatch, tmp_path):
        """A missing `PATH` must answer None, not raise `KeyError` at import time.

        Args:
            monkeypatch: Removes `PATH` entirely and stubs the fallback to None.
            tmp_path: Unused except to keep the test off the real filesystem.

        Test scenario:
            `BASH = find_bash()` runs at import, so anything this raises is a collection error
            for the whole directory rather than one red test. The `os.environ.get("PATH", "")`
            default is what prevents that, and no machine the suite runs on exercises it.
        """
        monkeypatch.delenv("PATH", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert find_bash() is None, (
            "an unset PATH must resolve to None rather than raise"
        )

    def test_it_falls_back_to_shutil_which_when_the_walk_finds_nothing(
        self, monkeypatch, tmp_path
    ):
        """The fallback keeps the module correct where a distribution installs bash off-`PATH`.

        Args:
            monkeypatch: Points `PATH` at an empty directory and stubs `shutil.which`.
            tmp_path: The empty `PATH` entry, from pytest.

        Test scenario:
            The walk looks only for `<entry>/bash[.exe]`, so a `bash` reachable some other way
            -- a shim, a symlinked name, an entry `shutil.which` expands differently -- is found
            only by the fallback. Nothing on a developer machine or a CI runner takes this
            branch, because the walk succeeds first.
        """
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/bash")

        assert find_bash() == "/usr/local/bin/bash", (
            "a bash the walk cannot see must still be found through shutil.which"
        )

    def test_a_fallback_that_resolves_to_the_wsl_launcher_is_refused(
        self, monkeypatch, tmp_path
    ):
        """The fallback is filtered too, or the WSL launcher re-enters by the back door.

        Args:
            monkeypatch: Points `PATH` at an empty directory and stubs `shutil.which` to the
                WSL launcher.
            tmp_path: The empty `PATH` entry, from pytest.

        Test scenario:
            The one case where filtering the walk is not enough: the walk finds nothing (every
            `bash` on `PATH` is under `System32`, or `PATH` is unusable) and `shutil.which` then
            returns the launcher. Answering None here is what makes the gate in
            `test_bundle_pr_arm.py` fail loudly instead of running the workflow's shell through
            a `bash` that cannot write a Windows `GITHUB_OUTPUT` path.
        """
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(
            shutil, "which", lambda _name: "C:/Windows/System32/bash.exe"
        )

        assert find_bash() is None, (
            "the WSL launcher must be refused on the fallback path as well as on the walk"
        )

    def test_no_bash_anywhere_resolves_to_none(self, monkeypatch, tmp_path):
        """The honest answer on a machine without bash, which the gate turns into a failure.

        Args:
            monkeypatch: Points `PATH` at an empty directory and stubs the fallback to None.
            tmp_path: The empty `PATH` entry, from pytest.

        Test scenario:
            Neither the walk nor the fallback finds anything. This is the value
            `test_bundle_pr_arm.py`'s `_bash_must_be_present` fixture keys off, so returning
            something truthy here would make that gate unreachable.
        """
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert find_bash() is None, (
            "no bash on PATH and none from the fallback means None"
        )

    def test_a_directory_named_bash_is_not_mistaken_for_an_executable(
        self, monkeypatch, tmp_path
    ):
        """`is_file()` rather than `exists()`: a `bash/` directory is not a shell.

        Args:
            monkeypatch: Points `PATH` at the directory holding the decoys and stubs the
                fallback to None.
            tmp_path: Root for the decoy directories, from pytest.

        Test scenario:
            Both searched names are planted as DIRECTORIES. `exists()` would accept either and
            hand `subprocess.run` a path it cannot execute, which surfaces as a `PermissionError`
            from the harness rather than as "no bash found".
        """
        (tmp_path / "bash").mkdir()
        (tmp_path / "bash.exe").mkdir()
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        assert find_bash() is None, (
            "a directory named bash must not be returned as the shell"
        )

    @pytest.mark.parametrize(
        "platform, planted, found",
        [
            ("nt", "bash.exe", True),
            ("nt", "bash", True),
            ("posix", "bash", True),
            ("posix", "bash.exe", False),
        ],
    )
    def test_the_names_searched_follow_the_platform(
        self, platform, planted, found, monkeypatch, tmp_path
    ):
        """`os.name` decides the name tuple, so one of its two branches is dead on any machine.

        Args:
            platform: The `os.name` value to simulate.
            planted: The executable name planted on `PATH`.
            found: Whether `find_bash` must find it under that `os.name`.
            monkeypatch: Simulates `os.name`, sets `PATH`, and stubs the fallback to None.
            tmp_path: The `PATH` entry the file is planted in, from pytest.

        Test scenario:
            The suite runs on ubuntu, macOS and Windows, so whichever branch the runner takes,
            the other is never executed there. Simulating `os.name` covers both everywhere. The
            `posix`/`bash.exe` row is the one that must NOT match: a POSIX run looking for
            `bash.exe` would pick up a Windows-named file on a shared volume. The fallback is
            stubbed to None so the name tuple is the only thing that can answer.
        """
        planted_path = _plant_bash(tmp_path, planted)
        _simulate_os_name(monkeypatch, platform)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        expected = str(planted_path) if found else None

        assert find_bash() == expected, (
            f"os.name={platform!r} with {planted!r} planted should resolve to {expected!r}"
        )

    def test_the_exe_name_is_preferred_on_windows_when_both_exist(
        self, monkeypatch, tmp_path
    ):
        """The name tuple is ordered, and a Git for Windows bin holds both names.

        Args:
            monkeypatch: Simulates `os.name == "nt"`, points `PATH` at the directory, and stubs
                the fallback to a sentinel.
            tmp_path: The `PATH` entry holding both planted names, from pytest.

        Test scenario:
            `("bash.exe", "bash")` is the Windows tuple, and Git for Windows ships `bash.exe`
            beside shim-like extensionless names. Returning the extensionless one to
            `subprocess.run` on Windows is the failure this ordering avoids, and it is invisible
            on a machine where only one of the two names exists.
        """
        exe = _plant_bash(tmp_path, "bash.exe")
        _plant_bash(tmp_path, "bash")
        _simulate_os_name(monkeypatch, "nt")
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(shutil, "which", lambda _name: _NEVER_REACHED)

        assert find_bash() == str(exe), (
            "on Windows bash.exe must win over an extensionless bash in the same directory"
        )


class TestTheModuleLevelGate:
    """`BASH` and `requires_bash`, the two names the rest of `tests/ci` imports."""

    def test_the_resolved_bash_is_usable_or_absent(self):
        """`BASH` is `find_bash()` frozen at import, and must satisfy the same contract.

        Test scenario:
            Asserts the invariant rather than a value, because the right answer differs per
            machine: either None, or a real file that is not the WSL launcher. Deliberately
            does NOT skip when `BASH` is None -- that is the shape this directory's convention
            forbids, and the invariant holds either way.
        """
        assert BASH is None or Path(BASH).is_file(), (
            f"BASH must name an existing file, got {BASH!r}"
        )
        assert BASH is None or not _is_wsl_launcher(Path(BASH)), (
            f"BASH must never be the WSL launcher, got {BASH!r}"
        )
        assert BASH == find_bash(), (
            "BASH must be exactly what find_bash() answers for this machine's PATH"
        )

    def test_the_marker_name_is_the_one_the_gate_looks_for(self):
        """`requires_bash` and the `_bash_must_be_present` fixture are coupled by a string.

        Test scenario:
            `test_bundle_pr_arm.py`'s fixture looks the marker up as
            `request.node.get_closest_marker("bash")`, so renaming the mark here would leave
            every `@requires_bash` class running with no gate at all -- green on a machine
            without bash, and silently not driving the workflow's shell. The name is also the
            one registered in `pyproject.toml` and the one `-m "not bash"` deselects with.
        """
        assert requires_bash.name == "bash", (
            f"the gate looks up the marker 'bash', but requires_bash is {requires_bash.name!r}"
        )


class TestTheHelperExamplesStayExecutable:
    """`lint.yml`'s `static` job runs doctests over `src` only, never over `tests`.

    `test_bundle_pr_arm.py` carries the same class for its own module, for the same reason:
    examples under `tests/` are run by nothing unless a test runs them. `_bash.py` is a second
    module under that blind spot, and its two examples are the only executable statement of
    which path shape counts as the WSL launcher.
    """

    def test_every_docstring_example_in_the_bash_helper_passes(self):
        """Execute `_bash`'s doctests and fail on the first rotted example.

        Test scenario:
            Requires both that examples were found, so the guard cannot pass by collecting
            nothing from a module whose examples were deleted, and that all of them pass.
        """
        failures, attempted = doctest.testmod(_bash, verbose=False, report=False)

        assert attempted > 0, (
            "no doctest examples were collected from tests/ci/_bash.py"
        )
        assert failures == 0, f"{failures} of {attempted} doctest examples failed"
