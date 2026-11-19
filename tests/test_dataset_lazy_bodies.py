"""Coverage for the dataset lazy bodies relocated from pyramids-core (#1590).

These modules — ``_raster_read``, ``_collection_cube``, ``_reduce``, ``_deferred``, ``_focal`` — are
reached in production through pyramids-core's ``Dataset`` / ``DatasetCollection`` API (which routes
to this backend). The core suite exercises that path; here they are unit-tested directly so the
backend carries its own coverage, with one integration test for ``read_raster_array_lazy`` (which
needs a real raster handle via a ``CachingFileManager``).
"""

from __future__ import annotations

import builtins

import dask.array as da
import numpy as np
import pytest
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset

from pyramids_lazy import _collection_cube, _deferred, _focal, _raster_read, _reduce

GEO = GeoReference(top_left_corner=(0.0, 4.0), cell_size=1.0, epsg=4326)


def _break_dask_import(monkeypatch):
    """Make ``import dask`` / ``import dask.array`` raise, to hit the branded guards."""
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "dask" or name.startswith("dask."):
            raise ImportError("no dask")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


class TestDelayed:
    """``_deferred.delayed`` wraps an eager callable in dask.delayed."""

    def test_defers_and_computes(self):
        """delayed(fn, *args) returns a Delayed that runs fn on compute()."""
        result = _deferred.delayed(lambda a, b: a + b, 2, 3)
        assert result.compute() == 5, "delayed call must materialise fn(*args)"

    def test_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded pyramids-lazy hint."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _deferred.delayed(len, "ab")


class TestFocalLazy:
    """``_focal.focal_lazy`` tiles a kernel with dask.array.map_overlap."""

    def test_identity_kernel_preserves_values(self):
        """An identity guarded kernel returns the input unchanged, same shape."""
        source = da.from_array(np.full((6, 6), 5.0), chunks=3)
        out = _focal.focal_lazy(source, lambda block: block, depth=1, dtype="float64")
        np.testing.assert_allclose(np.asarray(out.compute()), 5.0)
        assert out.shape == (6, 6), f"shape must be preserved, got {out.shape}"

    def test_wraps_non_lazy_source(self):
        """A plain ndarray source is wrapped with da.from_array before map_overlap."""
        out = _focal.focal_lazy(
            np.ones((4, 4)), lambda block: block * 2.0, depth=1, dtype="float64"
        )
        np.testing.assert_allclose(np.asarray(out.compute()), 2.0)

    def test_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded pyramids-lazy hint."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _focal.focal_lazy(np.ones((4, 4)), lambda b: b, depth=1, dtype="float64")


class TestMapBlocksLazy:
    """``_raster_read.map_blocks_lazy`` applies a kernel block-wise."""

    def test_applies_kernel(self):
        """map_blocks_lazy runs func on each block."""
        source = da.ones((4, 5), chunks=2)
        out = _raster_read.map_blocks_lazy(
            lambda b: b * 3.0, source, dtype="float64", drop_axis=None, new_axis=None
        )
        np.testing.assert_allclose(np.asarray(out.compute()), 3.0)

    def test_drop_axis_reduces_rank(self):
        """A drop_axis kernel lowers the result rank, confirming the kwarg is forwarded."""
        source = da.ones((2, 4), chunks=(1, 4))
        out = _raster_read.map_blocks_lazy(
            lambda b: b.sum(axis=0),
            source,
            dtype="float64",
            drop_axis=0,
            new_axis=None,
        )
        assert out.ndim == 1, f"drop_axis=0 must drop a dimension, got ndim {out.ndim}"

    def test_new_axis_forwarded(self):
        """new_axis is forwarded to dask.map_blocks (the graph builds with an added axis)."""
        out = _raster_read.map_blocks_lazy(
            lambda b: b[np.newaxis],
            da.ones((4,), chunks=2),
            dtype="float64",
            drop_axis=None,
            new_axis=0,
        )
        assert hasattr(out, "chunks"), (
            "map_blocks with new_axis must return a dask array"
        )

    def test_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded pyramids-lazy hint."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _raster_read.map_blocks_lazy(
                lambda b: b,
                np.ones((4, 4)),
                dtype="float64",
                drop_axis=None,
                new_axis=None,
            )


class TestReadRasterArrayLazy:
    """``_raster_read.read_raster_array_lazy`` builds the tiled band read (integration)."""

    def test_read_array_chunks_materialises(self, tmp_path):
        """Dataset.read_array(chunks=) routes here and computes the exact pixels."""
        arr = np.arange(20, dtype="float32").reshape(4, 5)
        ds = Dataset.from_array(arr, geo_ref=GEO, path=str(tmp_path / "r.tif"))
        lazy = ds.read_array(chunks=2)
        assert hasattr(lazy, "chunks"), "read_array(chunks=) must return a dask array"
        np.testing.assert_array_equal(np.asarray(lazy.compute()), arr)

    def test_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded hint before the manager is touched."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _raster_read.read_raster_array_lazy(
                None,
                manager=None,
                chunks=2,
                shape=(4, 5),
                previous_chunks=(4, 5),
                dtype=np.dtype("float32"),
                lock=None,
                band=0,
                single_band=True,
                gdal_env=None,
            )


