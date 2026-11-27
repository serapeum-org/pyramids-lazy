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
- `--locked`, without which a drifted `pixi.lock` is silently re-solved and the gate passes against
  dependencies the lock does not describe -- i.e. not the ones the deploy will install;
- the two halves of the review artifact: it is uploaded only on a pull request, and from the very
  directory `--site-dir` writes to. Either half moving alone keeps the run green while uploading
  nothing, which is the same silent-success failure mode as the missing gate itself;
- the gate's own checkout: `persist-credentials: false` and no explicit `ref:`, which is what keeps
  fork-controlled code (and the ambient token) out of the one job a pull request can reach.

The helpers get their own classes at the end. They are the module's blind spot: every assertion
above reads the workflow *through* them, so a helper that quietly returns nothing -- an unparsed
`needs` shape, a step list it never looked at -- reports a missing field or an unwired job rather
than failing where the fault is.
"""

from __future__ import annotations

import re
import sys
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

# The directory `mkdocs build` is told to write to. Captured rather than substring-matched so the
# upload step's `path:` can be compared against it: a `--site-dir` nobody uploads and an upload of
# a directory nothing wrote are both green runs that hand reviewers an empty artifact.
_SITE_DIR = re.compile(r"--site-dir\s+(\S+)")


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


class TestTheBuildIsPinnedToTheCommittedLock:
    """A gate that re-solves its environment gates a different dependency set than it claims."""

    def test_the_strict_build_asserts_the_lock(self):
        """`--locked` belongs on the same command that performs the strict build.

        Test scenario:
            `pixi run` without `--locked` re-solves a `pixi.lock` that no longer matches
            `pyproject.toml` instead of failing, so the gate would pass against freshly resolved
            dependencies while the deploy jobs -- and `tests.yml`'s `main` task, which does pass
            `--locked` -- install the committed ones. The flag is pixi's, not mkdocs', so this
            pins the whole `pixi run --locked` prefix rather than the bare word.
        """
        strict_runs = [
            run for run in _run_steps("build-strict") if "mkdocs build --strict" in run
        ]

        assert strict_runs, (
            "no step runs `mkdocs build --strict`, so there is no gate to pin"
        )
        assert all("pixi run --locked" in run for run in strict_runs), (
            f"the strict build does not run under `pixi run --locked`: {strict_runs}"
        )


class TestTheGateAndTheDeploysInstallOneEnvironment:
    """The gate only gates anything if it builds the dependency set that publishes."""

    @pytest.mark.parametrize("job_id", _DEPLOY_JOBS)
    def test_each_deploy_installs_the_environment_the_gate_builds(self, job_id):
        """The deploy's `install-groups` must name the gate's pixi environment.

        Args:
            job_id: A publishing job.

        Test scenario:
            `test_it_builds_in_the_docs_environment` asserts the gate side only -- that the
            `python-setup/pixi` step activates `docs` -- and its docstring asserts the other side
            in prose: that the deploys pass `install-groups: 'docs'`. Nothing read the deploys.
            Renaming the pixi environment on one side alone (or pointing a deploy at a `deploy`
            group) leaves a gate that builds one env and a deploy that installs another, which
            is the drift the gate exists to prevent.
        """
        gate_env = _step_using("build-strict", "python-setup/pixi")["with"][
            "environments"
        ]
        deploy_groups = _step_using(job_id, "mkdocs-deploy")["with"]["install-groups"]

        assert deploy_groups == gate_env, (
            f"`{job_id}` installs {deploy_groups!r} but the gate builds {gate_env!r}, so the "
            f"gate would approve a different dependency set than the one that publishes"
        )


class TestTheBuiltSiteReachesReviewersWithoutBeingPublished:
    """No PR-preview deploy exists, so the artifact is the only way to see a PR's pages."""

    def test_the_upload_only_happens_on_a_pull_request(self):
        """The artifact stands in for the deploy a pull request deliberately does not get.

        Test scenario:
            `build-strict` also runs on push-to-main, on `release: published` and via
            `workflow_run`, and on all three the deploy jobs publish the site for real. An
            ungated upload would attach a redundant artifact to every one of those runs, named
            `site-pr-` because `github.event.pull_request.number` is empty outside a pull
            request.
        """
        upload = _step_using("build-strict", "actions/upload-artifact")

        assert "if" in upload, (
            "the site upload is ungated, so it would also run on push, release and workflow_run"
        )
        assert "github.event_name == 'pull_request'" in upload["if"], (
            f"the upload is gated on something other than a pull request: {upload['if']!r}"
        )

    def test_the_uploaded_directory_is_the_one_the_build_writes(self):
        """`--site-dir` and the artifact's `path:` are two halves of a single fact.

        Test scenario:
            The build writes outside the checkout, so the upload cannot fall back to the
            workspace -- it has to name the same absolute directory. Moving either half alone
            (a `--site-dir` the upload does not read, or an upload of a directory the build
            never wrote) fails nothing: the run stays green and hands reviewers an empty
            artifact, which is indistinguishable from docs that built cleanly.
        """
        site_dirs = [
            match.group(1)
            for run in _run_steps("build-strict")
            if (match := _SITE_DIR.search(run))
        ]
        uploaded = _step_using("build-strict", "actions/upload-artifact")["with"][
            "path"
        ]

        assert site_dirs == [uploaded], (
            f"the build writes to {site_dirs} but the artifact uploads {uploaded!r}"
        )


