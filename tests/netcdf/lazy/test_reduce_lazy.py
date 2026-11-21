"""File-backed (dask-streamed) reduce tests, relocated from pyramids-core (#1590).

The eager reduce coverage stays in core; these take the dask streaming path on a file-backed
variable and must match numpy, so they live with the backend.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.netcdf import ExtraDimensions, GeoReference, NetCDF

from tests._marks import requires_lazy

_GEO = (0.0, 1.0, 0.0, 3.0, 0.0, -1.0)


@requires_lazy
class TestReduceStreamingLazyPath:
    """A file-backed reduce takes the dask streaming path and matches numpy across corners."""

    @staticmethod
    def _write_4d(tmp_path):
        """Write a 4-D `(time, level, y, x)` cube to disk; return `(path, array)`."""
        arr = np.arange(4 * 2 * 3 * 5, dtype="float64").reshape(4, 2, 3, 5)
        NetCDF.from_array(
            arr,
            geo_ref=GeoReference(geo=_GEO, epsg=4326),
            variable_name="v",
            dims=ExtraDimensions(dims=[("time", [0, 1, 2, 3]), ("level", [1000, 500])]),
        ).to_file(str(tmp_path / "cube4d.nc"))
        return str(tmp_path / "cube4d.nc"), arr

    def test_materialize_lazy_returns_dask_for_file_backed(self, tmp_path):
        """`_materialize_variable_array(lazy=True)` returns a dask array for a file-backed variable."""
        path, _ = self._write_4d(tmp_path)
        nc = NetCDF.read_file(path)
        try:
            arr = NetCDF._materialize_variable_array(nc.get_variable("v"), lazy=True)
            assert hasattr(arr, "dask"), (
                f"expected a dask array on the streaming path, got {type(arr)}"
            )
        finally:
            nc.close()

    @pytest.mark.parametrize(
        "how, np_func", [("mean", np.mean), ("std", np.std), ("var", np.var)]
    )
    def test_multi_band_dim_collapse_matches_numpy(self, how, np_func, tmp_path):
        """A file-backed `(time, level, y, x)` reduce over time streams and matches numpy."""
        path, arr = self._write_4d(tmp_path)
        nc = NetCDF.read_file(path)
        try:
            out = np.asarray(nc.reduce("time", how).get_variable("v").read_array())
            expected = np_func(arr, axis=0)
            assert np.allclose(out.reshape(expected.shape), expected), (
                f"{how} 4-D streaming collapse mismatch"
            )
        finally:
            nc.close()

    def test_windowed_groupby_matches_numpy(self, tmp_path):
        """A file-backed grouped reduce streams the np.take/np.stack path and matches numpy."""
        path, arr = self._write_4d(tmp_path)
        nc = NetCDF.read_file(path)
        try:
            out = np.asarray(
                nc.reduce("time", "mean", groupby=[0, 0, 1, 1])
                .get_variable("v")
                .read_array()
            )
            expected = np.stack(
                [arr[[0, 1]].mean(axis=0), arr[[2, 3]].mean(axis=0)], axis=0
            )
            assert np.allclose(out.reshape(expected.shape), expected), (
                "grouped 4-D streaming reduce mismatch"
            )
        finally:
            nc.close()