class TestCollectionCube:
    """``_collection_cube`` stack / from_array / from_zarr primitives."""

    def test_stack_lazy(self):
        """stack_lazy stacks per-timestep arrays along a new leading axis."""
        steps = [da.full((1, 2, 2), float(i), chunks=(1, 2, 2)) for i in range(3)]
        cube = _collection_cube.stack_lazy(steps, axis=0)
        assert cube.shape == (3, 1, 2, 2), f"got {cube.shape}"
        np.testing.assert_array_equal(
            np.asarray(cube.compute())[:, 0, 0, 0], [0.0, 1.0, 2.0]
        )

    def test_from_array_lazy(self):
        """from_array_lazy wraps an in-memory array as a single-chunk lazy array."""
        arr = np.arange(6, dtype="float64").reshape(1, 2, 3)
        lazy = _collection_cube.from_array_lazy(arr, chunks=arr.shape)
        np.testing.assert_array_equal(np.asarray(lazy.compute()), arr)

    def test_read_zarr_cube(self, tmp_path):
        """read_zarr_cube reads the 'data' component of a zarr store lazily."""
        store = str(tmp_path / "cube.zarr")
        arr = np.arange(2 * 1 * 2 * 2, dtype="float64").reshape(2, 1, 2, 2)
        da.from_array(arr, chunks=(1, 1, 2, 2)).to_zarr(store, component="data")
        cube = _collection_cube.read_zarr_cube(store)
        np.testing.assert_array_equal(np.asarray(cube.compute()), arr)

    def test_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded pyramids-lazy hint from the shared guard."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _collection_cube.stack_lazy([np.ones((1, 2, 2))], axis=0)


class TestReduce:
    """``_reduce`` dask reduction dispatch, kernels and the sparse-window gate."""

    @staticmethod
    def _group(values):
        """A lazy (n_t, band=1, 2, 2) group whose per-timestep plane is a constant."""
        stack = np.stack(
            [np.full((1, 2, 2), float(v), dtype="float64") for v in values], axis=0
        )
        return da.from_array(stack, chunks=(1, 1, 2, 2))

    def test_resolve_dask_op(self):
        """resolve_dask_op maps op+skipna onto the dask.array reduction."""
        assert _reduce.resolve_dask_op("mean", skipna=True) is da.nanmean
        assert _reduce.resolve_dask_op("sum", skipna=False) is da.sum

    def test_compute_batches(self):
        """compute evaluates several lazy arrays in one pass."""
        first, second = _reduce.compute(
            da.from_array(np.array([1.0, 2.0])).sum(),
            da.from_array(np.array([3.0, 4.0])).sum(),
        )
        assert (float(first), float(second)) == (3.0, 7.0)

    @pytest.mark.parametrize("op", ["mean", "sum", "min", "max", "std", "var"])
    def test_build_group_reduction_associative(self, op):
        """The six associative ops reduce the time axis to a (band, rows, cols) result."""
        result = _reduce.build_group_reduction(self._group([1.0, 3.0]), op, skipna=True)
        assert np.asarray(result.compute()).shape == (1, 2, 2), f"{op} shape"

    @pytest.mark.parametrize(
        ("op", "q"), [("median", None), ("quantile", 0.5), ("percentile", 50.0)]
    )
    def test_build_group_reduction_holistic(self, op, q):
        """median / quantile / percentile route through the shared reduce_axis."""
        result = _reduce.build_group_reduction(
            self._group([1.0, 2.0, 3.0]), op, skipna=True, q=q
        )
        np.testing.assert_allclose(np.asarray(result.compute()), 2.0)

    def test_build_group_reduction_mode(self):
        """mode picks the most frequent per-pixel value over time."""
        result = _reduce.build_group_reduction(
            self._group([5.0, 5.0, 7.0]), "mode", skipna=True
        )
        np.testing.assert_allclose(np.asarray(result.compute()), 5.0)

    @pytest.mark.parametrize("op", ["trend", "sens_slope"])
    def test_build_group_reduction_trend(self, op):
        """trend / sens_slope fit a per-pixel slope against the day offsets."""
        result = _reduce.build_group_reduction(
            self._group([1.0, 2.0, 3.0]),
            op,
            skipna=True,
            x=np.array([0.0, 1.0, 2.0]),
        )
        np.testing.assert_allclose(np.asarray(result.compute()), 1.0, atol=1e-9)

    @pytest.mark.parametrize("op", ["trend", "sens_slope"])
    def test_build_group_reduction_trend_skipna_false(self, op):
        """skipna=False exercises the all-timesteps-required branch of the slope kernels."""
        result = _reduce.build_group_reduction(
            self._group([1.0, 2.0, 3.0]),
            op,
            skipna=False,
            x=np.array([0.0, 1.0, 2.0]),
        )
        np.testing.assert_allclose(np.asarray(result.compute()), 1.0, atol=1e-9)

    def test_compute_without_dask_raises_branded(self, monkeypatch):
        """compute() with a missing dask raises the branded pyramids-lazy hint."""
        arr = da.from_array(np.array([1.0, 2.0])).sum()
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _reduce.compute(arr)

    def test_min_count_gate_nans_sparse_window(self):
        """min_count NaNs a cell whose window holds too few valid samples."""
        group = da.from_array(
            np.stack([np.full((1, 1, 1), 1.0), np.full((1, 1, 1), np.nan)], axis=0),
            chunks=(1, 1, 1, 1),
        )
        result = _reduce.build_group_reduction(group, "mean", skipna=True, min_count=2)
        assert np.isnan(np.asarray(result.compute())).all(), (
            "sub-threshold cell must be NaN"
        )

    def test_resolve_dask_op_without_dask_raises_branded(self, monkeypatch):
        """A missing dask raises the branded pyramids-lazy hint."""
        _break_dask_import(monkeypatch)
        with pytest.raises(ImportError, match="pyramids-lazy"):
            _reduce.resolve_dask_op("mean", skipna=True)
