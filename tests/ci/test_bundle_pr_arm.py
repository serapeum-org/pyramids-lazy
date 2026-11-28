"""Guard the `pull_request` arm of `.github/workflows/bundle-pypi-wheels.yml`.

The native wheel is the one artifact in this repo that CI cannot re-create from source on demand: it
carries a compiled OGR-Parquet driver linked against the libgdal inside a published pyramids-core
wheel, built by three separate per-platform implementations (`build.sh`, `build-macos.sh`,
`build-windows.ps1`). `verify.py` exists to catch a plugin that does not load -- and for as long as the
workflow's `pull_request` trigger stayed commented out it only ever ran *after* a merge. That is not
hypothetical: run 44936415149, the push for PR #43's merge commit, failed four build legs and
`verify windows AMD64` on `main`, and PR #48 existed only to repair it.

Two things therefore have to hold, and neither is visible from reading the trigger alone:

- the arm is live, behind the `paths` filter that keeps it off a PR which does not touch the build;
- the ref `resolve-ref` hands every build job is one `actions/checkout` can actually resolve. On a
  `pull_request` event `github.ref_name` is the unqualified `<pr_number>/merge`, and checkout's
  `getCheckoutInfo` treats an unqualified ref as branch-or-tag: no `refs/heads/54/merge` and no tag of
  that name exist, so it raises "A branch or tag with the name '54/merge' could not be found". Flipping
  the trigger on without a `pull_request` arm in the `case` block fails every build job at step one.

The ref logic is driven through a real `bash` with the workflow's own `run:` script, rather than
reimplemented here: a reimplementation only proves it agrees with itself. Four things are
nevertheless modelled, and each refuses input it cannot represent rather than guessing:
`_substitute` (GitHub expressions), `_paths_filter_selects` (the `paths` glob),
`_case_arms` (the `case` arm styles) and `_checkout_can_resolve` (checkout's ref resolution).

The GitHub-side facts the simulated contexts encode come from the published docs, not from
inspection of a live payload: `github.ref_name` is `<pr_number>/merge` for a pull request that was
not merged, and `github.ref` is `refs/pull/<pr_number>/merge`
(docs.github.com/en/actions/reference/workflows-and-actions/contexts); the falsy values in an
expression are `false, 0, -0, "", '', null`
(docs.github.com/en/actions/reference/workflows-and-actions/expressions).
"""

from __future__ import annotations

import doctest
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.ci._bash import BASH, requires_bash

pytestmark = pytest.mark.core

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bundle-pypi-wheels.yml"

_BASH = BASH


@pytest.fixture(autouse=True)
def _bash_must_be_present(request):
    """Fail a `bash`-marked test when no usable `bash` exists, rather than skipping it.

    The repo's convention is that a test needing something absent is deselected on the command
    line, never folded into a silent skip count -- a skip here would leave the 16 tests that are
    this module's whole point invisible while the suite stayed green.

    Args:
        request: The pytest request, used to read the test's own markers.
    """
    if request.node.get_closest_marker("bash") and _BASH is None:
        pytest.fail(
            "no usable bash found: these tests drive the workflow's own shell, so skipping "
            "them would leave only the YAML parsing covered. Install bash (Git for Windows "
            "provides it) or deselect deliberately with -m 'not bash'."
        )


# `on:` is a YAML 1.1 boolean, so PyYAML keys the trigger block under True rather than "on".
_ON_KEY = True

_EXPRESSION = re.compile(r"\$\{\{(.+?)\}\}")

# The only expression grammar `_substitute` models: a dotted context path, or several joined by
# `||`. Anything richer (a `&&` conditional, a comparison, `format(...)`) must fail loudly. Each
# operand needs at least one `.` so a bare literal cannot slip through: `[\w.]+` also matched
# `true`, `123` and `'main'`-style defaults, which `_substitute` then rendered as the empty string
# via `context.get("true", "")` -- the same guess-instead-of-refuse failure this guard exists to
# stop. Every expression the live step carries is a dotted path, so nothing is lost.
_MODELLED_EXPRESSION = re.compile(r"\w+(?:\.\w+)+(?:\s*\|\|\s*\w+(?:\.\w+)+)*")

# Every character GitHub's filter-pattern syntax treats specially, plus the `!` prefix. A pattern
# carrying any of them is outside what `_paths_filter_selects` models, so it is refused rather than
# answered. `?` is "zero or one of the preceding character", `+` is "one or more" and is POSTFIX --
# so a `startswith("+")` check could never have caught it -- and `[]` is a character class.
_PATHS_GLOB_CHARS = frozenset("*?+[]")

# A `case` arm header: one or more glob/word patterns, optionally single-quoted, joined by `|`,
# then `)`. The charset deliberately excludes `=`, `"` and `$`, so a body line such as
# `REF="$(cat .release-ref/release-ref.txt)"` cannot be mistaken for an arm.
#
# Excluding `"` costs the double-quoted header style: `"pull_request")` is invisible to this
# regex. Restyling an arm that way does not pass silently -- the `;;` cross-check in `_case_arms`
# fails, reading 3 headers against 4 `;;` for one restyled arm and 0 against 4 when all four are
# -- but the message it raises names the terminator count and this regex, not the arm that was
# just edited. Restyle with single quotes, or widen the charset here and re-check that the
# `REF="$(...)"` body line still cannot match.
# A `;;` that ends a line, i.e. an arm terminator rather than the same two characters inside a
# body string. `;&` and `;;&` fall-throughs are deliberately not counted: they are not used here,
# and the count disagreeing with the header count is the loud failure this is for.
_ARM_TERMINATOR = re.compile(r";;\s*$", re.MULTILINE)

_ARM_HEADER = re.compile(
    r"^\s*((?:'?[A-Za-z0-9_*?.\-]+'?)(?:\s*\|\s*'?[A-Za-z0-9_*?.\-]+'?)*)\)",
    re.MULTILINE,
)

# Every event the resolve step must handle: the context that drives it and the ref it must
# produce. `test_every_modelled_event_resolves_its_ref` iterates this, and the arm-coverage gate
# derives its expected arm set from these keys -- so an arm cannot be added to the step without
# either being driven here or failing the gate. `schedule` is not a trigger; it stands in for
# whatever future event reaches the catch-all `*` arm.
_CATCH_ALL_EVENTS = {"schedule"}

_EVENT_CASES: dict[str, tuple[dict[str, str], str]] = {
    "workflow_run": ({"github.event_name": "workflow_run"}, "release/auto-1-1"),
    "workflow_dispatch": (
        {
            "github.event_name": "workflow_dispatch",
            "inputs.branch": "ci/54-bundle-wheels-pr-arm",
            "github.ref_name": "main",
        },
        "ci/54-bundle-wheels-pr-arm",
    ),
    "pull_request": (
        {
            "github.event_name": "pull_request",
            "github.ref_name": "54/merge",
            "github.event.pull_request.number": "54",
        },
        "refs/pull/54/merge",
    ),
    "schedule": ({"github.event_name": "schedule", "github.ref_name": "main"}, "main"),
}


def _workflow() -> dict:
    """Return the parsed bundle workflow.

    Returns:
        dict: The whole workflow document.

    Examples:
        - The document is the workflow's top-level sections, read fresh from disk each call:
            ```python
            >>> _workflow()["name"]
            'Bundle PyPI Wheels'

            ```
        - `on:` parses as the YAML 1.1 boolean, never the string, which is what `_ON_KEY` is
          for:
            ```python
            >>> _ON_KEY in _workflow()
            True
            >>> "on" in _workflow()
            False

            ```

    See Also:
        _triggers: The `on:` block alone, reached through `_ON_KEY`.
    """
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    """Return the workflow's `on:` block.

    Returns:
        dict: Event name to its configuration (`{}` for a bare event).

    Examples:
        - The four events that start this workflow:
            ```python
            >>> sorted(_triggers())
            ['pull_request', 'push', 'workflow_dispatch', 'workflow_run']

            ```
        - Each event's value is its own filter block; `pull_request` carries both of its:
            ```python
            >>> sorted(_triggers()["pull_request"])
            ['branches', 'paths']
            >>> _triggers()["pull_request"]["branches"]
            ['main']

            ```

    See Also:
        _paths_filter_selects: Models the `paths` filter this returns.
    """
    return _workflow()[_ON_KEY]


def _resolve_script() -> str:
    """Return the shell of `resolve-ref`'s "Resolve the ref to build" step.

    Returns:
        str: The step's `run:` script, expressions still unsubstituted.

    Raises:
        AssertionError: If the step is renamed or removed, which would silently void this guard.
    """
    steps = _workflow()["jobs"]["resolve-ref"]["steps"]
    for step in steps:
        if step.get("id") == "ref":
            return step["run"]
    raise AssertionError("resolve-ref has no step with id 'ref'")


