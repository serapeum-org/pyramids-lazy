"""Lazy band-selection reads, relocated from pyramids-core (#1590).

The eager band-selection coverage and the "a cut result refuses chunks=" guards (which raise
before the backend) stay in core; these do a real chunked read, so they live with the backend.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pyramids.netcdf import NetCDF
from tests._marks import requires_lazy

CF_STORE = Path(__file__).parents[2] / "data" / "netcdf" / "cf__5v__1d4-4d1__y-asc.nc"


class TestSqueeze:
    """`squeeze()` keeps the lazy read for a no-op (lazy assertion)."""

    @requires_lazy
    def test_a_no_op_squeeze_keeps_the_lazy_read(self):
        """Dropping nothing must not cost a copy of the cube, nor its lazy read."""
        cube = NetCDF.read_file(str(CF_STORE))["temperature"]
        squeezed = cube.squeeze()
        assert squeezed._band_dim_names == cube._band_dim_names
        assert squeezed._band_dim_sizes == cube._band_dim_sizes
        assert np.asarray(squeezed.read_array(chunks="auto")).shape == (4, 3, 5, 6)
        assert np.array_equal(
            np.asarray(squeezed.read_array()), np.asarray(cube.read_array())
        )


class TestALazyReadOfACutVariable:
    """A store variable still reads lazily (the cut-variable refusal tests stay in core)."""

    @staticmethod
    def _cube() -> NetCDF:
        """The CF store's `temperature`, a `(time=4, pressure_level=3)` cube.

        Returns:
            NetCDF: The variable.
        """
        return NetCDF.read_file(str(CF_STORE))["temperature"]

    @requires_lazy
    def test_the_store_variable_itself_still_reads_lazily(self):
        """The guard must not touch a variable that is still its store's."""
        lazy = np.asarray(self._cube().read_array(chunks="auto"))
        assert lazy.shape == (4, 3, 5, 6), f"whole variable read back as {lazy.shape}"
