"""The no-silent-skip guard: its reason pattern, reason extraction and skip classification.

`tests/_skip_guard.py` decides whether a skip was caused by a missing optional dependency, which is
what `--strict-extra-skips` turns into a session failure. Its pattern therefore has to recognise
every reason shape `tests/_marks.py` produces -- a reason it does not match is a dependency skip
that stays invisible in exactly the CI job that was supposed to execute the test. The guard runs
against pytest report objects, so the tests below drive it with minimal stand-ins rather than a
live pytest session.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _marks
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
EXPECTED_MARKER_NAMES = frozenset(
    {
        "requires_dask",
        "requires_dask_array",
        "requires_dask_dataframe",
        "requires_dask_geopandas",
        "requires_kerchunk",
        "requires_lazy",
        "requires_netcdf_lazy",
        "requires_parquet",
        "requires_parquet_lazy",
        "requires_pyarrow",
    }
)

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
    ("dask + zarr (pyramids-lazy) not installed", True),
    ("dask-geopandas (pyramids-lazy[parquet]) not installed", True),
    ("dask.array not installed", True),
    ("dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])", True),
    ("kerchunk + h5py (pyramids-lazy) not installed", True),
    ("kerchunk not installed", True),
    ("pyramids-lazy[parquet] not installed", True),
    ("zarr not installed", True),
    # Deliberately NOT a dependency skip: the `live` marker's own opt-in message.
    ("live network test; run it with `-m live`", False),
    # Deliberately NOT a dependency skip: the package is installed, it is simply older than the
    # feature under test. Classifying it would redden every job until core ships the forwarder,
    # which is the opposite of what the gate is for.
    (
        "installed pyramids-core has no open_mfdataset(chunks=) forwarder yet (#1458)",
        False,
    ),
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
        reasons = _marker_reasons()
        assert len(reasons) == len(EXPECTED_MARKER_NAMES), (
            f"expected a string reason per marker, got {len(reasons)} "
            f"for {len(EXPECTED_MARKER_NAMES)} markers"
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

        # Counting FILES THAT CARRY A REASON, not every .py in the tree: the suite has ~60 modules
        # but only a handful declare skips, so a glob that silently stopped matching those would
        # still clear a threshold set against the total. Without this the subset check below is
        # vacuously true on an empty scan -- the way the marker reflection went blind (round-1 M5).
        assert files_with_reasons >= 5, (
            f"expected several files declaring skip reasons, found {files_with_reasons}"
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