def _substitute(script: str, context: dict[str, str]) -> str:
    """Replace every `${{ ... }}` in `script` with its value from `context`.

    Models the two expression forms the step uses: a bare context lookup, and `a || b` falling
    through to the first non-empty value. A key absent from `context` renders empty, which is what
    GitHub does for a context property the current event does not carry.

    Falsiness here is emptiness only, and that is the single place this helper approximates rather
    than refuses. GitHub's documented falsy list is `false, 0, -0, "", '', null`; this model treats
    only `""` as falsy, so a `||` whose left operand is the string `false`, `0`, `-0` or `null`
    returns that string where GitHub would fall through to the right operand. That divergence is
    not reachable from the resolve step as written: its only `||` is
    `inputs.branch || github.ref_name`, and `inputs.branch` is declared `type: string` with
    `default: ""`. It stays a boundary rather than a bug only while that holds --
    `inputs.dry_run_release` is a `type: boolean` input of this same workflow (read by the release
    job's `if`, which this file compares as text instead of substituting), so a boolean context
    reaching this step is the one rewrite that would make the model quietly wrong instead of loud.

    Any other expression form -- a `&&`/`||` conditional, a comparison, a function call such as
    `format(...)` -- RAISES rather than being approximated. The previous version split on `||` and
    looked each operand up as a key, so a conditional silently resolved to its fallback and a
    `format(...)` call to the empty string, which would have made this model a fiction the first
    time someone rewrote an arm.

    Args:
        script: Shell carrying `${{ }}` expressions.
        context: Expression path (e.g. `github.ref_name`) to its value for the simulated event.

    Returns:
        str: The script with every expression replaced.

    Raises:
        AssertionError: If an expression is not a bare lookup or a `||` chain of bare lookups.

    Examples:
        - A single context lookup is replaced in place:
            ```python
            >>> _substitute('REF="${{ github.ref_name }}"', {"github.ref_name": "main"})
            'REF="main"'

            ```
        - `a || b` falls through to the first non-empty operand:
            ```python
            >>> _substitute(
            ...     "${{ inputs.branch || github.ref_name }}",
            ...     {"inputs.branch": "", "github.ref_name": "main"},
            ... )
            'main'

            ```
        - A property the event does not carry renders empty, as GitHub does:
            ```python
            >>> _substitute("[${{ github.event.pull_request.number }}]", {})
            '[]'

            ```
        - The modelled boundary: only `""` is falsy here, so a string-valued `false` is
          returned rather than falling through as GitHub would:
            ```python
            >>> _substitute(
            ...     "${{ inputs.dry_run_release || github.ref_name }}",
            ...     {"inputs.dry_run_release": "false", "github.ref_name": "main"},
            ... )
            'false'

            ```

    See Also:
        _resolve_ref: Runs the substituted script through a real bash.
    """

    def _value(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        assert _MODELLED_EXPRESSION.fullmatch(body), (
            f"unmodelled GitHub expression {body!r}: this helper models only a bare context "
            "lookup and a `||` chain of them. Extend it deliberately rather than letting it "
            "guess."
        )
        for operand in (part.strip() for part in body.split("||")):
            resolved = context.get(operand, "")
            if resolved:
                return resolved
        return ""

    return _EXPRESSION.sub(_value, script)


def _run_resolve(
    context: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the real resolve step for a simulated event and return the process and its outputs.

    Returns the process rather than asserting on it, so a test can distinguish the step failing
    (the behaviour under test) from the harness failing. The environment is inherited with two
    overrides: an earlier version passed `PATH=/usr/bin:/bin` and nothing else, which made an
    unresolvable `cat` or a non-Git `bash` on Windows indistinguishable from the step's own error,
    and dropped `SystemRoot`/`HOME` for no benefit.

    `LC_ALL=C` is the second override, and it is load-bearing:
    `test_a_release_with_no_uploaded_ref_fails_the_step` asserts `cat`'s English
    "No such file or directory". Inheriting a localized `LANG`/`LC_ALL` would translate that
    message and fail the test for a reason unrelated to the workflow. CI runners are `C.UTF-8`, so
    this only ever bit a localized developer machine -- which is precisely the kind of failure that
    is maddening to diagnose.

    Args:
        context: The expression context for the event (see `_substitute`).
        cwd: Directory to run in; the `workflow_run` path reads `.release-ref/release-ref.txt`
            relative to it.

    Returns:
        tuple: The finished process, and the `ref=` lines it appended to `GITHUB_OUTPUT`.

    Raises:
        AssertionError: If no `bash` was found on this machine. Every test that reaches this
            sits in a `@requires_bash` class, so the assertion firing means that guard was
            dropped rather than that the machine is unusual.

    See Also:
        _resolve_ref: The strict wrapper to use when the step is expected to succeed.
        _substitute: Renders the step's `${{ }}` expressions before it is run.
    """
    assert _BASH is not None
    output = cwd / "github_output.txt"
    output.touch()
    env = {**os.environ, "GITHUB_OUTPUT": output.as_posix(), "LC_ALL": "C"}
    result = subprocess.run(
        [_BASH, "-c", _substitute(_resolve_script(), context)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )
    written = [
        line
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("ref=")
    ]
    return result, written


def _resolve_ref(context: dict[str, str], cwd: Path) -> str:
    """Run the resolve step for an event it must handle and return the ref it writes.

    Args:
        context: The expression context for the event (see `_substitute`).
        cwd: Directory to run in.

    Returns:
        str: The value the step appends to `GITHUB_OUTPUT` as `ref=`. Command substitution
            strips the trailing newline, so the `workflow_run` path yields the artifact's
            single line without it.

    Raises:
        AssertionError: If the step exits non-zero, or writes no `ref=` line.

    See Also:
        _run_resolve: Use that directly when the step is expected to fail.
    """
    result, written = _run_resolve(context, cwd)

    assert result.returncode == 0, f"resolve step failed: {result.stderr}"
    assert written, f"the step wrote no ref= line (stdout: {result.stdout})"
    return written[-1].removeprefix("ref=")


def _checkout_can_resolve(ref: str) -> bool:
    """Whether `actions/checkout` can resolve `ref` without a same-named branch or tag.

    Follows `getCheckoutInfo` in checkout's `src/ref-helper.ts`: a ref starting with `refs/` is
    taken as fully qualified, anything else has to exist as `origin/<ref>` or as a tag.

    What it models is exactly that qualified/unqualified split, with one deliberate tightening: the
    comparison is case-sensitive, which `getCheckoutInfo` is not (it upper-cases the ref first).
    Git refs are case-sensitive, so treating `Refs/pull/54/merge` as qualified would be generous.

    It does NOT model whether the job ultimately succeeds. `refs/notes/x`,
    `refs/remotes/origin/main` and even `refs/` all answer True here and would reach checkout's
    bare-`REFS/` branch and then fail to fetch. None of them can reach this helper: the only values
    the step produces are `refs/pull/<n>/merge`, a branch name, and the release-ref artifact's
    contents.

    Args:
        ref: The value passed to the action's `ref:` input.

    Returns:
        bool: True when the ref is fully qualified.

    Examples:
        - The qualified merge ref this workflow now builds from:
            ```python
            >>> _checkout_can_resolve("refs/pull/54/merge")
            True

            ```
        - The unqualified form `github.ref_name` yields on a pull request:
            ```python
            >>> _checkout_can_resolve("54/merge")
            False

            ```
        - A branch name is unqualified too; checkout accepts it only because
          `origin/main` exists, which this predicate deliberately does not model:
            ```python
            >>> _checkout_can_resolve("main")
            False

            ```
        - Case matters, because git refs are case-sensitive even though checkout's
          prefix test is not:
            ```python
            >>> _checkout_can_resolve("Refs/pull/54/merge")
            False

            ```
    """
    return ref.startswith("refs/")


def _case_span() -> str:
    """Return the text between the resolve step's `case ... in` and its `esac`.

    Returns:
        str: The arm block only, so a `)` elsewhere in the step cannot be read as an arm.

    Raises:
        AssertionError: If the step has no `case ... in` / `esac` pair.
    """
    span = re.search(r"case\b.*?\bin\n(.*?)\n\s*esac\b", _resolve_script(), re.DOTALL)
    assert span is not None, "the resolve step has no `case ... in` / `esac` block"
    return span.group(1)


def _case_arms() -> set[str]:
    """Return every event pattern the resolve step's `case` block branches on.

    Parses the `case ... esac` span rather than scanning the whole step, and accepts the arm
    styles `case` allows but this step does not currently use -- a pattern on its own line with
    its body beneath, an alternation (`a|b)`), and a single-quoted pattern -- because the earlier
    one-line-only regex returned the same four names for all of those and let a new arm pass
    unnoticed. The arm count is cross-checked against the `;;` terminators so a parse that reads
    too few arms fails instead of agreeing with the expected set.

    Double quotes are the one style deliberately left out: `_ARM_HEADER` excludes `"` so that the
    body line `REF="$(cat .release-ref/release-ref.txt)"` cannot be read as an arm, which also
    makes a header written `"pull_request")` invisible. Restyling arms that way fails the `;;`
    cross-check below instead of returning a wrong arm set -- but the failure names the terminator
    count and the regex, not the arm that was edited, so a maintainer who has just restyled one
    arm should read it as "restyle with single quotes", not as a broken test.

    Returns:
        set[str]: One entry per pattern, alternations split and quotes stripped, `*` included.

    Raises:
        AssertionError: If the number of arm headers found does not match the `;;` count.

    Examples:
        - The step's four arms, read off the live workflow:
            ```python
            >>> sorted(_case_arms())
            ['*', 'pull_request', 'workflow_dispatch', 'workflow_run']

            ```
    """
    span = _case_span()
    headers = _ARM_HEADER.findall(span)
    terminators = len(_ARM_TERMINATOR.findall(span))
    assert len(headers) == terminators, (
        f"parsed {len(headers)} case arms but found {terminators} arm terminators; the arm regex "
        "or the terminator style no longer matches the step. Counting `;;` only at end of line "
        "keeps a body string such as `REF=\"${A};;${B}\"` from inflating this, but a `;&` "
        "fall-through terminator would deflate it."
    )
    return {
        pattern.strip().strip("'\"")
        for header in headers
        for pattern in header.split("|")
    }


def _paths_filter_selects(path: str) -> bool:
    """Whether a PR changing `path` alone would start the workflow.

    Models the `on.pull_request.paths` filter: a trailing `<dir>/**` pattern matches anything
    beneath that directory, any other pattern is a literal path. Those are the only two forms the
    live filter uses, so this stays a model of what is written rather than a reimplementation of
    GitHub's matcher -- and it RAISES on anything outside those two forms rather than answering.

    That guard is the point, and getting it half right was worse than not having it. The first
    version refused only a leading `!`, and then only `*` on the `/**` branch and `*?[` on the
    literal branch, so `ci/plugin?/**`, `ci/plugin[ab]/**`, `ci/par+quet-plugin/**` and `setup.p+y`
    all returned `False` where GitHub would have selected the path -- reporting "this PR will not
    build" for a filter under which it would. The whole pattern is now checked against
    `_PATHS_GLOB_CHARS` before the `/**` split, so one shared guard covers both branches and every
    special character, `+` included (it is postfix, so the old prefix test could never catch it).

    A `!pattern` negation is refused for a different reason: it removes paths an earlier pattern
    matched, which is a relationship between patterns this model has no way to represent. Before
    the guard, `["ci/parquet-plugin/**", "!ci/parquet-plugin/verify.py"]` was modelled as still
    building `verify.py` while GitHub would not have run the workflow at all.

    The `/**` branch below strips `**` and KEEPS the separator, so the prefix it compares against
    is `ci/parquet-plugin/`. `pattern[: -len("/**")]` looks like the tidier spelling of the same
    thing and is not: it leaves `ci/parquet-plugin`, which `str.startswith` also matches for
    `ci/parquet-plugin-notes/build.sh`, `ci/parquet-plugin.bak/build.sh` and `ci/parquet-pluginx`
    -- siblings GitHub would not select, so the model would promise a build for a PR that cannot
    touch the wheel. `test_a_sibling_sharing_the_directory_prefix_is_not_selected` is the only
    thing that notices; every other negative case is outside `ci/` entirely.

    Args:
        path: A repository-relative path, as a changed-file entry.

    Returns:
        bool: True when at least one pattern selects the path.

    Raises:
        AssertionError: If the filter contains a pattern this model cannot represent.

    Examples:
        - A build script beneath a `<dir>/**` pattern is selected:
            ```python
            >>> _paths_filter_selects("ci/parquet-plugin/build.sh")
            True

            ```
        - A literal pattern selects that exact path:
            ```python
            >>> _paths_filter_selects("setup.py")
            True

            ```
        - Library code cannot change the wheel, so it pays nothing:
            ```python
            >>> _paths_filter_selects("src/pyramids_lazy/_zarr.py")
            False

            ```
    """
    selected = False
    for pattern in _triggers()["pull_request"]["paths"]:
        assert not pattern.startswith("!"), (
            f"unmodelled paths pattern {pattern!r}: negation changes what earlier patterns "
            "selected, which this model does not represent. Extend it before using it to "
            "justify the filter."
        )
        body = pattern[: -len("/**")] if pattern.endswith("/**") else pattern
        assert not _PATHS_GLOB_CHARS & set(body), (
            f"unmodelled paths pattern {pattern!r}: the only glob this models is a trailing "
            f"`/**`, and {sorted(_PATHS_GLOB_CHARS & set(body))} is not that. Extend it before "
            "using it to justify the filter."
        )
        if pattern.endswith("/**"):
            if path.startswith(pattern[: -len("**")]):
                selected = True
        elif path == pattern:
            selected = True
    return selected


class TestThePullRequestArmIsLive:
    """The trigger itself -- the thing issue #54 reported dormant."""

    def test_the_workflow_runs_on_a_pull_request(self):
        """A commented-out `pull_request:` leaves the key absent entirely."""
        assert "pull_request" in _triggers(), "the pull_request arm is dormant again"

    def test_it_targets_main(self):
        """Only PRs into `main` build wheels; a stacked branch PR is not the release path."""
        assert _triggers()["pull_request"]["branches"] == ["main"]

    def test_it_is_filtered_to_the_paths_that_decide_the_wheel(self):
        """The filter is what keeps ~95 job-minutes off a PR that cannot change the artifact."""
        assert _triggers()["pull_request"]["paths"] == [
            "pyproject.toml",
            "src/pyramids_lazy/__init__.py",
            "ci/parquet-plugin/**",
            "setup.py",
            ".github/workflows/bundle-pypi-wheels.yml",
        ]

    def test_pushes_to_main_still_build(self):
        """The arm is additive: a merge must keep building and verifying, as before."""
        assert _triggers()["push"]["branches"] == ["main"]

    def test_no_other_event_starts_the_workflow(self):
        """95 job-minutes a run: a stray `schedule` or `pull_request_target` would be costly.

        `pull_request_target` would also run a fork PR's build scripts with this repo's secrets,
        so its absence is a security property, not only a cost one.
        """
        assert set(_triggers()) == {
            "push",
            "pull_request",
            "workflow_run",
            "workflow_dispatch",
        }


@requires_bash
class TestTheResolvedRefIsCheckoutable:
    """`resolve-ref`'s output feeds `actions/checkout`'s `ref:` in every build job."""

    def test_a_pull_request_resolves_to_a_fully_qualified_merge_ref(self, tmp_path):
        """The regression this guards: `github.ref_name` alone is `54/merge`, which checkout rejects.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A `pull_request` event on PR 54, where `github.ref_name` is `54/merge` and
            `github.event.pull_request.number` is `54`.

            `_EVENT_CASES` drives this event too; this stays as the named, readable statement of
            the regression the whole change exists for.
        """
        resolved = _resolve_ref(
            {
                "github.event_name": "pull_request",
                "github.ref_name": "54/merge",
                "github.event.pull_request.number": "54",
            },
            tmp_path,
        )

        assert resolved == "refs/pull/54/merge"
        assert _checkout_can_resolve(resolved)
        assert resolved != "54/merge", (
            "an unqualified merge ref fails every build job at checkout"
        )

    @pytest.mark.parametrize("number", ["1", "1234"])
    def test_any_pr_number_is_qualified_the_same_way(self, number, tmp_path):
        """The number is interpolated, so a 1- or 4-digit PR must qualify like the 54 above.

        Args:
            number: The pull request number for the simulated event.
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A 1-digit and a 4-digit number, because the arm builds the ref by interpolating
            `github.event.pull_request.number` into `refs/pull/<n>/merge` rather than matching a
            shape -- so nothing about the digit count may change the result.
        """
        resolved = _resolve_ref(
            {
                "github.event_name": "pull_request",
                "github.ref_name": f"{number}/merge",
                "github.event.pull_request.number": number,
            },
            tmp_path,
        )

        assert resolved == f"refs/pull/{number}/merge"
        assert _checkout_can_resolve(resolved)

    def test_a_push_still_resolves_to_the_branch_name(self, tmp_path):
        """Unqualified is correct here: `origin/main` exists, so checkout resolves it.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A `push` event, which has no arm of its own -- the step's arms are `workflow_run`,
            `workflow_dispatch`, `pull_request` and `*`, so a push reaches the catch-all and
            takes `github.ref_name`. The ref it yields is therefore the same one
            `test_an_unrecognised_event_falls_back_to_the_triggering_ref` asserts, reached by the
            same arm; this test names the trigger that actually relies on it.
        """
        resolved = _resolve_ref(
            {"github.event_name": "push", "github.ref_name": "main"},
            tmp_path,
        )

        assert resolved == "main"

    def test_a_dispatch_prefers_the_requested_branch(self, tmp_path):
        """`inputs.branch || github.ref_name` -- the manual pre-merge build path.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A dispatch that fills `inputs.branch` while `github.ref_name` is `main`, so a pass
            proves the left operand wins rather than both happening to agree. This is the only
            `||` in the step, and both of its operands are string-valued -- the reason
            `_substitute`'s emptiness-only falsiness is sufficient here.

            Deliberately overlaps the `workflow_dispatch` row of `_EVENT_CASES`: the row proves the
            arm is exercised, this names the build path that depends on it.
        """
        resolved = _resolve_ref(
            {
                "github.event_name": "workflow_dispatch",
                "inputs.branch": "ci/54-bundle-wheels-pr-arm",
                "github.ref_name": "main",
            },
            tmp_path,
        )

        assert resolved == "ci/54-bundle-wheels-pr-arm"

    def test_a_dispatch_without_a_branch_falls_back_to_the_selected_ref(self, tmp_path):
        """Blank input means "the ref chosen in the UI".

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            The context omits `inputs.branch` entirely, which `_substitute` renders as empty --
            the same thing the input's declared `default: ""` produces when the dispatch form is
            submitted untouched.
        """
        resolved = _resolve_ref(
            {"github.event_name": "workflow_dispatch", "github.ref_name": "main"},
            tmp_path,
        )

        assert resolved == "main"

    def test_a_release_still_resolves_to_the_uploaded_release_ref(self, tmp_path):
        """The bump lives on the release branch, not `head_branch`; that artifact is its only source.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            Writes the `.release-ref/release-ref.txt` the job's `download-artifact` step fetches,
            then checks the arm reads it. The file is written with a trailing newline while the
            expected ref has none: command substitution strips it, which is why the arm needs no
            explicit trim.

            Deliberately overlaps the `workflow_run` row of `_EVENT_CASES`, for the same reason: the
            row proves the arm is driven, this says what the arm is for.
        """
        (tmp_path / ".release-ref").mkdir()
        (tmp_path / ".release-ref" / "release-ref.txt").write_text(
            "release/auto-1-1\n", encoding="utf-8"
        )

        resolved = _resolve_ref({"github.event_name": "workflow_run"}, tmp_path)

        assert resolved == "release/auto-1-1"


class TestAPullRequestCannotPublish:
    """A PR now triggers the workflow, so the publish gate carries real weight."""

    def test_the_release_job_requires_a_release_run_or_a_dry_run_dispatch(self):
        """`pull_request` satisfies neither branch of the `if`, so nothing is uploaded from a PR.

        Test scenario:
            Compares the whole normalised condition, not substrings. A substring check stays green
            when a third disjunct is added -- including one a pull request could satisfy -- which
            is the only way this gate can quietly stop holding.
        """
        condition = " ".join(_workflow()["jobs"]["release"]["if"].split())

        assert condition == (
            "(github.event_name == 'workflow_run' && "
            "github.event.workflow_run.conclusion == 'success' && "
            "github.event.workflow_run.head_repository.full_name == github.repository) || "
            "(github.event_name == 'workflow_dispatch' && inputs.dry_run_release)"
        )

    def test_the_release_job_needs_every_build_and_verify_job(self):
        """A red verify must stop a publish, not merely colour the run.

        Test scenario:
            The eleven jobs the artifact's correctness depends on -- the four artifact-building
            jobs (three wheel builds plus `sdist`), all five verify jobs, the `test-native-wheel`
            suite run against the published wheel, and the `verify-sdist-pure-backend` check.
            `resolve-ref` is deliberately absent: `release` reads no `needs.resolve-ref`
            expression, so it is one of the two jobs
            `test_the_set_of_such_jobs_is_the_expected_twelve` leaves out.
        """
        assert set(_workflow()["jobs"]["release"]["needs"]) == {
            "build-linux-wheels",
            "build-macos-wheels",
            "build-windows-wheels",
            "sdist",
            "verify-debian12",
            "verify-rocky9",
            "verify-rocky9-aarch64",
            "verify-macos",
            "verify-windows",
            "test-native-wheel",
            "verify-sdist-pure-backend",
        }


class TestSupersededPullRequestRunsAreCancelled:
    """95 job-minutes a run makes the hosted-runner pool the scarce resource."""

    def test_only_pull_requests_share_a_cancellable_group(self):
        """A push or release gets the unique `run_id` group, so a merge is always built to completion.

        Test scenario:
            Asserts the conditional as a substring because the group is prefixed with
            `${{ github.workflow }}`, and compares `cancel-in-progress` whole. The group keys a
            pull request on `github.ref`, which stays `refs/pull/<n>/merge` across pushes to that
            PR -- so a new push shares the group and cancels the run it supersedes. This
            expression is also a form `_substitute` refuses, which costs nothing: it is read as
            text here and never run through the model.
        """
        concurrency = _workflow()["concurrency"]

        assert (
            "github.event_name == 'pull_request' && github.ref || github.run_id"
            in concurrency["group"]
        )
        assert (
            concurrency["cancel-in-progress"]
            == "${{ github.event_name == 'pull_request' }}"
        )


class TestThePathsFilterSelectsOnlyBuildTouchingPullRequests:
    """The filter is the whole cost control: it decides which PRs pay for a wheel build."""

    @pytest.mark.parametrize(
        "path",
        [
            "ci/parquet-plugin/build.sh",
            "ci/parquet-plugin/build-macos.sh",
            "ci/parquet-plugin/build-windows.ps1",
            "ci/parquet-plugin/lib.sh",
            "ci/parquet-plugin/soname.py",
            "ci/parquet-plugin/fetch_cpl_config.py",
            "ci/parquet-plugin/verify.py",
            "setup.py",
            "pyproject.toml",
            "src/pyramids_lazy/__init__.py",
            ".github/workflows/bundle-pypi-wheels.yml",
        ],
    )
    def test_a_file_that_decides_the_wheel_starts_the_build(self, path):
        """Every input the published artifact is built from must be covered.

        Args:
            path: A changed file that can alter the wheel or how it is verified.

        Test scenario:
            The six build scripts named in issue #54, plus `verify.py`, `setup.py` (which forces
            the `py3-none-<plat>` tag), `pyproject.toml` (which holds `[tool.cibuildwheel]`'s
            `before-all` / `repair-wheel-command` entries and the `package-data` line that puts
            the driver in the wheel), `src/pyramids_lazy/__init__.py` (whose Windows block
            prepends the installed `gdalplugins/` directory to `PATH`, the only thing that lets
            GDAL resolve the plugin's self-vendored Arrow DLLs) and the workflow itself.
        """
        assert _paths_filter_selects(path), (
            f"{path} changes the wheel but would not build it"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "src/pyramids_lazy/_zarr.py",
            "tests/ci/test_bundle_pr_arm.py",
            "docs/index.md",
            "README.md",
            ".github/workflows/tests.yml",
        ],
    )
    def test_an_unrelated_file_does_not(self, path):
        """A PR that cannot change the artifact must not pay 95 job-minutes.

        Args:
            path: A changed file outside the native-build inputs.

        Test scenario:
            A library module, this test, docs, and a sibling workflow. None of these reaches
            the native build. Note the exclusion is per file, not "`src/` is library code": the
            package's `__init__.py` performs the Windows plugin registration and is therefore a
            positive case above, while `_zarr.py` is pure Python the wheel carries either way.
            `pyproject.toml` is likewise NOT in this list -- it configures cibuildwheel and the
            packaging.
        """
        assert not _paths_filter_selects(path), f"{path} cannot change the wheel"

    @pytest.mark.parametrize(
        "path",
        [
            "ci/parquet-plugin-notes/build.sh",
            "ci/parquet-plugin.bak/build.sh",
            "ci/parquet-pluginx",
        ],
    )
    def test_a_sibling_sharing_the_directory_prefix_is_not_selected(self, path):
        """`ci/parquet-plugin/**` must match the directory, not everything starting with its name.

        Args:
            path: A path under a sibling whose name begins with `ci/parquet-plugin`.

        Test scenario:
            The `/**` branch compares `path.startswith(pattern[: -len("**")])`, i.e. against
            `ci/parquet-plugin/` WITH the separator. Stripping `/**` instead of `**` -- the
            obvious-looking tidy-up, since the pattern ends in `/**` -- leaves the prefix
            `ci/parquet-plugin` and silently widens the filter to every sibling directory
            sharing that name. Every path in `test_an_unrelated_file_does_not` is outside `ci/`
            entirely, so nothing there notices; these three are the shapes that do.

            The direction matters: this is the expensive one. GitHub would not run the workflow
            for any of these, so a wrongly-widened prefix spends ~95 job-minutes on a PR that
            cannot touch the wheel.
        """
        assert not _paths_filter_selects(path), (
            f"{path} is outside ci/parquet-plugin/ and must not start a build"
        )


class TestTheModelsRefuseWhatTheyCannotRepresent:
    """A model that quietly approximates is worse than one that stops."""

    def test_an_expression_form_the_step_does_not_use_is_rejected(self):
        """A conditional silently resolved to its fallback before this guard existed.

        Test scenario:
            `${{ A == 'x' && github.ref || github.run_id }}` previously returned the `run_id`
            fallback regardless of the condition, and `format(...)` returned the empty string.
        """
        with pytest.raises(AssertionError, match="unmodelled GitHub expression"):
            _substitute(
                "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
                {"github.ref": "refs/pull/54/merge", "github.run_id": "999"},
            )

    @pytest.mark.parametrize("literal", ["true", "123", "main", "'main'"])
    def test_a_bare_literal_operand_is_rejected(self, literal):
        """A literal default renders as `''` through `context.get`, where GitHub renders the value.

        Args:
            literal: An operand that is not a dotted context path.

        Test scenario:
            `${{ inputs.branch || 'main' }}` is the plausible rewrite: the old grammar accepted the
            literal and resolved it to the empty string rather than refusing it.
        """
        with pytest.raises(AssertionError, match="unmodelled GitHub expression"):
            _substitute(
                "${{ inputs.branch || " + literal + " }}", {"inputs.branch": ""}
            )

    def test_a_function_call_expression_is_rejected(self):
        """`format(...)` is a plausible rewrite of the new arm, and was modelled as empty.

        Test scenario:
            The live `pull_request` arm builds its ref by concatenation,
            `refs/pull/${{ github.event.pull_request.number }}/merge`; `format()` is the obvious
            tidier spelling of exactly that, so it is the rewrite most likely to arrive. The
            expression here spells the whole ref, and the old lookup-by-key model resolved it to
            the empty string -- an empty `REF`, the silent-default-branch outcome
            `TestTheResolveStepFailsLoudlyRatherThanResolvingNothing` is named for.
        """
        with pytest.raises(AssertionError, match="unmodelled GitHub expression"):
            _substitute(
                "${{ format('refs/pull/{0}/merge', github.event.pull_request.number) }}",
                {"github.event.pull_request.number": "54"},
            )

    def test_only_trusted_contexts_are_interpolated_into_the_shell(self):
        """No attacker-controlled string may reach the step's shell, now that forks can trigger it.

        Test scenario:
            The step interpolates `${{ }}` straight into shell, and enabling `pull_request` made
            this workflow reachable by a pull request from a fork -- so "every interpolated field
            is one an outsider cannot set" became load-bearing and untested. It holds today:
            `github.event.pull_request.number` is numeric, `github.ref_name` on a pull request is
            `<n>/merge` derived from that number, and `inputs.branch` needs write access to
            dispatch. `github.head_ref` -- the fork's own branch name, free text chosen by the PR
            author -- is deliberately not used, and this test is what fails if an arm added later
            reaches for it.
        """
        trusted = {
            "github.event_name",
            "github.ref_name",
            "inputs.branch",
            "github.event.pull_request.number",
        }

        read = {
            operand.strip()
            for body in _EXPRESSION.findall(_resolve_script())
            for operand in body.split("||")
        }

        assert read <= trusted, (
            f"untrusted context(s) interpolated into the step: {sorted(read - trusted)}"
        )

    def test_the_live_step_only_uses_modelled_expressions(self):
        """The guard above is only useful if the step itself stays inside the modelled grammar.

        Test scenario:
            The step currently carries four expressions -- `github.event_name`,
            `inputs.branch || github.ref_name`, `github.event.pull_request.number` and
            `github.ref_name` -- one `||` chain and three bare lookups, all inside the grammar.
            An expression added outside it fails here rather than being silently approximated by
            every test that runs the step.
        """
        for body in _EXPRESSION.findall(_resolve_script()):
            assert _MODELLED_EXPRESSION.fullmatch(body.strip()), body

    def test_a_negated_paths_pattern_is_rejected(self, monkeypatch):
        """Negation is the one filter change that stayed green under the old model.

        Args:
            monkeypatch: Replaces the live filter with one carrying a `!` pattern.

        Test scenario:
            `["ci/parquet-plugin/**", "!ci/parquet-plugin/verify.py"]` would stop `verify.py`
            changes from building, yet the old model still reported it as selected.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_triggers",
            lambda: {
                "pull_request": {
                    "paths": ["ci/parquet-plugin/**", "!ci/parquet-plugin/verify.py"]
                }
            },
        )

        with pytest.raises(AssertionError, match="unmodelled paths pattern"):
            _paths_filter_selects("ci/parquet-plugin/verify.py")

    @pytest.mark.parametrize(
        "pattern, path",
        [
            ("ci/plugin?/**", "ci/plugin/x"),
            ("ci/plugin[ab]/**", "ci/plugina/x"),
            ("ci/par+quet-plugin/**", "ci/parquet-plugin/x"),
            ("setup.p+y", "setup.py"),
        ],
    )
    def test_every_glob_special_character_is_rejected(self, pattern, path, monkeypatch):
        """`?`, `[]` and the POSTFIX `+` were all answered `False` where GitHub would select.

        Args:
            pattern: A `paths` entry using a GitHub glob special this model cannot represent.
            path: A changed file GitHub's matcher would select under that pattern.
            monkeypatch: Replaces the live filter with the one-pattern filter under test.

        Test scenario:
            The dangerous direction: each of these reported "this PR will not build" for a filter
            under which GitHub *would* build, so widening the filter with one of them read as a
            cost saving that was not real.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_triggers",
            lambda: {"pull_request": {"paths": [pattern]}},
        )

        with pytest.raises(AssertionError, match="unmodelled paths pattern"):
            _paths_filter_selects(path)

    def test_a_non_trailing_glob_is_rejected(self, monkeypatch):
        """`ci/*/build.sh` is a legal filter and not something this model represents.

        Args:
            monkeypatch: Replaces the live filter with one carrying an interior glob.

        Test scenario:
            The pattern does not end in `/**`, so it takes the literal branch of the model,
            where an unguarded glob would be compared with `==` and silently match nothing.
            `test_an_interior_glob_before_a_trailing_double_star_is_rejected` covers the other
            branch.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_triggers",
            lambda: {"pull_request": {"paths": ["ci/*/build.sh"]}},
        )

        with pytest.raises(AssertionError, match="unmodelled paths pattern"):
            _paths_filter_selects("ci/parquet-plugin/build.sh")

    def test_an_interior_glob_before_a_trailing_double_star_is_rejected(
        self, monkeypatch
    ):
        """A pattern can end in `/**` and still carry a glob this model cannot read.

        Args:
            monkeypatch: Replaces the live filter with `ci/*/plugin/**`.

        Test scenario:
            One shared guard now checks the whole pattern, with a trailing `/**` stripped
            first, so this and `test_a_non_trailing_glob_is_rejected` reach it by the two
            different pattern shapes rather than through two separate asserts. This shape
            is the one that would otherwise slip past: the prefix is compared with
            `str.startswith`, so `ci/*/plugin/` would match only a path containing a
            literal asterisk and every path GitHub selects would read as not building.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_triggers",
            lambda: {"pull_request": {"paths": ["ci/*/plugin/**"]}},
        )

        with pytest.raises(AssertionError, match=r"unmodelled paths pattern.*\['\*'\]"):
            _paths_filter_selects("ci/parquet-plugin/plugin/build.sh")


