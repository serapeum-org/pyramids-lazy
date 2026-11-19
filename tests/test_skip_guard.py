"""The no-silent-skip gate: its reason pattern, its classifier, and the conftest hooks around it.

`tests/_skip_guard.py` decides whether a skip was caused by a missing optional dependency, which is
what `--strict-extra-skips` turns into a session failure. A reason the pattern does not match is a
dependency skip that stays invisible in exactly the CI job that was supposed to execute the test;
a reason it matches too eagerly turns a deliberate skip red. Both directions are pinned here.

Five classes, covering the gate end to end apart from its registration:

- `TestDependencySkipReason` and `TestSuiteReasonInventory` -- the pattern, against every
  `reason="..."` literal the suite declares, in both directions so a stale row fails too.
- `TestSkipReason` and `TestIsUnexpectedDependencySkip` -- reason extraction and classification,
  including the `wasxfail` exclusion.
- `TestStrictExtraSkipsWiring` -- the `tests/conftest.py` hooks that register the flag, record a
  classified skip, flip the exit status and write the summary.

The guard runs against pytest report objects, so these drive it with minimal stand-ins rather than
a live pytest session. That means they verify the hook *bodies*: they would not catch a hook pytest
had stopped invoking.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _marks, conftest
from tests._skip_guard import (
    DEPENDENCY_SKIP_REASON,
    is_unexpected_dependency_skip,
    skip_reason,
)


def _report(**attrs) -> SimpleNamespace:
    """Build a stand-in pytest report carrying only the attributes the guard reads.

    Args:
        **attrs: Report attributes to set, e.g. `skipped`, `wasxfail`, `longrepr`.

    Returns:
        SimpleNamespace: An object the guard can interrogate with `getattr`.
    """
    return SimpleNamespace(**attrs)


#: The `requires_*` marker names in `_marks.py`, pinned so the reflection below cannot silently
#: shrink to a subset (or to nothing) and leave the parametrized suite vacuously green.
#:
#: Ten names, but not ten distinct markers: `requires_dask`, `requires_dask_geopandas` and
#: `requires_pyarrow` are legacy aliases of `requires_lazy`, `requires_parquet_lazy` and
#: `requires_parquet`. Pinning them records what `_marks.py` exports today; if the aliases are
#: dropped, this set is the thing to update, and the failure message says which names went.
#:
#: They split into two kinds, and keeping that split honest is the point of this module:
#: CATEGORY markers carry no condition at all (their dependencies are hard, so a skip would
#: only ever hide a broken install), while CONDITIONAL ones gate on the `[parquet]` extra,
#: the only genuinely optional dependency set.
CATEGORY_MARKER_NAMES = frozenset(
    {
        "requires_dask",
        "requires_dask_array",
        "requires_kerchunk",
        "requires_lazy",
        "requires_netcdf_lazy",
    }
)

#: The `requires_*` names that DO gate on a condition -- all of them on the `[parquet]` extra.
CONDITIONAL_MARKER_NAMES = frozenset(
    {
        "requires_dask_dataframe",
        "requires_dask_geopandas",
        "requires_parquet",
        "requires_parquet_lazy",
        "requires_pyarrow",
    }
)

EXPECTED_MARKER_NAMES = CATEGORY_MARKER_NAMES | CONDITIONAL_MARKER_NAMES

#: Every `reason="..."` literal declared in the suite, and whether the gate must see it.
#:
#: Scope, stated precisely because the previous wording over-claimed: this covers skip reasons
#: written as a `reason="..."` keyword. It does NOT cover reasons pytest generates at runtime --
#: `pytest.importorskip("x")` produces `could not import 'x': No module named 'y'`, where the
#: missing module may differ from the one named. Those are matched by the `could not import`
#: alternative, which `TestDependencySkipReason` covers separately.
#:
#: Collected by scanning the suite rather than by reflection over `_marks.py`, because `_marks.py`
#: is not the only place skips are declared.
#:
#: Several rows are currently UNREACHABLE: `zarr`, `dask`, `kerchunk` and `h5py` are hard
#: `[project]` dependencies, so their `is None` skipifs can never fire in an installed environment.
#: They are kept because the wording convention still applies if a dependency is ever made
#: optional, and because the gate must classify them correctly either way. Only the `parquet`
#: extra is genuinely optional today.
SUITE_REASONS: tuple[tuple[str, bool], ...] = (
    ("dask-geopandas (pyramids-lazy[parquet]) not installed", True),
    ("dask.dataframe needs pyarrow (pyramids-lazy[parquet])", True),
    ("pyramids-lazy[parquet] not installed", True),
    # Deliberately NOT a dependency skip: the `live` marker's own opt-in message.
    ("live network test; run it with `-m live`", False),
    # Deliberately NOT a dependency skip: the package is installed, it is simply older than the
    # feature under test. Classifying it would redden every job until core ships the forwarder,
    # which is the opposite of what the gate is for.
    (
        "installed pyramids-core has no open_mfdataset(chunks=) forwarder yet (#1458)",
        False,
    ),
    # Deliberately NOT a dependency skip: nothing is missing, the platform simply has no ELF.
    # `tests/ci` cross-checks `soname.py` against a real shared object, which only exists on
    # Linux; the macOS and Windows legs have nothing to read and no install could change that.
    ("ELF is Linux-only here", False),
)


def _marker_reasons() -> list[str]:
    """Every `reason=` string the `requires_*` skipif markers in `_marks.py` carry."""
    reasons = []
    for name in dir(_marks):
        if not name.startswith("requires_"):
            continue
        mark = getattr(_marks, name)
        reason = getattr(mark, "kwargs", {}).get("reason")
        if isinstance(reason, str):
            reasons.append(reason)
    return reasons


class TestDependencySkipReason:
    """Every reason `_marks.py` emits must be recognised as a dependency skip."""

    def test_the_markers_expose_reasons_to_check(self):
        """Reflection finds every expected `requires_*` marker, each carrying a string reason.

        Test scenario:
            Asserting only non-emptiness would let the reflection silently shrink to one marker
            and leave `test_every_marker_reason_is_recognised` vacuously green, so the marker
            names and the reason count are both pinned.
        """
        found = {name for name in dir(_marks) if name.startswith("requires_")}
        assert found == EXPECTED_MARKER_NAMES, (
            f"marker set drifted; added {sorted(found - EXPECTED_MARKER_NAMES)}, "
            f"removed {sorted(EXPECTED_MARKER_NAMES - found)}"
        )
        conditional = {
            name
            for name in found
            if isinstance(
                getattr(getattr(_marks, name), "kwargs", {}).get("reason"), str
            )
        }
        assert conditional == CONDITIONAL_MARKER_NAMES, (
            "the category/conditional split drifted; markers that newly carry a skip condition: "
            f"{sorted(conditional - CONDITIONAL_MARKER_NAMES)}, markers that lost one: "
            f"{sorted(CONDITIONAL_MARKER_NAMES - conditional)}. A hard dependency must not gain "
            "a skipif -- it would hide a broken install behind a green 'skipped'."
        )

    @pytest.mark.parametrize("reason", sorted(set(_marker_reasons())))
    def test_every_marker_reason_is_recognised(self, reason):
        """A `requires_*` reason that the pattern misses is a silently-invisible dependency skip."""
        assert DEPENDENCY_SKIP_REASON.search(reason), (
            f"reason not recognised as a dependency skip: {reason!r}"
        )


class TestSuiteReasonInventory:
    """Every skip reason the suite really emits is classified as intended."""

    @pytest.mark.parametrize("reason, is_dependency_skip", SUITE_REASONS)
    def test_each_suite_reason_is_classified_as_intended(
        self, reason, is_dependency_skip
    ):
        """Test the pattern's verdict on each real reason matches the inventory.

        Args:
            reason: A skip reason the suite produces.
            is_dependency_skip: Whether the gate must treat it as a dependency skip.

        Test scenario:
            Covers both directions — a dependency skip the gate must see, and a deliberate skip
            it must leave alone. `_marks.py` reflection alone misses the skipifs declared
            elsewhere in the suite.
        """
        matched = bool(DEPENDENCY_SKIP_REASON.search(reason))
        assert matched is is_dependency_skip, (
            f"{reason!r}: expected dependency-skip={is_dependency_skip}, got {matched}"
        )

    def test_the_inventory_matches_the_suite(self):
        """Test every `reason="..."` literal in the suite appears in the inventory above.

        Test scenario:
            A new skip wording that nobody classified is the blind spot the gate keeps falling
            into, so adding one must fail here until it is listed with an intended verdict.
        """
        self_path = Path(__file__).resolve()
        files_with_reasons = 0
        found: set[str] = set()
        for path in self_path.parent.rglob("*.py"):
            # Compare resolved paths, not basenames: a same-named file in a subdirectory would
            # otherwise be skipped silently.
            if "__pycache__" in path.parts or path.resolve() == self_path:
                continue
            reasons = re.findall(r'reason="([^"]+)"', path.read_text(encoding="utf-8"))
            if reasons:
                files_with_reasons += 1
                found |= set(reasons)

        # An empty scan cannot pass silently: the reverse check at the end of this test requires
        # every catalogued row to be found, so a glob that stopped matching fails there with a
        # "stale row" message. This assertion exists only to make that failure legible, naming the
        # scan rather than the catalogue as the thing that broke.
        assert found, (
            f"scanned {files_with_reasons} file(s) and found no skip reasons at all — the scan is "
            "broken, not the catalogue"
        )

        catalogued = {reason for reason, _ in SUITE_REASONS}
        assert found <= catalogued, (
            f"uncatalogued skip reason(s): {sorted(found - catalogued)} — add them to "
            "SUITE_REASONS with the verdict the gate should give"
        )
        # The other direction: a row nobody emits any more is stale, and leaving it in makes the
        # inventory describe a suite that no longer exists. Rows for reasons declared outside a
        # `reason="..."` literal would trip this, so there are none.
        assert catalogued <= found, (
            f"stale SUITE_REASONS row(s) no longer emitted by the suite: "
            f"{sorted(catalogued - found)} — remove them or fix the wording they track"
        )

    def test_this_packages_own_extra_is_recognised(self):
        """A reason naming only `pyramids-lazy[...]`, with no 'not installed', still matches."""
        reason = "dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])"
        assert DEPENDENCY_SKIP_REASON.search(reason)

    @pytest.mark.parametrize(
        "reason",
        [
            "needs cleopatra (pyramids-core[viz])",
            "pyramids_lazy[parquet] missing",
            "Pyramids-Lazy[parquet] missing",
            "pyramids-core2[viz] missing",
            "pyramids[parquet] missing",
        ],
        ids=[
            "core-extra",
            "underscore-dist",
            "mixed-case",
            "digit-in-dist",
            "bare-dist",
        ],
    )
    def test_the_extra_reference_shape_matches_on_its_own(self, reason):
        """Test the `pyramids…[` alternative matches without help from the other two.

        Args:
            reason: A reason whose only matchable feature is the extra reference.

        Test scenario:
            Deliberately excludes "not installed" and "could not import", so a reason that
            carries only the extra reference cannot pass via another alternative. The earlier
            version of this test used `"pyramids-core[viz] not installed"`, which matched on
            `not installed` and so proved nothing about this shape.
        """
        assert DEPENDENCY_SKIP_REASON.search(reason), (
            f"extra reference not matched: {reason!r}"
        )

    @pytest.mark.parametrize(
        "reason",
        ["pyramidsomething[extra] missing", "pyramid[x] missing"],
        ids=["different-dist", "singular-prefix"],
    )
    def test_a_different_distribution_is_not_matched(self, reason):
        """Test the extra-reference shape does not swallow an unrelated distribution name.

        Args:
            reason: A reason naming a distribution that merely starts with the same letters.

        Test scenario:
            `pyramidsomething` is a different package, not an extra of this one. An earlier
            unanchored character class matched it; the `[_-]`-anchored group does not.
        """
        assert not DEPENDENCY_SKIP_REASON.search(reason), f"over-matched: {reason!r}"

    def test_unrelated_wording_is_not_treated_as_a_dependency_skip(self):
        """Free-text skips stay out, so the guard does not fail a deliberate skip."""
        assert not DEPENDENCY_SKIP_REASON.search("dask + zarr required")
        assert not DEPENDENCY_SKIP_REASON.search(
            "live network test; run it with `-m live`"
        )


class TestSkipReason:
    """Tests for skip_reason."""

    def test_three_tuple_longrepr_yields_the_reason_text(self):
        """Test skip_reason unwraps pytest's `(path, lineno, text)` skip longrepr.

        Test scenario:
            A real skip reports `longrepr` as a 3-tuple whose third element carries the
            "Skipped: <reason>" text; that element is what matching needs.
        """
        report = _report(
            longrepr=("tests/test_x.py", 12, "Skipped: pyarrow not installed")
        )
        result = skip_reason(report)
        assert result == "Skipped: pyarrow not installed", (
            f"Unexpected reason: {result!r}"
        )

    @pytest.mark.parametrize(
        "longrepr, expected",
        [
            (("only", "two"), "('only', 'two')"),
            ("a bare string longrepr", "a bare string longrepr"),
            (None, "None"),
        ],
        ids=["tuple-wrong-arity", "plain-string", "none"],
    )
    def test_other_longrepr_shapes_are_stringified(self, longrepr, expected):
        """Test skip_reason falls back to `str()` for any non-3-tuple longrepr.

        Args:
            longrepr: The report's longrepr value.
            expected: The stringified form matching should receive.

        Test scenario:
            Only a 3-tuple is unwrapped; every other shape is stringified so the caller
            always has text to match against rather than a type error.
        """
        result = skip_reason(_report(longrepr=longrepr))
        assert result == expected, f"Expected {expected!r}, got {result!r}"

    def test_missing_longrepr_attribute_is_tolerated(self):
        """Test skip_reason returns a string, and no dependency-skip, for a bare report.

        Test scenario:
            The attribute is read with `getattr(..., None)`, so a report lacking it must not
            raise. The contract asserted here is "a string that is not matched", not the exact
            `str(None)` spelling — `"None"` is an artefact of the fallback, and pinning it would
            freeze an accident rather than a guarantee.
        """
        result = skip_reason(_report())
        assert isinstance(result, str), (
            f"expected a string, got {type(result).__name__}"
        )
        assert not DEPENDENCY_SKIP_REASON.search(result), (
            f"a report with no longrepr must not read as a dependency skip, got {result!r}"
        )

    def test_a_none_reason_slot_yields_none(self):
        """Test a 3-tuple whose reason slot is `None` returns `None`, not a string.

        Test scenario:
            `skip_reason` unwraps `longrepr[2]` verbatim, so this is the one shape that can
            return a non-string. It is why `is_unexpected_dependency_skip`'s `or ""` fallback is
            load-bearing rather than defensive clutter: `re.search(None)` raises `TypeError`.
        """
        assert skip_reason(_report(longrepr=("f.py", 1, None))) is None


class TestIsUnexpectedDependencySkip:
    """Tests for is_unexpected_dependency_skip."""

    def test_a_dependency_skip_is_reported(self):
        """Test a genuine missing-dependency skip is classified as unexpected.

        Test scenario:
            Skipped, no `wasxfail`, and a reason the pattern recognises — the one case the
            strict gate must fail on.
        """
        # No `wasxfail` at all: a real non-xfail skip report does not carry the attribute, so
        # passing `wasxfail=None` would be testing a shape pytest never produces.
        report = _report(
            skipped=True,
            longrepr=(
                "tests/test_x.py",
                3,
                "Skipped: pyramids-lazy[parquet] not installed",
            ),
        )
        assert is_unexpected_dependency_skip(report) is True, (
            "a dependency skip must be reported"
        )

    @pytest.mark.parametrize(
        "reason",
        [
            "Skipped: dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])",
            "Skipped: could not import 'kerchunk'",
            "Skipped: needs cleopatra (pyramids-core[viz])",
        ],
        ids=["this-package-extra", "importorskip", "core-extra"],
    )
    def test_each_canonical_reason_shape_is_reported(self, reason):
        """Test every canonical dependency-skip wording trips the guard.

        Args:
            reason: A skip reason in one of the three documented shapes.

        Test scenario:
            The extra-reference shape carries no "not installed", so it exercises the
            alternative that silently stopped matching when the reasons were rebranded.
        """
        report = _report(skipped=True, longrepr=("f.py", 1, reason))
        assert is_unexpected_dependency_skip(report) is True, (
            f"not recognised: {reason!r}"
        )

    def test_a_non_skipped_report_is_ignored(self):
        """Test a passing or failing report is never a dependency skip.

        Test scenario:
            `skipped` is False, so the guard returns early without inspecting the reason.
        """
        report = _report(skipped=False, longrepr=("f.py", 1, "Skipped: not installed"))
        assert is_unexpected_dependency_skip(report) is False, (
            "only skips may be reported"
        )

    def test_a_report_without_the_skipped_attribute_is_ignored(self):
        """Test a report lacking `skipped` is treated as not skipped.

        Test scenario:
            `skipped` is read with a `False` default, so an unusual report object is ignored
            rather than raising inside the gate.
        """
        assert is_unexpected_dependency_skip(_report()) is False, (
            "a bare report must be ignored"
        )

    @pytest.mark.parametrize(
        "longrepr",
        [
            "ExceptionChainRepr-ish text mentioning not installed",
            None,
            ("f.py", 1, "Skipped: not installed"),
        ],
        ids=["repr-object-text", "none", "three-tuple"],
    )
    def test_an_xfail_is_excluded_whatever_its_longrepr_shape(self, longrepr):
        """Test `wasxfail` excludes a report before the reason is ever inspected.

        Args:
            longrepr: A longrepr shape, each carrying matchable text or none at all.

        Test scenario:
            An xfail also reports `skipped=True`. It ran and is visible as xfailed, so it must
            not trip the gate even when its reason names a dependency. Parametrized over the
            longrepr shape because a real xfail does not produce the `(path, lineno, text)`
            tuple a skip does — it carries an `ExceptionChainRepr` — and the exclusion must not
            depend on that shape, since it happens before any reason matching.
        """
        report = _report(
            skipped=True,
            wasxfail="reason: the optional driver is not installed",
            longrepr=longrepr,
        )
        assert is_unexpected_dependency_skip(report) is False, (
            "an xfail is not a dependency skip"
        )

    def test_a_bare_xfail_with_an_empty_reason_is_still_excluded(self):
        """Test `wasxfail=""` excludes the report, so the check cannot become a truthiness test.

        Test scenario:
            A bare `@pytest.mark.xfail` carries no reason, and pytest sets `wasxfail` to the
            empty string rather than to `None`. The guard uses `is not None` precisely for this:
            rewriting it as `if getattr(report, "wasxfail", None):` would let a bare xfail whose
            skip text happens to say "not installed" fail an armed job. Nothing else in the
            suite pins that distinction.
        """
        report = _report(
            skipped=True,
            wasxfail="",
            longrepr=("f.py", 1, "Skipped: zarr not installed"),
        )
        assert is_unexpected_dependency_skip(report) is False, (
            "a bare xfail (wasxfail='') must still be excluded"
        )

    def test_an_unmatched_reason_is_not_reported(self):
        """Test a deliberate skip with free-text wording is left alone.

        Test scenario:
            Skipped with a reason the pattern does not recognise, e.g. the `live` marker's own
            message — turning those red is the false positive the narrow pattern avoids.
        """
        report = _report(
            skipped=True,
            longrepr=("f.py", 1, "Skipped: live network test; run it with `-m live`"),
        )
        assert is_unexpected_dependency_skip(report) is False, (
            "free-text skips stay invisible"
        )


class TestStrictExtraSkipsWiring:
    """The conftest plumbing that turns a classified skip into a failed session."""

    @pytest.fixture(autouse=True)
    def _isolate_the_recorder(self, monkeypatch):
        """Give each test its own recorder list, so the hooks cannot leak across tests."""
        monkeypatch.setattr(conftest, "_strict_extra_skips", [])

    def test_the_option_is_registered_off_by_default(self):
        """Test `--strict-extra-skips` is a `store_true` defaulting to False.

        Test scenario:
            An ordinary or partial-install run must still skip cleanly, so the gate has to be
            opt-in. Reads the registration the hook performs rather than trusting the help text.
        """
        recorded = {}

        class _Parser:
            def addoption(self, name, **kwargs):
                recorded[name] = kwargs

        conftest.pytest_addoption(_Parser())

        assert "--strict-extra-skips" in recorded, "the option was not registered"
        assert recorded["--strict-extra-skips"]["action"] == "store_true"
        assert recorded["--strict-extra-skips"]["default"] is False

    def test_a_dependency_skip_is_recorded_with_its_nodeid(self):
        """Test the logreport hook records a classified skip as `<nodeid> -> <reason>`."""
        report = _report(
            skipped=True,
            nodeid="tests/test_x.py::test_y",
            longrepr=("f.py", 1, "Skipped: zarr not installed"),
        )

        conftest.pytest_runtest_logreport(report)

        assert conftest._strict_extra_skips == [
            "tests/test_x.py::test_y -> Skipped: zarr not installed"
        ]

    def test_an_unrelated_skip_is_not_recorded(self):
        """Test a free-text skip leaves the recorder empty, so the gate stays quiet."""
        report = _report(
            skipped=True,
            nodeid="tests/test_x.py::test_y",
            longrepr=("f.py", 1, "Skipped: live network test; run it with `-m live`"),
        )

        conftest.pytest_runtest_logreport(report)

        assert conftest._strict_extra_skips == []

    @pytest.mark.parametrize(
        "flag, recorded, expected_status",
        [
            (True, ["tests/x.py::y -> Skipped: zarr not installed"], 1),
            (False, ["tests/x.py::y -> Skipped: zarr not installed"], 0),
            (True, [], 0),
        ],
        ids=["armed-with-a-skip", "unarmed-with-a-skip", "armed-without-a-skip"],
    )
    def test_the_session_fails_only_when_armed_and_a_skip_was_recorded(
        self, flag, recorded, expected_status
    ):
        """Test `pytest_sessionfinish` flips the exit status only for the one case that matters.

        Args:
            flag: Whether `--strict-extra-skips` was passed.
            recorded: The dependency-skips recorded during the session.
            expected_status: The exit status the hook should leave behind.

        Test scenario:
            Covers the full truth table, so neither arming the gate on its own nor a recorded
            skip on its own can fail a session.
        """
        conftest._strict_extra_skips.extend(recorded)

        class _Config:
            def getoption(self, name):
                assert name == "--strict-extra-skips", f"unexpected option {name!r}"
                return flag

        class _Session:
            config = _Config()
            exitstatus = 0

        session = _Session()
        conftest.pytest_sessionfinish(session, exitstatus=0)

        assert session.exitstatus == expected_status

    @pytest.mark.parametrize(
        "flag, recorded, expect_output",
        [
            (True, ["tests/x.py::y -> Skipped: zarr not installed"], True),
            (False, ["tests/x.py::y -> Skipped: zarr not installed"], False),
            (True, [], False),
        ],
        ids=["armed-with-a-skip", "unarmed-with-a-skip", "armed-without-a-skip"],
    )
    def test_the_summary_is_written_only_when_the_gate_fired(
        self, flag, recorded, expect_output
    ):
        """Test `pytest_terminal_summary` reports the offending skips, and only then.

        Args:
            flag: Whether `--strict-extra-skips` was passed.
            recorded: The dependency-skips recorded during the session.
            expect_output: Whether the reporter should have been written to.

        Test scenario:
            A failed session whose summary says nothing is unactionable, so the armed case must
            name each offending nodeid. The two negative rows keep the summary quiet otherwise.
        """
        conftest._strict_extra_skips.extend(recorded)
        written: list[str] = []

        class _Reporter:
            class config:
                @staticmethod
                def getoption(name):
                    assert name == "--strict-extra-skips", f"unexpected option {name!r}"
                    return flag

            def write_sep(self, _sep, title):
                written.append(title)

            def write_line(self, line):
                written.append(line)

        conftest.pytest_terminal_summary(_Reporter())

        assert bool(written) is expect_output, f"unexpected summary output: {written}"
        if expect_output:
            assert any("tests/x.py::y" in line for line in written), (
                f"the summary must name the offending nodeid, got {written}"
            )
