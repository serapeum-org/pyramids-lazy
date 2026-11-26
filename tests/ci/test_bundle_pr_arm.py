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
reimplemented here: a reimplementation only proves it agrees with itself. The `${{ }}` expressions are
substituted from a simulated event context, which is the one part this test has to model.
"""

from __future__ import annotations

import doctest
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.core

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bundle-pypi-wheels.yml"

_BASH = shutil.which("bash")

requires_bash = pytest.mark.skipif(
    _BASH is None, reason="bash unavailable on this machine"
)

# `on:` is a YAML 1.1 boolean, so PyYAML keys the trigger block under True rather than "on".
_ON_KEY = True

_EXPRESSION = re.compile(r"\$\{\{(.+?)\}\}")

# The only expression grammar `_substitute` models: a dotted context path, or several joined by
# `||`. Anything richer (a `&&` conditional, a comparison, `format(...)`) must fail loudly.
_MODELLED_EXPRESSION = re.compile(r"[\w.]+(?:\s*\|\|\s*[\w.]+)*")

# A `case` arm header: one or more glob/word patterns, optionally quoted, joined by `|`, then
# `)`. The charset deliberately excludes `=`, `"` and `$`, so a body line such as
# `REF="$(cat .release-ref/release-ref.txt)"` cannot be mistaken for an arm.
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
    """
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    """Return the workflow's `on:` block.

    Returns:
        dict: Event name to its configuration (`{}` for a bare event).
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
    GitHub does for a context property the current event does not carry. Falsiness here is
    emptiness only -- GitHub's documented falsy list is `false, 0, -0, "", '', null`, so for the
    string-valued contexts this step reads, `""` is the only falsy value either side treats as
    such.

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


def _resolve_ref(context: dict[str, str], cwd: Path) -> str:
    """Run the real resolve step for a simulated event and return the ref it writes.

    Args:
        context: The expression context for the event (see :func:`_substitute`).
        cwd: Directory to run in; the `workflow_run` path reads `.release-ref/release-ref.txt`
            relative to it.

    Returns:
        str: The value the step appends to `GITHUB_OUTPUT` as `ref=`.

    Raises:
        AssertionError: If the step fails, or writes no `ref=` line.
    """
    assert _BASH is not None
    output = cwd / "github_output.txt"
    output.touch()
    result = subprocess.run(
        [_BASH, "-c", _substitute(_resolve_script(), context)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={"GITHUB_OUTPUT": output.as_posix(), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, f"resolve step failed: {result.stderr}"
    written = [
        line
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("ref=")
    ]
    assert written, f"the step wrote no ref= line (stdout: {result.stdout})"
    return written[-1].removeprefix("ref=")


def _checkout_can_resolve(ref: str) -> bool:
    """Whether `actions/checkout` can resolve `ref` without a same-named branch or tag.

    Mirrors `getCheckoutInfo` in checkout's `src/ref-helper.ts`: a ref starting with `refs/` is
    taken as fully qualified, anything else has to exist as `origin/<ref>` or as a tag.

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
    """
    return ref.upper().startswith("REFS/")


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
    its body beneath, an alternation (`a|b)`), and a quoted pattern -- because the earlier
    one-line-only regex returned the same four names for all of those and let a new arm pass
    unnoticed. The arm count is cross-checked against the `;;` terminators so a parse that reads
    too few arms fails instead of agreeing with the expected set.

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
    assert len(headers) == span.count(";;"), (
        f"parsed {len(headers)} case arms but found {span.count(';;')} `;;` terminators; "
        "the arm regex no longer matches the step's style"
    )
    return {
        pattern.strip().strip("'\"")
        for header in headers
        for pattern in header.split("|")
    }


def _paths_filter_selects(path: str) -> bool:
    """Whether a PR changing `path` alone would start the workflow.

    Models the `on.pull_request.paths` filter: a `<dir>/**` pattern matches anything beneath that
    directory, any other pattern is a literal path. Those are the only two forms the live filter
    uses, so this stays a model of what is written rather than a reimplementation of GitHub's
    matcher -- and it now RAISES on any pattern outside those two forms instead of falling through
    to literal equality.

    That guard is the point. A `!pattern` negation removes paths an earlier pattern matched, and
    the old fall-through treated it as a literal that matched nothing -- so a filter of
    `["ci/parquet-plugin/**", "!ci/parquet-plugin/verify.py"]` was modelled as still building
    `verify.py` while GitHub would not have run the workflow at all. Negation is the one direction
    in which a bad filter stayed green here; every other divergence already failed loudly.

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
        assert not pattern.startswith(("!", "+")), (
            f"unmodelled paths pattern {pattern!r}: negation changes what earlier patterns "
            "selected, which this model does not represent. Extend it before using it to "
            "justify the filter."
        )
        if pattern.endswith("/**"):
            assert "*" not in pattern[: -len("/**")], (
                f"unmodelled paths pattern {pattern!r}: only a trailing `/**` is modelled"
            )
            if path.startswith(pattern[: -len("**")]):
                selected = True
        else:
            assert not set("*?[") & set(pattern), (
                f"unmodelled paths pattern {pattern!r}: a glob that is not a trailing `/**` is "
                "not modelled"
            )
            if path == pattern:
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

    def test_a_push_still_resolves_to_the_branch_name(self, tmp_path):
        """Unqualified is correct here: `origin/main` exists, so checkout resolves it.

        Args:
            tmp_path: Working directory for the step, from pytest.
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
        """`pull_request` satisfies neither branch of the `if`, so nothing is uploaded from a PR."""
        condition = " ".join(_workflow()["jobs"]["release"]["if"].split())

        assert "github.event_name == 'workflow_run'" in condition
        assert (
            "github.event_name == 'workflow_dispatch' && inputs.dry_run_release"
            in condition
        )
        assert "pull_request" not in condition

    def test_the_release_job_needs_every_build_and_verify_job(self):
        """A red verify must stop a publish, not merely colour the run."""
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
        }


class TestSupersededPullRequestRunsAreCancelled:
    """95 job-minutes a run makes the hosted-runner pool the scarce resource."""

    def test_only_pull_requests_share_a_cancellable_group(self):
        """A push or release gets the unique `run_id` group, so a merge is always built to completion."""
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
            the driver in the wheel) and the workflow itself.
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
            Library code, this test, docs, and a sibling workflow. None of these reaches the
            native build: `src/` is pure Python the wheel carries either way, and `tests.yml` is a
            different pipeline. `pyproject.toml` is deliberately NOT in this list -- it configures
            cibuildwheel and the packaging, so it belongs with the positive cases above.
        """
        assert not _paths_filter_selects(path), f"{path} cannot change the wheel"


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

    def test_a_function_call_expression_is_rejected(self):
        """`format(...)` is a plausible rewrite of the new arm, and was modelled as empty."""
        with pytest.raises(AssertionError, match="unmodelled GitHub expression"):
            _substitute(
                "${{ format('refs/pull/{0}/merge', github.event.pull_request.number) }}",
                {"github.event.pull_request.number": "54"},
            )

    def test_the_live_step_only_uses_modelled_expressions(self):
        """The guard above is only useful if the step itself stays inside the modelled grammar."""
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

    def test_a_non_trailing_glob_is_rejected(self, monkeypatch):
        """`ci/*/build.sh` is a legal filter and not something this model represents.

        Args:
            monkeypatch: Replaces the live filter with one carrying an interior glob.
        """
        monkeypatch.setattr(
            sys.modules[__name__],
            "_triggers",
            lambda: {"pull_request": {"paths": ["ci/*/build.sh"]}},
        )

        with pytest.raises(AssertionError, match="unmodelled paths pattern"):
            _paths_filter_selects("ci/parquet-plugin/build.sh")


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
        """
        with pytest.raises(AssertionError, match="resolve step failed"):
            _resolve_ref({"github.event_name": "workflow_run"}, tmp_path)

    def test_an_unrecognised_event_falls_back_to_the_triggering_ref(self, tmp_path):
        """The `*)` arm: a future trigger resolves to its own ref rather than to nothing.

        Args:
            tmp_path: Working directory for the step, from pytest.

        Test scenario:
            A hypothetical `schedule` event -- not currently a trigger, per
            `test_no_other_event_starts_the_workflow`.
        """
        resolved = _resolve_ref(
            {"github.event_name": "schedule", "github.ref_name": "main"}, tmp_path
        )

        assert resolved == "main"


@requires_bash
class TestEveryBranchOfTheResolveStepIsExercised:
    """Branch coverage for shell embedded in YAML, which no coverage tool reports on."""

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

    @pytest.mark.parametrize("number", ["1", "54", "1234"])
    def test_the_pull_request_arm_qualifies_any_pr_number(self, number, tmp_path):
        """The number is interpolated, so a multi-digit PR must qualify the same way.

        Args:
            number: The pull request number for the simulated event.
            tmp_path: Working directory for the step.
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