class TestTheExpressionModelReadsTheSpellingsItAccepts:
    """`_MODELLED_EXPRESSION` is written to tolerate spellings the live step does not use.

    The grammar allows any whitespace around `||` and any number of operands, while the live
    step carries exactly one `||` with a single space either side. So the tolerance is dead code
    against the workflow as written -- and a tolerance nothing exercises is one that breaks the
    first time the step is reformatted, which is the same trap `_case_arms`' arm styles were
    widened to avoid.
    """

    @pytest.mark.parametrize(
        "body, spelling",
        [
            ("inputs.branch||github.ref_name", "no spaces around the pipes"),
            ("inputs.branch  ||  github.ref_name", "extra spaces around the pipes"),
            (
                "inputs.branch || inputs.tag || github.ref_name",
                "a three-operand chain",
            ),
        ],
    )
    def test_a_pipe_chain_is_read_whatever_its_spacing_and_length(self, body, spelling):
        """Each spelling must resolve through to the first non-empty operand.

        Args:
            body: The expression body, inside the `${{ }}` the test wraps it in.
            spelling: How it is written, for the failure message.

        Test scenario:
            All three resolve to `github.ref_name`, so a pass proves both halves agree: the
            regex admitted the body AND `body.split("||")` plus `strip()` recovered the
            operands from it. Splitting on `" || "` rather than `"||"` would pass the regex and
            then look up one long operand that is in no context, which renders as the empty
            string -- the silent-empty-`REF` outcome, reached through the model rather than
            through the step.
        """
        resolved = _substitute(
            "${{ " + body + " }}",
            {"inputs.branch": "", "inputs.tag": "", "github.ref_name": "main"},
        )

        assert resolved == "main", f"{spelling} resolved to {resolved!r}"


