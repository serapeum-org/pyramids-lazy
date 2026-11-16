"""The no-silent-skip guard: its reason pattern, reason extraction and skip classification.

`tests/_skip_guard.py` decides whether a skip was caused by a missing optional dependency, which is
what `--strict-extra-skips` turns into a session failure. Its pattern therefore has to recognise
every reason shape `tests/_marks.py` produces -- a reason it does not match is a dependency skip
that stays invisible in exactly the CI job that was supposed to execute the test. The guard runs
against pytest report objects, so the tests below drive it with minimal stand-ins rather than a
live pytest session.
"""

from __future__ import annotations

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
        """The reflection above finds the `requires_*` markers, so the suite below is not empty."""
        assert _marker_reasons()

    @pytest.mark.parametrize("reason", _marker_reasons())
    def test_every_marker_reason_is_recognised(self, reason):
        """A `requires_*` reason that the pattern misses is a silently-invisible dependency skip."""
        assert DEPENDENCY_SKIP_REASON.search(reason), (
            f"reason not recognised as a dependency skip: {reason!r}"
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