class TestTheGateCannotRunForkControlledCode:
    """A pull request reaches `build-strict`, which makes its checkout the exposed surface."""

    def test_the_checkout_leaves_no_credentials_in_the_worktree(self):
        """`persist-credentials: false` keeps the ambient token out of `.git/config`.

        Test scenario:
            This is the one job a fork's pull request can run, and it runs the repository's own
            `pixi run`/mkdocs plugins over the fork's `docs/`. Without this, checkout writes an
            `http.extraheader` credential into the checkout that any of that code could read.
            The deploy jobs deliberately do not set it -- they are push/release-only and need a
            usable token -- so the assertion is about this job, not about every checkout.
        """
        checkout = _step_using("build-strict", "actions/checkout")

        assert checkout["with"].get("persist-credentials") is False, (
            "the gate's checkout persists credentials into a worktree a pull request controls: "
            f"{checkout['with']}"
        )

    def test_the_checkout_takes_no_explicit_ref(self):
        """Omitting `ref:` is what makes a pull request build the merge commit.

        Test scenario:
            On a `pull_request` event checkout's default is `refs/pull/<n>/merge`, not the
            fork's head, so the gate builds the code as it would land. Pinning the head sha or
            `github.head_ref` here would run fork-controlled content instead, in the one job a
            fork can trigger. Note `deploy-release` *does* pin a `ref:` -- it is unreachable
            from a pull request and needs the release branch tip -- so the omission is specific
            to this job.
        """
        checkout = _step_using("build-strict", "actions/checkout")

        assert "ref" not in checkout["with"], (
            "the gate's checkout names an explicit ref, so a pull request no longer builds the "
            f"merge commit: ref={checkout['with']['ref']!r}"
        )


class TestOnlyTheRunStepsAreCollected:
    """`_run_steps` reads jobs whose steps are mostly `uses:`, and has to skip those."""

    @pytest.mark.parametrize(
        "job_id, run_count",
        [("build-strict", 1), ("deploy-release", 1)],
    )
    def test_steps_without_a_run_are_skipped(self, job_id, run_count):
        """A `uses:`-only step contributes nothing and must not be indexed as `run`.

        Args:
            job_id: A job mixing `uses:` steps with exactly one `run:` step.
            run_count: How many of its steps carry a `run:`.

        Test scenario:
            Every `--strict` / `-e docs` / `--locked` / `--site-dir` assertion in this module is
            a substring search over this helper's output, so a helper that collected the wrong
            steps would decide those. `build-strict` is three `uses:` steps around one `run:`,
            `deploy-release` two around one; dropping the `"run" in step` filter raises
            `KeyError` on the first `uses:` step and inverting it returns the wrong count.
        """
        steps = _job(job_id)["steps"]
        runs = _run_steps(job_id)

        assert len(steps) > run_count, (
            f"`{job_id}` has no `uses:`-only steps left, so it no longer exercises the filter"
        )
        assert len(runs) == run_count, (
            f"expected {run_count} run script(s) from `{job_id}`'s {len(steps)} steps, got "
            f"{len(runs)}: {runs}"
        )

    def test_a_multi_line_script_is_normalised_to_one_line(self):
        """`deploy-release`'s version step is the workflow's only multi-line `run:`.

        Test scenario:
            A block scalar arrives with newlines and the YAML block's indentation, so a
            substring assertion against a command wrapped across lines would miss it. The
            helper joins the script's words with single spaces; this pins that on the one step
            whose shape can show it.
        """
        runs = _run_steps("deploy-release")

        assert len(runs) == 1, (
            f"expected one run script in `deploy-release`, got {runs}"
        )
        assert "\n" not in runs[0], f"newlines survived normalisation: {runs[0]!r}"
        assert "  " not in runs[0], (
            f"runs of whitespace survived normalisation: {runs[0]!r}"
        )