class TestTheTrustedContextAllowlistCatchesAnUntrustedContext:
    """The allowlist guard runs against the live step, which satisfies it -- so it never fired.

    `test_only_trusted_contexts_are_interpolated_into_the_shell` is the security property that
    enabling the `pull_request` trigger made load-bearing: a fork's PR can now start this
    workflow, and the resolve step interpolates `${{ }}` straight into shell. As written it can
    only ever pass, because the one input it reads is the live workflow -- so neither the
    operand extraction nor the allowlist itself has been shown to reject anything.

    Both tests here drive that guard with a substituted `_resolve_script`, i.e. they call the
    assertion rather than re-implement it. Re-stating the allowlist locally would mean a second
    copy to widen, and widening the real one -- adding `github.head_ref` to `trusted` -- is
    precisely the change that must not pass unnoticed.
    """

    _GUARD = TestTheModelsRefuseWhatTheyCannotRepresent

    def test_an_arm_reaching_for_the_fork_branch_name_is_reported(self, monkeypatch):
        """`github.head_ref` is free text chosen by the PR author, and must never be interpolated.

        Args:
            monkeypatch: Replaces the step with one interpolating the fork's branch name.

        Test scenario:
            A branch named `$(curl ...)` or ``;id;`` reaches the step's shell verbatim through
            `${{ github.head_ref }}`, which is command injection into a job holding this repo's
            token. The guard's job is to fail on the context, not on the value, so the branch
            name here is ordinary -- an attacker-shaped one would pass or fail for the wrong
            reason.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: 'REF="${{ github.head_ref }}"\n',
        )

        with pytest.raises(
            AssertionError, match=r"untrusted context\(s\).*github\.head_ref"
        ):
            self._GUARD().test_only_trusted_contexts_are_interpolated_into_the_shell()

    def test_a_step_reading_only_trusted_contexts_still_passes(self, monkeypatch):
        """The guard must accept the four allowlisted contexts, or it is a blanket refusal.

        Args:
            monkeypatch: Replaces the step with one reading only allowlisted contexts.

        Test scenario:
            The other direction of the same `read <= trusted` comparison. An inverted or
            mis-typed comparison (`trusted <= read`, or comparing against the wrong set) still
            passes against the live step, whose four contexts happen to be exactly the
            allowlist -- a subset drawn from it is what separates the two.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: 'REF="${{ inputs.branch || github.ref_name }}"\n',
        )

        self._GUARD().test_only_trusted_contexts_are_interpolated_into_the_shell()


