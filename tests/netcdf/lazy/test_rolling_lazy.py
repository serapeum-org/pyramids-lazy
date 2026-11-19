"""File-backed (dask-streamed) rolling test, relocated from pyramids-core (#1590).

The eager rolling coverage stays in core; this streams a file-backed variable through dask and
must match the eager read, so it lives with the backend.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.testing import assert_allclose
from pyramids.netcdf import NetCDF

from tests._marks import requires_lazy

ERA5_T2M = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "netcdf"
    / "cf__5v__1d4-3d1__geog__y-desc.nc"
)


@requires_lazy
class TestRollingStreamed:
    """A file-backed variable streamed through dask rolls the same cells as an eager read."""

    def test_streamed_equals_eager(self):
        """ERA5 `t2m` rolled as read from its store equals the same variable read whole."""
        variable = NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m")
        assert NetCDF._reads_as_its_store(variable)
        streamed = variable.rolling("valid_time", 3, center=True, min_periods=2)
        eager_source = variable.isel(valid_time=slice(None))
        assert not NetCDF._reads_as_its_store(eager_source)
        eager = eager_source.rolling("valid_time", 3, center=True, min_periods=2)
        assert_allclose(
            np.asarray(streamed.read_array()),
            np.asarray(eager.read_array()),
            equal_nan=True,
        )