class TestDeclaredNeedsReadsBothShapesOfNeeds:
    """Both deploys write `needs: build-strict` as a bare string, so the list form is untaken."""

    def test_the_gate_itself_declares_no_needs(self):
        """`build-strict` is the root job, and must not make the helper raise.

        Test scenario:
            The `.get("needs", [])` default is reachable only from a job declaring none, and
            `_declared_needs` is otherwise only ever called on the two deploy jobs, which both
            declare one. It also pins the gate as a root job: giving it a `needs` of its own
            would put something ahead of the only check a pull request runs.
        """
        assert _declared_needs("build-strict") == [], (
            "the gate declares a `needs` of its own; it is meant to be the root job"
        )

    @pytest.mark.parametrize("job_id", _DEPLOY_JOBS)
    def test_the_bare_string_form_normalises_to_a_one_item_list(self, job_id):
        """`needs: build-strict` must read as a one-item list, not as the string itself.

        Args:
            job_id: A publishing job, each of which writes its `needs` as a bare string.

        Test scenario:
            `test_the_deploy_waits_for_the_strict_build` membership-tests the result, and
            `"build-strict" in "build-strict-preview"` is true for a raw string -- so a deploy
            gated on a similarly-named job would read as wired if the string were not wrapped.
            Equality also pins that the deploys wait for the gate and nothing else.
        """
        needs = _declared_needs(job_id)

        assert needs == ["build-strict"], (
            f"`{job_id}` must declare exactly `needs: build-strict`, read as {needs!r}"
        )

    def test_a_list_of_needs_is_returned_in_the_order_written(self, monkeypatch):
        """The sequence form has to survive too, though this workflow does not use it yet.

        Args:
            monkeypatch: Replaces `_workflow` with a two-job document whose deploy declares a
                list -- the shape a second gate alongside `build-strict` would introduce.

        Test scenario:
            With only bare strings live, `list(needs)` is dead code here, so a mistake in it
            would surface the first time a gate is added: as a deploy reported unwired, or
            worse, a `needs` silently read as empty.
        """
        workflow = {
            "jobs": {
                "build-strict": {},
                "deploy-main": {"needs": ["build-strict", "another-gate"]},
            }
        }
        monkeypatch.setattr(sys.modules[__name__], "_workflow", lambda: workflow)

        assert _declared_needs("deploy-main") == ["build-strict", "another-gate"], (
            "a sequence `needs` must come back as a list in the order written"
        )


class TestTheHelpersFailLoudlyOnAWorkflowTheyCannotRead:
    """Every assertion here reads the workflow through a helper, so silence is the danger."""

    def test_a_missing_job_is_named_alongside_the_jobs_that_exist(self):
        """A renamed or deleted job must be reported as missing, not as a missing field.

        Test scenario:
            Without the assertion in `_job`, `_workflow()["jobs"]["deploy-preview"]` raises a
            bare `KeyError: 'deploy-preview'`, and the tests that read a job's `if:` or `steps:`
            would report the renamed job's *fields* as the problem. The enumeration is checked
            by its exact spelling rather than by looking for a job id in the text: this module
            is rewritten by pytest, so a `_job` that named no jobs at all would still raise a
            message carrying `assert 'deploy-preview' in {'build-strict': ...}` -- the whole
            jobs dict -- and a substring check for `build-strict` would pass against it.
        """
        with pytest.raises(
            AssertionError, match=r"no `deploy-preview` job"
        ) as exc_info:
            _job("deploy-preview")

        assert f"jobs are {sorted(_workflow()['jobs'])}" in str(exc_info.value), (
            f"the message should enumerate the jobs that do exist, got: {exc_info.value}"
        )

    def test_a_missing_action_is_raised_rather_than_returning_nothing(self):
        """`_step_using` must not fall off the end of a job's steps and return `None`.

        Test scenario:
            Four tests here locate a step by its action (`actions/checkout`,
            `python-setup/pixi`, `actions/upload-artifact`, `mkdocs-deploy`). If a step is
            removed or its action swapped, a helper returning `None` turns those into
            `TypeError: 'NoneType' object is not subscriptable` -- or, worse, silently satisfies
            an `in` check elsewhere. The substring searched is quoted into the message, so the
            failure names the action that is gone.
        """
        with pytest.raises(
            AssertionError, match=r"no step using 'actions/upload-pages-artifact'"
        ):
            _step_using("build-strict", "actions/upload-pages-artifact")

    def test_a_job_without_steps_is_reported_rather_than_read_as_empty(
        self, monkeypatch
    ):
        """A job calling a reusable workflow has no `steps:` at all.

        Args:
            monkeypatch: Replaces `_workflow` with a document whose `build-strict` is a
                reusable-workflow call -- a legitimate rewrite that carries `uses:` at job
                level and no steps.

        Test scenario:
            Both helpers default to `[]` for a stepless job, which no live job exercises. The
            distinction that matters is what each then does: `_step_using` still raises, so an
            action assertion fails loudly, while `_run_steps` returns nothing -- which is why
            `test_it_builds_with_strict` and the other command tests assert `any(...)` with the
            scripts attached to the message rather than trusting the helper to have found any.
        """
        workflow = {
            "jobs": {"build-strict": {"uses": "./.github/workflows/reusable-docs.yml"}}
        }
        monkeypatch.setattr(sys.modules[__name__], "_workflow", lambda: workflow)

        with pytest.raises(AssertionError, match=r"no step using 'actions/checkout'"):
            _step_using("build-strict", "actions/checkout")
        assert _run_steps("build-strict") == [], (
            "a job without steps must yield no run scripts rather than raising"
        )