@requires_bash
class TestTheResolveStepFailsLoudlyRatherThanResolvingNothing:
    """An empty ref is the dangerous outcome: checkout would silently take the default branch."""

    def test_a_release_with_no_uploaded_ref_fails_the_step(self, tmp_path):
        """`set -euo pipefail` must turn a missing artifact into a red job, not an empty ref.

        Args:
            tmp_path: Working directory for the step, holding no `.release-ref` directory.

        Test scenario:
            A `workflow_run` event where `.release-ref/release-ref.txt` was never downloaded. An
            empty `REF` would make every build job check out the default branch and publish wheels
            built from the wrong tree.

            Asserts `cat`'s own message, not the harness's assertion text: an earlier version
            matched only `"resolve step failed"`, which any non-zero exit satisfies -- so a
            missing `cat` or the wrong `bash` would have passed this test for the wrong reason
            while the positive release test failed.
        """
        result, written = _run_resolve({"github.event_name": "workflow_run"}, tmp_path)

        assert result.returncode != 0, (
            f"the step should have failed (stdout: {result.stdout})"
        )
        assert "No such file or directory" in result.stderr, result.stderr
        assert not [line for line in written if line != "ref="], (
            f"a failing step must write no usable ref, got {written}"
        )

    def test_an_unrecognised_event_falls_back_to_the_triggering_ref(self, tmp_path):
        """The `*)` arm: a future trigger resolves to its own ref rather than to nothing.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A hypothetical `schedule` event -- not currently a trigger, per
            `test_no_other_event_starts_the_workflow`.

            Deliberately overlaps the `schedule` row of `_EVENT_CASES`, which stands in for `*`.
        """
        resolved = _resolve_ref(
            {"github.event_name": "schedule", "github.ref_name": "main"}, tmp_path
        )

        assert resolved == "main"


