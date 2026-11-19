"""Unit tests for the time-axis reduction builders in ``pyramids_lazy._reduce``."""

import pytest

from pyramids_lazy._reduce import resolve_dask_op


class TestResolveDaskOp:
    """The op-name guard in the dask reduction resolver."""

    def test_rejects_unknown_op(self):
        """``resolve_dask_op`` raises ``ValueError`` for an op outside the supported set.

        Test scenario:
            A reduction name the resolver does not know is rejected before any dask work, naming
            the supported set.
        """
        with pytest.raises(ValueError, match="Unsupported reduction"):
            resolve_dask_op("bogus", skipna=True)
