"""Chunked (dask-backed) NetCDF interop tests relocated from pyramids-core (#1590 decouple).

``NetCDF.to_xarray(chunks=)`` / ``from_xarray`` of a dask-backed variable route through this backend,
so these need dask AND xarray — hosted here against released core. The eager interop tests (plain
``to_xarray``/``from_xarray``, no chunks) stay in pyramids-core.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

xr = pytest.importorskip("xarray")

from pyramids.netcdf.engines.interop import _write_md_array_streamed
from pyramids.netcdf.netcdf import NetCDF

from tests.netcdf.conftest import make_3d_nc

pytestmark = pytest.mark.lazy

GEOG_3D_NC = "tests/data/netcdf/cf__5v__1d4-3d1__geog__y-desc.nc"
CF_PATH = "tests/data/netcdf/cf__5v__1d4-4d1__y-asc.nc"
GROUPED = "tests/data/netcdf/none__35v__1d35__groups-nc4.nc"


def _make_3d_nc(rows=4, cols=6, bands=3, variable_name="temperature"):
    """Create a 3D in-memory NetCDF with sequential data (delegates to the shared helper)."""
    return make_3d_nc(
        rows=rows,
        cols=cols,
        bands=bands,
        variable_name=variable_name,
        geo=(10.0, 1.0, 0, 44.0, 0, -1.0),
        arr_type="sequential",
        extra_dim_name="time",
        extra_dim_values=[0, 6, 12],
    )


class TestToXarrayLazy:
    """Chunked interop export builds dask-backed variables in native order (ARC-48)."""

    def test_numeric_var_is_dask_backed(self):
        """A numeric data variable is dask-backed when chunks= is given."""
        nc = NetCDF.read_file(GEOG_3D_NC)
        try:
            lazy = nc.to_xarray(chunks="auto")
            assert hasattr(lazy["t2m"].data, "dask"), (
                "numeric var should be dask-backed"
            )
        finally:
            nc.close()

    def test_lazy_values_match_eager(self):
        """Computed lazy values equal the eager interop values (raw order kept)."""
        nc = NetCDF.read_file(GEOG_3D_NC)
        try:
            eager = nc.to_xarray()
            lazy = nc.to_xarray(chunks="auto")
            assert_allclose(
                np.asarray(lazy["t2m"].compute().values),
                np.asarray(eager["t2m"].values),
                equal_nan=True,
            )
        finally:
            nc.close()

    def test_string_var_falls_back_to_eager(self):
        """A non-chunkable string variable is read eagerly, not as dask, and still matches."""
        nc = NetCDF.read_file(GEOG_3D_NC)
        try:
            eager = nc.to_xarray()
            lazy = nc.to_xarray(chunks="auto")
            assert not hasattr(lazy["expver"].data, "dask"), (
                "string var must fall back to eager"
            )
            assert_array_equal(
                np.asarray(lazy["expver"].values), np.asarray(eager["expver"].values)
            )
        finally:
            nc.close()

    def test_in_memory_container_ignores_chunks(self):
        """An in-memory container has no file to reopen, so chunks= falls back to eager."""
        nc = _make_3d_nc(variable_name="temperature")
        ds = nc.to_xarray(chunks="auto")
        assert not hasattr(ds["temperature"].data, "dask"), (
            "in-memory container should ignore chunks and stay eager"
        )

    def test_lazy_read_threads_gdal_env(self, monkeypatch):
        """The container's `_gdal_env` is carried into the lazy read so a signed remote store
        re-opens authenticated (#839)."""
        from pyramids.netcdf.engines import interop as interop_mod

        captured = {}

        def _spy(path, variable_name, chunks, lock=None, orient=True, gdal_env=None):
            captured["gdal_env"] = gdal_env
            captured["orient"] = orient
            return object()  # stand-in lazy array; _lazy_var_data returns it unchanged

        lazy_mod = pytest.importorskip("pyramids_lazy._lazy", exc_type=ImportError)
        if not hasattr(lazy_mod, "build_lazy_array"):
            pytest.skip(
                "pyramids_lazy._lazy.build_lazy_array not present in this backend"
            )
        monkeypatch.setattr(lazy_mod, "build_lazy_array", _spy)
        nc = NetCDF.read_file(GEOG_3D_NC)
        try:
            nc.attach_gdal_env({"AWS_REQUEST_PAYER": "requester"})
            interop_mod._lazy_var_data(nc, "t2m", "auto", None)
        finally:
            nc.close()
        assert captured["gdal_env"] == {"AWS_REQUEST_PAYER": "requester"}, (
            f"container gdal_env must be threaded into the lazy read, got {captured}"
        )
        assert captured["orient"] is False, (
            "lazy interop read must be raw (orient=False)"
        )


class _RecordingMDArray:
    """A stand-in ``gdal.MDArray`` that records each hyperslab write into a real buffer."""

    def __init__(self, shape, dtype):
        """Allocate the destination buffer and a write log."""
        self.buf = np.zeros(shape, dtype=dtype)
        self.writes = []

    def Write(self, block, array_start_idx=None, count=None):
        """Record and apply one write; a whole write has ``array_start_idx is None``."""
        self.writes.append((array_start_idx, count))
        if array_start_idx is None:
            self.buf[...] = np.asarray(block)
        else:
            sl = tuple(slice(s, s + c) for s, c in zip(array_start_idx, count))
            self.buf[sl] = np.asarray(block)


class TestFromXarrayStreaming:
    """from_xarray streams a dask-backed variable block by block, not all at once (ARC-48)."""

    def test_dask_input_round_trips_and_matches_eager(self):
        """A dask-backed input reproduces the data and agrees with the equivalent numpy input."""
        import dask.array as da

        base = np.arange(4 * 3 * 2, dtype="float32").reshape(4, 3, 2)
        coords = {"time": np.arange(4), "lat": [10.0, 11.0, 12.0], "lon": [20.0, 21.0]}
        lazy = xr.Dataset(
            {"t": (("time", "lat", "lon"), da.from_array(base, chunks=(2, 2, 1)))},
            coords=coords,
        )
        eager = xr.Dataset({"t": (("time", "lat", "lon"), base)}, coords=coords)
        nc_lazy = NetCDF.from_xarray(lazy)
        nc_eager = NetCDF.from_xarray(eager)
        try:
            got = np.asarray(nc_lazy.to_xarray()["t"].values)
            assert np.array_equal(got, base), (
                "streamed dask input must reproduce the data"
            )
            assert np.array_equal(got, np.asarray(nc_eager.to_xarray()["t"].values)), (
                "streamed and eager from_xarray must agree"
            )
        finally:
            nc_lazy.close()
            nc_eager.close()

    def test_dask_written_block_by_block(self):
        """A dask array is written one hyperslab per block, and the blocks reconstruct the array."""
        import dask.array as da

        base = np.arange(24, dtype="float32").reshape(4, 3, 2)
        arr = da.from_array(base, chunks=(2, 3, 1))  # 2 * 1 * 2 = 4 blocks
        rec = _RecordingMDArray((4, 3, 2), "float32")
        _write_md_array_streamed(rec, arr)
        assert len(rec.writes) == 4, (
            f"expected one write per block, got {len(rec.writes)}"
        )
        assert all(s is not None and c is not None for s, c in rec.writes), (
            "each streamed write must be a bounded hyperslab (array_start_idx + count)"
        )
        assert np.array_equal(rec.buf, base), (
            "the block writes must reconstruct the full array"
        )


class TestTheFacadeSignatureChunked:
    """The positional `chunks=` form routes through the backend (relocated from core interop)."""

    def test_chunks_is_still_positional(self):
        """`to_xarray("auto")` keeps working — the spelled-out signature is compatible."""
        exported = NetCDF.read_file(CF_PATH).to_xarray("auto")
        assert exported["temperature"].chunks is not None


class TestTheLazyExportResolvesTheSameNames:
    """`to_xarray()`'s two arms must agree about which names exist (grouped store)."""

    def test_a_chunked_export_of_a_grouped_store_returns_a_dataset(self):
        """The lazy arm must resolve a sub-group name, not raise on it."""
        dataset = NetCDF.read_file(str(GROUPED))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exported = dataset.to_xarray(chunks="auto")
        assert len(exported.data_vars) > 5

    def test_the_two_arms_export_the_same_variables(self):
        """One method, one answer -- `chunks=` is a memory strategy, not a filter."""
        dataset = NetCDF.read_file(str(GROUPED))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            eager = dataset.to_xarray()
            lazy = dataset.to_xarray(chunks="auto")
        assert sorted(lazy.data_vars) == sorted(eager.data_vars)