@requires_bash
class TestTheResolveHarnessRefusesAnUnusableResult:
    """`_resolve_ref` is the strict wrapper every positive ref assertion here trusts.

    `_run_resolve` deliberately returns the process, so a test can assert on a step that
    fails on purpose. `_resolve_ref` is the other half of that split: it must raise rather
    than hand back a ref the step never produced.
    """

    def test_a_non_zero_exit_is_raised_rather_than_returned(
        self, monkeypatch, tmp_path
    ):
        """A red step must not reach the caller as a ref.

        Args:
            monkeypatch: Replaces the step with one that writes to stderr and exits 3.
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            `test_a_release_with_no_uploaded_ref_fails_the_step` drives the real failure
            through `_run_resolve`, which returns instead of asserting -- so
            `_resolve_ref`'s own exit-code guard was never exercised. The message carries
            the step's stderr, which is what makes a harness failure diagnosable.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: 'echo "boom" >&2\nexit 3\n',
        )

        with pytest.raises(AssertionError, match="resolve step failed: boom"):
            _resolve_ref({}, tmp_path)

    def test_a_step_that_writes_no_ref_line_is_raised(self, monkeypatch, tmp_path):
        """Exit 0 having written nothing is the silent-empty-ref case, and must still raise.

        Args:
            monkeypatch: Replaces the step with one that succeeds and appends no output.
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A rewrite that drops the `echo "ref=${REF}" >> "$GITHUB_OUTPUT"` line, or
            redirects it somewhere else, leaves the step green with no ref -- and checkout
            then silently takes the default branch, the outcome
            `TestTheResolveStepFailsLoudlyRatherThanResolvingNothing` is named for. The
            exit-code guard above cannot catch it, so this is the branch that does.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: 'echo "resolved ref: main"\n',
        )

        with pytest.raises(AssertionError, match="wrote no ref= line"):
            _resolve_ref({}, tmp_path)


class TestEveryArmIsAccountedFor:
    """The arm/table agreement, which reads YAML only and so needs no `bash`."""

    def test_the_tests_drive_every_case_arm(self):
        """Every arm in the step is one `_EVENT_CASES` drives, and vice versa.

        Test scenario:
            The expected set is derived from `_EVENT_CASES`, the same table
            `test_every_modelled_event_resolves_its_ref` iterates -- not a literal written out
            beside it. Adding an arm to the step therefore cannot be satisfied by editing a
            number in this test: the event has to be added to `_EVENT_CASES`, which immediately
            makes the driving test run it and assert its ref.
        """
        driven = {
            "*" if event in _CATCH_ALL_EVENTS else event for event in _EVENT_CASES
        }

        assert _case_arms() == driven


@requires_bash
class TestEveryBranchOfTheResolveStepIsExercised:
    """Branch coverage for shell embedded in YAML, which no coverage tool reports on."""

    @pytest.mark.parametrize("event", sorted(_EVENT_CASES))
    def test_every_modelled_event_resolves_its_ref(self, event, tmp_path):
        """Drive each event in `_EVENT_CASES` through the real step and check the ref it writes.

        Args:
            event: The `github.event_name` key from `_EVENT_CASES`.
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            This is what makes the gate above mean something: every arm the step declares is
            executed here, so an untested arm is impossible rather than merely discouraged. The
            `workflow_run` case gets the `release-ref` artifact the real job downloads.
        """
        context, expected = _EVENT_CASES[event]
        if event == "workflow_run":
            (tmp_path / ".release-ref").mkdir()
            (tmp_path / ".release-ref" / "release-ref.txt").write_text(
                f"{expected}\n", encoding="utf-8"
            )

        assert _resolve_ref(context, tmp_path) == expected


class TestTheWorkflowParsersReportAStructureTheyCannotRead:
    """Each parser reads the live YAML, so a silent miss would void the guard above it.

    These are the failure modes that cannot be provoked from the live workflow, because
    the live workflow is well-formed -- which is exactly why nothing exercised them.
    """

    def test_a_resolve_step_without_the_ref_id_is_reported(self, monkeypatch):
        """`_resolve_script` keys off `id: ref`, not off the step's name or position.

        Args:
            monkeypatch: Replaces `_workflow` with one whose resolve-ref step lost its id.

        Test scenario:
            Every ref test funnels through `_resolve_script`, so a step that is renamed,
            re-ordered or dropped has to raise here rather than return a neighbouring
            step's `run:` -- which would leave this whole file green while testing the
            wrong shell. The step is replaced with one carrying the right *name* and no
            id, the shape a careless edit produces.
        """
        workflow = _workflow()
        workflow["jobs"]["resolve-ref"]["steps"] = [
            {"name": "Resolve the ref to build", "run": "true"}
        ]
        monkeypatch.setattr(sys.modules[__name__], "_workflow", lambda: workflow)

        with pytest.raises(AssertionError, match="no step with id 'ref'"):
            _resolve_script()

    def test_a_resolve_step_without_a_case_block_is_reported(self, monkeypatch):
        """`_case_span` narrows the arm scan; no `case ... in`/`esac` means no scan at all.

        Args:
            monkeypatch: Replaces the step with a straight-line script carrying no `case`.

        Test scenario:
            A rewrite to `if`/`elif` is the plausible way this happens. Falling back to
            the whole step would let `_ARM_HEADER` read body lines as arms, which is the
            precise reason the span exists.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: 'set -euo pipefail\nREF="${{ github.ref_name }}"\n',
        )

        with pytest.raises(AssertionError, match="no `case ... in` / `esac` block"):
            _case_span()

    def test_an_arm_style_the_regex_cannot_read_fails_the_cross_check(
        self, monkeypatch
    ):
        """The `;;` count is what stops a blind parse from agreeing with the expected set.

        Args:
            monkeypatch: Replaces the step with a `case` block whose arms are
                double-quoted.

        Test scenario:
            `_ARM_HEADER`'s charset excludes `"` deliberately, so that a body line such as
            `REF="$(cat .release-ref/release-ref.txt)"` cannot be read as an arm -- which
            also makes a double-quoted arm header invisible to it. Without the
            cross-check `_case_arms` would return an empty set for this block and
            `test_the_tests_drive_every_case_arm` would be comparing nothing against
            nothing.
        """
        script = (
            'case "${E}" in\n  "push") REF=a ;;\n  "pull_request") REF=b ;;\nesac\n'
        )
        monkeypatch.setattr(sys.modules[__name__], "_resolve_script", lambda: script)

        with pytest.raises(AssertionError, match="parsed 0 case arms but found 2"):
            _case_arms()


class TestTheArmParserReadsTheStylesItClaimsToAccept:
    """`_case_arms` documents tolerance for three arm styles the live step does not use.

    Nothing in the live workflow reaches the alternation split or the quote stripping, so
    both are dead code against it -- and a tolerance no test exercises is one that breaks
    the first time an arm is restyled, which is the regression the helper was widened to
    prevent.

    The quoted style below means SINGLE quotes. A double-quoted header is outside
    `_ARM_HEADER`'s charset, which excludes `"` so that the step's own
    `REF="$(cat .release-ref/release-ref.txt)"` body line cannot be read as an arm; that case is
    covered one class up, by `test_an_arm_style_the_regex_cannot_read_fails_the_cross_check`,
    because it belongs with the failures rather than the tolerances.
    """

    @pytest.mark.parametrize(
        "style, span, expected",
        [
            (
                "a pattern on its own line with its body beneath",
                "  pull_request)\n    REF=a\n    ;;\n  *)\n    REF=b\n    ;;",
                {"*", "pull_request"},
            ),
            (
                "an alternation",
                "  push|pull_request) REF=a ;;\n  *) REF=b ;;",
                {"*", "push", "pull_request"},
            ),
            (
                "a quoted pattern",
                "  'pull_request') REF=a ;;\n  *) REF=b ;;",
                {"*", "pull_request"},
            ),
        ],
    )
    def test_it_reads_an_arm_written_in_each_tolerated_style(
        self, style, span, expected, monkeypatch
    ):
        """Parse a synthetic `case` block in one tolerated style and check the arm set.

        Args:
            style: How the arm is written, for the failure message.
            span: The synthetic `case` body, wrapped into a full block below.
            expected: The patterns `_case_arms` must report for it.
            monkeypatch: Replaces the live step with that synthetic block.

        Test scenario:
            Each style also satisfies the `;;` cross-check, so a pass here means the
            regex read the arms rather than merely counting terminators. The `*` arm is
            present in all three because the live step has one, and dropping it would
            change what the cross-check compares.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_resolve_script",
            lambda: f'case "${{E}}" in\n{span}\nesac\n',
        )

        assert _case_arms() == expected, (
            f"{style} was misread as {sorted(_case_arms())}"
        )


