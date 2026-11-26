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

import re
import shutil
import subprocess
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
    GitHub does for a context property the current event does not carry.

    Args:
        script: Shell carrying `${{ }}` expressions.
        context: Expression path (e.g. `github.ref_name`) to its value for the simulated event.

    Returns:
        str: The script with every expression replaced.
    """

    def _value(match: re.Match[str]) -> str:
        for operand in (part.strip() for part in match.group(1).split("||")):
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
    """
    return ref.upper().startswith("REFS/")


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
            "ci/parquet-plugin/**",
            "setup.py",
            ".github/workflows/bundle-pypi-wheels.yml",
        ]

    def test_pushes_to_main_still_build(self):
        """The arm is additive: a merge must keep building and verifying, as before."""
        assert _triggers()["push"]["branches"] == ["main"]


@requires_bash
class TestTheResolvedRefIsCheckoutable:
    """`resolve-ref`'s output feeds `actions/checkout`'s `ref:` in every build job."""

    def test_a_pull_request_resolves_to_a_fully_qualified_merge_ref(self, tmp_path):
        """The regression this guards: `github.ref_name` alone is `54/merge`, which checkout rejects.

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
        """Unqualified is correct here: `origin/main` exists, so checkout resolves it."""
        resolved = _resolve_ref(
            {"github.event_name": "push", "github.ref_name": "main"},
            tmp_path,
        )

        assert resolved == "main"

    def test_a_dispatch_prefers_the_requested_branch(self, tmp_path):
        """`inputs.branch || github.ref_name` -- the manual pre-merge build path."""
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
        """Blank input means "the ref chosen in the UI"."""
        resolved = _resolve_ref(
            {"github.event_name": "workflow_dispatch", "github.ref_name": "main"},
            tmp_path,
        )

        assert resolved == "main"

    def test_a_release_still_resolves_to_the_uploaded_release_ref(self, tmp_path):
        """The bump lives on the release branch, not on `head_branch`; that artifact is the only source."""
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
