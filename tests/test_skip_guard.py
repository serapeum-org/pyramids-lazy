"""The no-silent-skip guard's reason pattern, against the reasons this suite really emits.

`tests/_skip_guard.py` decides whether a skip was caused by a missing optional dependency, which is
what `--strict-extra-skips` turns into a session failure. Its pattern therefore has to recognise
every reason shape `tests/_marks.py` produces -- a reason it does not match is a dependency skip
that stays invisible in exactly the CI job that was supposed to execute the test.
"""

from __future__ import annotations

import pytest

from tests import _marks
from tests._skip_guard import DEPENDENCY_SKIP_REASON


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