class TestTheArmTerminatorCountsOnlyEndOfLineSemicolons:
    """`_ARM_TERMINATOR` replaced a raw `span.count(";;")`, and the cross-check depends on it.

    That count is the only thing stopping `_case_arms` from returning a set it merely agrees
    with: `test_the_tests_drive_every_case_arm` compares the parsed arms against `_EVENT_CASES`,
    so a parse that silently reads too few arms has to be caught by something else. Counting the
    raw substring made the count wrong in one direction and the regex makes it wrong in the
    other, so both need a test -- the live step has four plain arms and exercises neither.
    """

    def test_a_body_string_carrying_two_semicolons_does_not_inflate_the_count(
        self, monkeypatch
    ):
        """A `;;` inside an arm's BODY is not an arm terminator, and must not be counted.

        Args:
            monkeypatch: Replaces the step with a `case` block whose first arm's body contains
                `;;` inside a double-quoted string.

        Test scenario:
            `REF="${A};;${B}"` is the shape that breaks a substring count: two arms, three
            occurrences of `;;`, so `span.count(";;")` reports 3 against 2 headers and
            `_case_arms` raises on a block it parsed perfectly well. The failure would read as
            "the arm regex no longer matches the step" while pointing at nothing wrong, and the
            natural fix -- loosening the cross-check -- is what would then let a genuinely
            mis-parsed block through.
        """
        script = (
            'case "${E}" in\n  workflow_run) REF="${A};;${B}" ;;\n  *) REF=b ;;\nesac\n'
        )
        monkeypatch.setattr(sys.modules[__name__], "_resolve_script", lambda: script)

        assert _case_arms() == {"*", "workflow_run"}, (
            "a `;;` inside a body string was counted as an arm terminator"
        )

    def test_a_fall_through_terminator_deflates_the_count_and_is_reported(
        self, monkeypatch
    ):
        """`;&` is a legal terminator this does not count, so the mismatch must raise.

        Args:
            monkeypatch: Replaces the step with a `case` block whose first arm ends in `;&`.

        Test scenario:
            The headers-exceed-terminators direction of the cross-check, which nothing reached:
            `test_an_arm_style_the_regex_cannot_read_fails_the_cross_check` drives the opposite
            case (0 headers against 2 terminators). `;&` falls through to the next arm, so an
            arm written that way changes which ref is produced -- raising is the documented
            behaviour, and the message names the terminator count so the cause is readable.
        """
        script = 'case "${E}" in\n  push) REF=a ;&\n  *) REF=b ;;\nesac\n'
        monkeypatch.setattr(sys.modules[__name__], "_resolve_script", lambda: script)

        with pytest.raises(AssertionError, match="parsed 2 case arms but found 1"):
            _case_arms()


class TestTheHelperExamplesStayExecutable:
    """`lint.yml`'s `static` job runs doctests over `src` only, never over `tests`.

    The `>>>` examples on the helpers above would therefore rot unnoticed -- the exact failure the
    repo added that job to prevent. Running them here puts them under the suite CI does run.
    """

    def test_every_docstring_example_in_this_module_passes(self):
        """Execute the module's own doctests and fail on the first rotted example.

        Test scenario:
            Collects every `>>>` example in this module -- the ones on the module-level helpers,
            deliberately not on the test classes -- and requires both that some were found, so
            the guard cannot pass by collecting nothing, and that all of them pass. The helper
            list is left unenumerated on purpose: naming it here is the kind of detail that rots
            the moment an example is added, which is the failure this test exists to catch.
        """
        failures, attempted = doctest.testmod(
            sys.modules[__name__], verbose=False, report=False
        )

        assert attempted > 0, "no doctest examples were collected from this module"
        assert failures == 0, f"{failures} of {attempted} doctest examples failed"


def _jobs_reading_resolve_ref() -> set[str]:
    """Return every job whose definition mentions `needs.resolve-ref` anywhere.

    Found by searching each job's own dumped YAML rather than from a hand-written list, so a job
    added later is covered without this file being touched. It is a substring search, so a mention
    in a `name:`, an `env:` value or an `echo` inside a `run:` would count too -- which
    over-requires a `needs` declaration rather than missing one, the harmless direction. The
    substring survives `safe_dump`'s folding: the only fold points inside
    `${{ needs.resolve-ref.outputs.ref }}` are the spaces just after `{{` and before `}}`.

    Returns:
        set[str]: Job ids containing a `needs.resolve-ref...` expression.

    Examples:
        - Twelve of the workflow's fourteen jobs read the resolved ref:
            ```python
            >>> len(_jobs_reading_resolve_ref())
            12
            >>> "report" in _jobs_reading_resolve_ref()
            True

            ```
        - The two that do not are `resolve-ref` itself and `release`, which gates on the build
          and verify jobs instead:
            ```python
            >>> sorted({"resolve-ref", "release"} & _jobs_reading_resolve_ref())
            []

            ```

    See Also:
        _declared_needs: The other half of the check -- what a job declares, not what it reads.
    """
    return {
        job_id
        for job_id, job in _workflow()["jobs"].items()
        if "needs.resolve-ref" in yaml.safe_dump(job)
    }


def _declared_needs(job_id: str) -> list[str]:
    """Return a job's `needs` as a list, whether written as a string or a sequence.

    Args:
        job_id: The job's id in the workflow.

    Returns:
        list[str]: The job ids it declares a dependency on.

    Raises:
        KeyError: If `job_id` is not a job in the workflow.

    Examples:
        - A bare-string `needs` becomes a one-item list, so membership cannot match a
          substring of a differently-named job:
            ```python
            >>> _declared_needs("build-linux-wheels")
            ['resolve-ref']

            ```
        - A job declaring none reads as empty rather than raising:
            ```python
            >>> _declared_needs("resolve-ref")
            []

            ```
        - A list is returned in the order written, `resolve-ref` first here:
            ```python
            >>> _declared_needs("verify-debian12")
            ['resolve-ref', 'build-linux-wheels']

            ```
    """
    needs = _workflow()["jobs"][job_id].get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


class TestEveryJobReadingTheResolvedRefDeclaresIt:
    """An undeclared `needs` renders the expression EMPTY rather than failing.

    `actions/checkout` then falls back to `github.ref`. That is the same ref on a push or a pull
    request, which is why this went unnoticed -- but on the release path `github.ref` is the
    default branch while the wheel is built from the `release/auto-*` branch carrying the version
    bump, so `verify.py` would have run from a different tree than the wheel it was verifying.
    `report`'s `needs.resolve-ref.result != 'skipped'` was likewise reading null, making the guard
    a no-op.

    Latent rather than historical: this workflow has never run on a `workflow_run` event -- every
    run so far is a push, a pull request or a dispatch -- and 0.1.0/0.1.1/0.2.0 all predate it, so
    no published wheel was verified against the wrong tree. The first release through this arm
    would have been.
    """

    def test_every_such_job_is_wired(self):
        """The invariant for all twelve jobs, derived from the workflow rather than listed here.

        Test scenario:
            Collects every job whose definition mentions `needs.resolve-ref` -- the four
            artifact-building jobs (three wheel builds plus `sdist`), the five verify jobs,
            `test-native-wheel`, `verify-sdist-pure-backend` and `report` -- and requires each
            to declare it. A job added later is covered automatically.
        """
        missing = {
            job_id
            for job_id in _jobs_reading_resolve_ref()
            if "resolve-ref" not in _declared_needs(job_id)
        }

        assert missing == set(), (
            f"these jobs read needs.resolve-ref without declaring it: {missing}"
        )

    def test_the_set_of_such_jobs_is_the_expected_twelve(self):
        """Guards the guard: an empty collection would make the test above vacuous.

        Test scenario:
            Twelve of the workflow's fourteen jobs, i.e. every job except `resolve-ref` itself and
            `release`. Listing them is the point: `test_every_such_job_is_wired` passes trivially
            on an empty set, so this is what proves the search found the jobs rather than nothing.
        """
        assert _jobs_reading_resolve_ref() == {
            "build-linux-wheels",
            "build-macos-wheels",
            "build-windows-wheels",
            "sdist",
            "verify-debian12",
            "verify-rocky9",
            "verify-rocky9-aarch64",
            "verify-macos",
            "verify-windows",
            "test-native-wheel",
            "verify-sdist-pure-backend",
            "report",
        }

    def test_reports_skip_guard_can_now_fire(self):
        """`report`'s `if` reads `needs.resolve-ref.result`, which requires the declaration.

        Test scenario:
            `report` runs under `always()`, so its `!= 'skipped'` test is the only thing stopping
            it from reporting on a run whose ref was never resolved. An undeclared need renders
            null, and `null != 'skipped'` is true -- the guard would read as a condition while
            always passing. Checks both halves: the `if` still reads the result, and the job still
            declares the need that makes reading it meaningful.
        """
        report = _workflow()["jobs"]["report"]

        assert "needs.resolve-ref.result" in report["if"]
        assert "resolve-ref" in _declared_needs("report")


