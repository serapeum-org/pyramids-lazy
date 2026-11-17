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
# The distribution part is a pattern rather than a literal. Every extra-reference reason in this
# suite names THIS package (``pyramids-lazy[parquet]``); the pattern also accepts a bare
# ``pyramids[`` and any ``pyramids-<name>[`` / ``pyramids_<name>[``, so a reason naming core's
# extras keeps matching if one ever lands here — none does today. Matching only ``pyramids-core[``
# left the one reason that carries no "not installed" — ``requires_dask_dataframe`` — invisible to
# the gate.
#
# The optional group is `[_-]`-anchored on purpose: a plain ``[a-z0-9_-]*`` also swallowed
# ``pyramidsomething[``, which is a different distribution, not an extra of this one.
# ``tests/test_skip_guard.py`` pins both directions.
DEPENDENCY_SKIP_REASON = re.compile(
    r"not installed|could not import|pyramids(?:[_-][a-z0-9_-]+)?\[", re.IGNORECASE
)


def skip_reason(report) -> str | None:
    """Return the human reason of a skip ``report``.

    A skip's ``longrepr`` is a ``(path, lineno, "Skipped: <reason>")`` tuple; anything else is
    stringified so matching still has something to work with.

    Returns:
        The reason text, or ``None`` when the report carries a 3-tuple whose reason slot is
        itself ``None`` — the one shape returned verbatim rather than stringified.
    """
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        # Returned verbatim, so a tuple whose reason slot is None yields None rather than a
        # string. That is the one shape this function does not stringify, and the reason
        # `is_unexpected_dependency_skip` guards its `re.search` call with `or ""`.
        return longrepr[2]
    return str(longrepr)


def is_unexpected_dependency_skip(report) -> bool:
    """Return True iff ``report`` is a genuine skip for a missing optional dependency.

    An expected failure (``xfail``) also reports ``skipped=True`` but carries ``wasxfail`` and is
    *not* a dependency-skip — it ran, it is visible as xfailed, and it must never trip the strict
    gate, so such reports are excluded before any reason matching. This suite currently declares
    no ``xfail`` marks at all (``grep -rn "mark\\.xfail" tests/`` is empty), so the exclusion is a
    safeguard carried over with the module rather than a response to a test living here.

    The ``or ""`` below is load-bearing, not defensive padding: :func:`skip_reason` returns
    ``longrepr[2]`` verbatim, so a 3-tuple whose reason slot is ``None`` yields ``None``, and
    ``re.search(None)`` raises :class:`TypeError`.
    """
    if not getattr(report, "skipped", False):
        return False
    if getattr(report, "wasxfail", None) is not None:
        return False
    return bool(DEPENDENCY_SKIP_REASON.search(skip_reason(report) or ""))