class TestTheHelperExamplesStayExecutable:
    """`lint.yml`'s `static` job runs doctests over `src` only, never over `tests`.

    The `>>>` examples on the helpers above would therefore rot unnoticed -- the exact failure the
    repo added that job to prevent. Running them here puts them under the suite CI does run.
    """

    def test_every_docstring_example_in_this_module_passes(self):
        """Execute the module's own doctests and fail on the first rotted example.

        Test scenario:
            Collects every `>>>` example in this module (the examples on `_substitute`,
            `_checkout_can_resolve` and `_paths_filter_selects`) and requires both that some were
            found -- so the guard cannot pass by collecting nothing -- and that all of them pass.
        """
        failures, attempted = doctest.testmod(
            sys.modules[__name__], verbose=False, report=False
        )

        assert attempted > 0, "no doctest examples were collected from this module"
        assert failures == 0, f"{failures} of {attempted} doctest examples failed"


class TestTheBuildJobsWireTheResolvedRef:
    """A job checking out `needs.resolve-ref.outputs.ref` must declare the dependency.

    An undeclared `needs` renders the expression empty rather than failing, and checkout then
    falls back to `github.ref` -- right on a push or a PR, wrong on the release path, where the
    version bump lives on the release branch rather than on `head_branch`.
    """

    @pytest.mark.parametrize(
        "job",
        ["build-linux-wheels", "build-macos-wheels", "build-windows-wheels", "sdist"],
    )
    def test_each_build_job_depends_on_resolve_ref(self, job):
        """Pin the wiring for every job that builds a published artifact.

        Args:
            job: A job that builds an artifact from the resolved ref.
        """
        needs = _workflow()["jobs"][job]["needs"]
        declared = [needs] if isinstance(needs, str) else needs

        assert "resolve-ref" in declared