class TestDeclaredNeedsReadsEveryShapeTheWorkflowUses:
    """`needs` is spelled three ways in this workflow but yields only two Python shapes.

    `build-linux-wheels` writes a bare string; `verify-debian12` an inline flow sequence and
    `report` the same sequence broken across lines, which YAML parses into an identical `list`. So
    `_declared_needs` has two shapes to normalise, the bare string being the one that matters --
    the third case below exercises no new code and is kept only to pin the spelling. The root job,
    `resolve-ref`, omits `needs` entirely, which is the remaining branch.

    `test_every_such_job_is_wired` membership-tests the result, so a shape read wrongly
    does not raise -- it reports a wired job as unwired, or an unwired one as fine.
    """

    def test_a_job_with_no_needs_reads_as_an_empty_list(self):
        """`resolve-ref` is the root job, and must not make the helper raise.

        Test scenario:
            The `.get("needs", [])` default is reachable only from a job declaring none.
            Every job `_jobs_reading_resolve_ref` yields has a `needs`, so the live suite
            never took this branch; a job added later that reads the ref through a
            different expression would be the first to.
        """
        assert _declared_needs("resolve-ref") == [], (
            "the root job declares no needs, which must read as an empty list"
        )

    @pytest.mark.parametrize(
        "job_id, shape",
        [
            ("build-linux-wheels", "a bare string"),
            ("verify-debian12", "an inline list"),
            ("report", "a list on the following line"),
        ],
    )
    def test_it_normalises_the_shapes_the_live_workflow_uses(self, job_id, shape):
        """Each written form must yield a list that contains `resolve-ref`.

        Args:
            job_id: A job declaring its `needs` in one of the live YAML forms.
            shape: How that job writes it, for the failure message.

        Test scenario:
            `needs: resolve-ref`, `needs: [resolve-ref, ...]` and the same list wrapped
            onto the next line all appear in this workflow. The bare-string form is the
            one that matters: `"resolve-ref" in "resolve-ref-check"` is true for a
            substring, so a job needing only a similarly-named job would read as wired if
            the string were membership-tested instead of wrapped in a list.
        """
        needs = _declared_needs(job_id)

        assert isinstance(needs, list), (
            f"{shape} did not normalise to a list: {needs!r}"
        )
        assert "resolve-ref" in needs, (
            f"{job_id} writes its needs as {shape} and must still list resolve-ref"
        )


def _request_for(marker: object) -> SimpleNamespace:
    """Build the minimal `request` stand-in `_bash_must_be_present` reads.

    The fixture touches exactly one thing on the request it is handed,
    `request.node.get_closest_marker("bash")`, so a two-attribute namespace is a complete stand
    in. Faking it is what makes the fixture's failure branch reachable at all: the real request
    carries the markers of the test being set up, and a test cannot be both the subject and the
    driver of its own gate.

    Args:
        marker: What `get_closest_marker("bash")` should return -- any truthy object to stand
            for the marker being present, or None for a test that does not carry it. The
            fixture only tests truthiness, never the object's contents.

    Returns:
        SimpleNamespace: A `request`-shaped object exposing `node.get_closest_marker`.

    Examples:
        - The `bash` marker is reported as present:
            ```python
            >>> _request_for("the mark").node.get_closest_marker("bash")
            'the mark'

            ```
        - Every other marker is absent, so the gate cannot fire on an unrelated one:
            ```python
            >>> _request_for("the mark").node.get_closest_marker("live") is None
            True

            ```
    """
    return SimpleNamespace(
        node=SimpleNamespace(
            get_closest_marker=lambda name: marker if name == "bash" else None
        )
    )


class TestTheBashGateFailsRatherThanSkipping:
    """`_bash_must_be_present` is the whole reason this module has no `skipif`.

    The repo's `CLAUDE.md` forbids folding a missing dependency into a skip count, and here that
    rule has teeth: the 16 `@requires_bash` tests are the only ones that run the workflow's own
    shell, so a machine without `bash` would otherwise report "16 skipped" beside a green line
    while nothing about the resolve step had been checked.

    The fixture's failing branch cannot be reached by running a test -- an autouse fixture is
    set up before the test body, so monkeypatching `_BASH` from inside one is already too late.
    It is called through `__wrapped__`, the original function pytest keeps on the fixture object,
    because pytest refuses a direct call on the fixture itself.

    Both outcome types are then caught by hand rather than with
    `pytest.raises(pytest.fail.Exception)`, because `Failed` and `Skipped` are sibling subclasses
    of `OutcomeException` and neither catches the other. That is not a precaution: the first
    version of the test below used `pytest.raises`, a `pytest.fail` -> `pytest.skip` mutation of
    the gate escaped it, and pytest reported the guard's own test as skipped -- the silent skip
    produced by the very test written to forbid it.
    """

    def test_a_bash_marked_test_fails_when_no_bash_exists(self, monkeypatch):
        """The gate must raise `Failed`, not `Skipped`, and must say how to opt out.

        Args:
            monkeypatch: Sets the module's `_BASH` to None, i.e. simulates a machine where
                `find_bash()` found nothing usable.

        Test scenario:
            The one combination that must stop the run: a `bash`-marked test on a machine with
            no usable `bash`. Asserts the outcome type as well as the message, because
            `pytest.skip` is a one-word edit away and would turn this gate into exactly the
            silent skip it exists to replace -- and the message is checked for `-m 'not bash'`
            because an environment that genuinely has no `bash` needs to be told how to
            deselect deliberately rather than left to guess.

            The outcome is caught by hand rather than with `pytest.raises(pytest.fail.Exception)`
            for that same reason: `Skipped` is not a subclass of `Failed` -- both descend from
            `OutcomeException` -- so a skip raised here escapes `pytest.raises` and pytest
            reports THIS test as skipped. That is what happened to the first version of this
            test: the mutation was reported as a skip by the test written to catch it, which is
            as silent a pass as the one being guarded against.
        """
        monkeypatch.setattr(sys.modules[__name__], "_BASH", None)

        outcome: BaseException | None = None
        try:
            _bash_must_be_present.__wrapped__(_request_for(pytest.mark.bash))
        except (pytest.fail.Exception, pytest.skip.Exception) as raised:
            outcome = raised

        assert isinstance(outcome, pytest.fail.Exception), (
            f"the gate must fail the run, never skip and never pass; it raised {outcome!r}"
        )
        assert not isinstance(outcome, pytest.skip.Exception), (
            "the gate must fail the run, never skip -- a skip is what it replaced"
        )

        message = str(outcome)

        assert "no usable bash found" in message, message
        assert "-m 'not bash'" in message, (
            f"the gate must name the deselection it expects, got: {message}"
        )

    @pytest.mark.parametrize(
        "marker, bash, case",
        [
            (None, None, "an unmarked test on a machine with no bash"),
            (None, "/usr/bin/bash", "an unmarked test where bash exists"),
            (pytest.mark.bash, "/usr/bin/bash", "a bash-marked test where bash exists"),
        ],
    )
    def test_it_lets_every_other_combination_through(
        self, marker, bash, case, monkeypatch
    ):
        """The gate is autouse, so it runs for every test in the module and must not over-fire.

        Args:
            marker: What the simulated test carries, the `bash` marker or nothing.
            bash: The simulated `find_bash()` result.
            case: The combination in words, for the failure message.
            monkeypatch: Sets the module's `_BASH` to the simulated result.

        Test scenario:
            The other three cells of the truth table. The first is the one that matters: the
            YAML-only tests here -- the trigger, the `paths` filter, the `needs` wiring -- must
            still run on a machine without `bash`, so dropping the marker test from the
            condition would fail the whole module rather than the 16 tests that need a shell.
        """
        monkeypatch.setattr(sys.modules[__name__], "_BASH", bash)

        assert _bash_must_be_present.__wrapped__(_request_for(marker)) is None, (
            f"the gate must pass {case} through"
        )
