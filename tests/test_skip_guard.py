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

#: Every skip reason the suite actually produces, and whether the gate must see it. Collected with
#: `grep -rnoE 'reason="[^"]+"' tests/` rather than by reflection, because `_marks.py` is not the
#: only place skips are declared -- the `requires zarr` skipifs live in `tests/dataset/ops/`, and
#: missing them is exactly the blind spot this inventory exists to catch. A new wording that is not
#: listed here fails `test_the_inventory_matches_the_suite`, which forces a decision rather than a
#: silent gap.
SUITE_REASONS: tuple[tuple[str, bool], ...] = (
    ("dask + zarr (pyramids-lazy) not installed", True),
    ("dask-geopandas (pyramids-lazy[parquet]) not installed", True),
    ("dask-geopandas not installed", True),
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
        assert len(_marker_reasons()) == len(EXPECTED_MARKER_NAMES), (
            f"expected a string reason per marker, got {len(_marker_reasons())} "
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
        tests_dir = Path(__file__).resolve().parent
        found: set[str] = set()
        for path in tests_dir.rglob("*.py"):
            if "__pycache__" in path.parts or path.name == Path(__file__).name:
                continue
            found |= set(
                re.findall(r'reason="([^"]+)"', path.read_text(encoding="utf-8"))
            )

        catalogued = {reason for reason, _ in SUITE_REASONS}
        assert found <= catalogued, (
            f"uncatalogued skip reason(s): {sorted(found - catalogued)} — add them to "
            "SUITE_REASONS with the verdict the gate should give"
        )

    def test_this_packages_own_extra_is_recognised(self):
        """A reason naming only `pyramids-lazy[...]`, with no 'not installed', still matches."""
        reason = "dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])"
        assert DEPENDENCY_SKIP_REASON.search(reason)

    def test_cores_extra_is_still_recognised(self):
        """Reasons relocated from core, naming `pyramids-core[...]`, keep matching."""
        assert DEPENDENCY_SKIP_REASON.search("pyramids-core[viz] not installed")
        assert DEPENDENCY_SKIP_REASON.search("needs cleopatra (pyramids-core[viz])")

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
        """Test skip_reason returns "None" when the report has no longrepr at all.

        Test scenario:
            The attribute is read with `getattr(..., None)`, so a report lacking it must not
            raise — the guard has to survive unusual report objects.
        """
        result = skip_reason(_report())
        assert result == "None", (
            f"Expected 'None' for a report without longrepr, got {result!r}"
        )


class TestIsUnexpectedDependencySkip:
    """Tests for is_unexpected_dependency_skip."""

    def test_a_dependency_skip_is_reported(self):
        """Test a genuine missing-dependency skip is classified as unexpected.

        Test scenario:
            Skipped, no `wasxfail`, and a reason the pattern recognises — the one case the
            strict gate must fail on.
        """
        report = _report(
            skipped=True,
            wasxfail=None,
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
        report = _report(skipped=True, wasxfail=None, longrepr=("f.py", 1, reason))
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

    def test_an_xfail_is_excluded_even_when_its_reason_mentions_a_dependency(self):
        """Test an xfail carrying `wasxfail` is excluded before any reason matching.

        Test scenario:
            An xfail also reports `skipped=True`. It ran and is visible as xfailed, so it must
            not trip the gate — even when its reason names a dependency, as the OGR Parquet
            driver xfail does.
        """
        report = _report(
            skipped=True,
            wasxfail="reason: OGR Parquet driver not installed",
            longrepr=("f.py", 1, "Skipped: not installed"),
        )
        assert is_unexpected_dependency_skip(report) is False, (
            "an xfail is not a dependency skip"
        )

    def test_an_unmatched_reason_is_not_reported(self):
        """Test a deliberate skip with free-text wording is left alone.

        Test scenario:
            Skipped with a reason the pattern does not recognise, e.g. the `live` marker's own
            message — turning those red is the false positive the narrow pattern avoids.
        """
        report = _report(
            skipped=True,
            wasxfail=None,
            longrepr=("f.py", 1, "Skipped: live network test; run it with `-m live`"),
        )
        assert is_unexpected_dependency_skip(report) is False, (
            "free-text skips stay invisible"
        )
