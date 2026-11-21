"""`read_array(squeeze=)` eager-vs-lazy shape convergence — the lazy half, relocated from
pyramids-core (#1590). The eager squeeze tests stay in core; these need the dask backend.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pyramids.netcdf import NetCDF

from tests._marks import requires_lazy

MULTIVAR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "netcdf"
    / "cf__12v__1d4-2d5-3d2-4d1__y-asc.nc"
)


class TestReadArraySqueeze:
    """`squeeze` converges the eager and lazy read shapes (lazy assertions)."""

    @requires_lazy
    @pytest.mark.parametrize("name", ["ua", "tas"])
    def test_squeeze_false_eager_equals_lazy(self, name):
        """`squeeze=False` returns the identical dimension-preserving shape eager and lazy."""
        var = NetCDF.read_file(str(MULTIVAR)).get_variable(name)
        eager = var.read_array(squeeze=False)
        lazy = var.read_array(chunks="auto", squeeze=False)
        assert eager.shape == tuple(lazy.shape), (
            f"{name}: {eager.shape} != {tuple(lazy.shape)}"
        )
        np.testing.assert_array_equal(np.asarray(eager), np.asarray(lazy))

    @requires_lazy
    @pytest.mark.parametrize("name", ["ua", "tas"])
    def test_squeeze_true_eager_equals_lazy(self, name):
        """`squeeze=True` returns the identical classic flattened shape eager and lazy."""
        var = NetCDF.read_file(str(MULTIVAR)).get_variable(name)
        eager = var.read_array(squeeze=True)
        lazy = var.read_array(chunks="auto", squeeze=True)
        assert eager.shape == tuple(lazy.shape), (
            f"{name}: {eager.shape} != {tuple(lazy.shape)}"
        )
        np.testing.assert_array_equal(np.asarray(eager), np.asarray(lazy))

    @requires_lazy
    def test_the_default_converges_eager_and_lazy(self):
        """Since #1241 the default returns the identical dimension-preserving shape both ways."""
        ua = NetCDF.read_file(str(MULTIVAR)).get_variable("ua")
        eager = ua.read_array()
        lazy = ua.read_array(chunks="auto")
        assert eager.shape == tuple(lazy.shape), (
            f"default shapes now converge: {eager.shape} != {tuple(lazy.shape)}"
        )
        np.testing.assert_array_equal(np.asarray(eager), np.asarray(lazy))
