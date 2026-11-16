"""Reason-matching for the ``--strict-extra-skips`` gate.

The gate (wired up in :mod:`tests.conftest`, documented in ``docs/how-to/testing.md``) turns a
skip for a missing optional dependency into a failure in the CI jobs whose environment installs
that dependency. This module isolates the "is this skip a missing-dependency skip?" decision so it
is unit-testable without a live pytest session.
"""

from __future__ import annotations

import re

# The optional-dependency skip idioms this suite actually produces:
#   * the ``requires_<extra>`` decorators in ``tests/_marks.py`` — e.g.
#     "pyramids-lazy[parquet] not installed", "the optional interop dependency is not installed",
#     "dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])";
#   * ``pytest.importorskip("x")`` — default reason "could not import 'x'".
# The pattern matches those three shapes ("not installed", a "pyramids-<dist>[" extra reference, and
# "could not import"). It deliberately does NOT try to catch arbitrary free-text wording (e.g. a
# bare ``pytest.skip("dask + zarr required")``): matching generic words like "required" or "not
# available" would false-positive legitimate platform skips and turn them red in a strict job. A
# dependency skip must therefore be written with one of the canonical forms above — the project
# convention — to be seen by the gate.
#
# The distribution part is a character class rather than a literal: the reasons here name THIS
# package's extra (``pyramids-lazy[parquet]``), while the ones relocated from core still name
# ``pyramids-core[viz]``. Matching only the latter left the one reason that carries no "not
# installed" — ``requires_dask_dataframe`` — invisible to the gate, so a mis-routed dask.dataframe
# test skipped silently in the very job that installs pyarrow. ``tests/test_skip_guard.py`` pins
# every reason ``_marks.py`` emits against this pattern so the two cannot drift apart again.
DEPENDENCY_SKIP_REASON = re.compile(
    r"not installed|could not import|pyramids-[a-z]+\[", re.IGNORECASE
)


def skip_reason(report) -> str:
    """Return the human reason of a skip ``report``.

    A skip's ``longrepr`` is a ``(path, lineno, "Skipped: <reason>")`` tuple; anything else is
    stringified so matching still has something to work with.
    """
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return longrepr[2]
    return str(longrepr)


def is_unexpected_dependency_skip(report) -> bool:
    """Return True iff ``report`` is a genuine skip for a missing optional dependency.

    An expected failure (``xfail``) also reports ``skipped=True`` but carries ``wasxfail`` and is
    *not* a dependency-skip — it ran, it is visible as xfailed, and it must never trip the strict
    gate (a strict ``xfail`` whose reason mentions a dependency, like the OGR Parquet driver test,
    would otherwise be mis-counted). Such reports are excluded before any reason matching.
    """
    if not getattr(report, "skipped", False):
        return False
    if getattr(report, "wasxfail", None) is not None:
        return False
    return bool(DEPENDENCY_SKIP_REASON.search(skip_reason(report) or ""))
