"""Guard the strict-docs gate in `.github/workflows/github-pages-mkdocs.yml`.

The deploy action builds the site **non-strict**, so a broken `nav:` entry, a dangling
`::: identifier` or a malformed docstring publishes silently rather than failing anything. A
warnings-as-errors `mkdocs build --strict` is the only thing that catches those, and before this
gate existed nothing in CI ran it: `github-pages-mkdocs.yml` fired only on push-to-main, on
`release: published` and via `workflow_run`, and no pull-request workflow built the site at all.

This is a **regression guard**, not new ground. The same check lived in `lint.yml` as a `docs` job
from `4b70d7e` (PR #14, which closed issue #17 -- "nothing validates the docs site before merge")
until `8e383b6` (PR #43) rewrote `lint.yml` wholesale and the job did not survive the rewrite. At
`8e383b6^` that file's jobs were `pre-commit`, `static`, `docs`, `gitleaks`; afterwards the `docs`
job was simply gone, and nobody noticed for the same reason it was needed: nothing fails when a
docs gate disappears. Issue #46 then asked for core's shape instead, which also gates the deploys.

So the tests here pin the parts that would make the gate silently stop working:

- the `pull_request` trigger, without which the gate never runs pre-merge;
- the `build-strict` job and that it really runs `--strict` in the `docs` environment -- the same
  environment the deploy jobs install, so the gate cannot pass against a different dependency set
  than the one that publishes;
- `needs: build-strict` on both deploy jobs, which is what turns the gate from a PR-only check into
  one that also blocks publishing a site that does not build;
- that adding the `pull_request` trigger did **not** make either deploy job reachable from a pull
  request. The workflow grants `contents: write` + `pages: write` at the top level and the deploys
  hold `ACTIONS_DEPLOY_TOKEN`, so a deploy job that a PR could satisfy would be a serious hole;
  `build-strict` therefore also narrows its own permissions to `contents: read`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.core

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "github-pages-mkdocs.yml"

# `on:` is a YAML 1.1 boolean, so PyYAML keys the trigger block under True rather than "on".
_ON_KEY = True

#: The job ids that publish, and so must be gated and unreachable from a pull request.
_DEPLOY_JOBS = ("deploy-main", "deploy-release")


def _workflow() -> dict:
    """Return the parsed docs workflow.

    Returns:
        dict: The whole workflow document.
    """
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(job_id: str) -> dict:
    """Return one job's definition.

    Args:
        job_id: The job's key under `jobs:`.

    Returns:
        dict: That job's mapping.

    Raises:
        AssertionError: If the job is absent, which every test here would otherwise
            misreport as a missing field.
    """
    jobs = _workflow()["jobs"]
    assert job_id in jobs, f"no `{job_id}` job; jobs are {sorted(jobs)}"
    return jobs[job_id]


def _declared_needs(job_id: str) -> list[str]:
    """Return a job's `needs` as a list, whether written as a string or a sequence.

    Args:
        job_id: The job's key under `jobs:`.

    Returns:
        list[str]: The job ids it declares a dependency on.
    """
    needs = _job(job_id).get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _run_steps(job_id: str) -> list[str]:
    """Return every `run:` script in a job, whitespace-normalised.

    Args:
        job_id: The job's key under `jobs:`.

    Returns:
        list[str]: One entry per step that carries a `run:`.
    """
    return [
        " ".join(step["run"].split())
        for step in _job(job_id).get("steps", [])
        if "run" in step
    ]


def _step_using(job_id: str, action: str) -> dict:
    """Return the first step in a job whose `uses:` names `action`.

    Args:
        job_id: The job's key under `jobs:`.
        action: A substring of the action reference, e.g. `python-setup/pixi`.

    Returns:
        dict: That step's mapping.

    Raises:
        AssertionError: If no step uses it.
    """
    for step in _job(job_id).get("steps", []):
        if action in step.get("uses", ""):
            return step
    raise AssertionError(f"`{job_id}` has no step using {action!r}")


class TestTheGateRunsBeforeAMerge:
    """Without the `pull_request` trigger the gate only ever runs after the fact."""

    def test_the_workflow_runs_on_a_pull_request(self):
        """A docs break must fail the PR, not the deploy that follows it."""
        assert "pull_request" in _workflow()[_ON_KEY], (
            "no pull_request trigger: the strict build would only run post-merge again"
        )

    def test_it_runs_for_a_pull_request_into_any_branch(self):
        """Mirrors core: a docs break is worth catching on a stacked PR too."""
        assert _workflow()[_ON_KEY]["pull_request"]["branches"] == ["**"]

    def test_the_existing_triggers_are_kept(self):
        """The gate is additive -- push-to-main, release and the release workflow_run all stay."""
        triggers = _workflow()[_ON_KEY]

        assert triggers["push"]["branches"] == ["main"]
        assert triggers["release"]["types"] == ["published"]
        assert triggers["workflow_run"]["workflows"] == ["github-release"]


class TestTheStrictBuildIsReal:
    """A `build-strict` job that does not actually build strictly is worse than none."""

    def test_the_job_exists(self):
        """Named `build-strict`, as in pyramids-core, so the two repos stay diffable."""
        assert "build-strict" in _workflow()["jobs"]

    def test_it_builds_with_strict(self):
        """`--strict` is what turns an mkdocs/griffe warning into a failure."""
        runs = _run_steps("build-strict")

        assert any("mkdocs build --strict" in run for run in runs), runs

    def test_it_builds_in_the_docs_environment(self):
        """The gate and the deploy must install the same env or they drift apart.

        Test scenario:
            The deploy jobs pass `install-groups: 'docs'` to the mkdocs-deploy action, so a gate
            built from any other environment could pass against a different dependency set than
            the one that publishes.
        """
        runs = _run_steps("build-strict")
        setup = _step_using("build-strict", "python-setup/pixi")

        assert any("-e docs" in run for run in runs), runs
        assert setup["with"]["environments"] == "docs"
        assert setup["with"]["activate-environment"] == "docs"

    def test_it_does_not_write_into_the_repository(self):
        """`--site-dir` keeps the build out of the tree, so nothing can be committed by accident."""
        runs = _run_steps("build-strict")

        assert any("--site-dir" in run for run in runs), runs

    def test_it_only_reads_the_repository(self):
        """The workflow grants contents+pages write at the top level; the gate needs neither.

        Test scenario:
            A pull request can now trigger this workflow, so the one job a PR reaches must not
            inherit the write scopes the deploy jobs need for `ACTIONS_DEPLOY_TOKEN`.
        """
        assert _job("build-strict")["permissions"] == {"contents": "read"}


class TestEveryDeployIsGatedOnIt:
    """The difference between a PR check and a gate: the deploys have to wait for it."""

    @pytest.mark.parametrize("job_id", _DEPLOY_JOBS)
    def test_the_deploy_waits_for_the_strict_build(self, job_id):
        """A site that fails `--strict` must not reach gh-pages.

        Args:
            job_id: A publishing job.
        """
        assert "build-strict" in _declared_needs(job_id), (
            f"`{job_id}` would publish a site the gate never approved"
        )


class TestAPullRequestCannotDeploy:
    """Enabling `pull_request` on a workflow holding the deploy token is the risk here."""

    @pytest.mark.parametrize("job_id", _DEPLOY_JOBS)
    def test_no_deploy_job_can_be_satisfied_by_a_pull_request(self, job_id):
        """Each deploy `if:` must demand an event a pull request cannot be.

        Args:
            job_id: A publishing job.

        Test scenario:
            `deploy-main` requires `github.event_name == 'push'`; `deploy-release` requires a
            `release` event or a successful non-fork `workflow_run`. Neither is satisfiable by a
            `pull_request`, and this test fails if a future edit loosens either one.
        """
        condition = " ".join(_job(job_id)["if"].split())

        assert "pull_request" not in condition
        assert re.search(
            r"github\.event_name == '(push|release|workflow_run)'", condition
        ), condition

    def test_the_fork_guard_on_the_release_deploy_survives(self):
        """The workflow_run path must still refuse a fork's head repository."""
        condition = " ".join(_job("deploy-release")["if"].split())

        assert "github.event.workflow_run.head_repository.fork == false" in condition
