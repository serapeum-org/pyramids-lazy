"""Chunked (dask) coarsen agreement, relocated from pyramids-core (#1590 full decouple).

A file-backed variable coarsens through the pyramids-lazy backend and must answer what the in-memory
numpy path answers. The eager coarsen tests stay in pyramids-core; only the chunked path lives here.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from pyramids.netcdf import ExtraDimensions, GeoReference, NetCDF
from pyramids.netcdf.engines._along_dim import _resize_axis
from pyramids.netcdf.netcdf import Variable

pytestmark = pytest.mark.lazy

GEO = GeoReference(geo=(0.0, 1.0, 0.0, 3.0, 0.0, -1.0), epsg=4326)
NDV = -9999.0
TIMES = [0.0, 6.0, 12.0, 18.0]
NT, NY, NX = 4, 3, 4
ALL_MASKED = (0, 0)


def _values() -> np.ndarray:
    """The `(time, y, x)` stack, with one column that is no-data at every step."""
    rng = np.random.default_rng(7)
    values = rng.uniform(-10.0, 10.0, size=(NT, NY, NX))
    values[:, ALL_MASKED[0], ALL_MASKED[1]] = NDV
    values[1, 2, 3] = NDV
    return values


def _container(values: np.ndarray | None = None):
    """An in-memory container holding `values` as variable `v` over `time`."""
    return NetCDF.from_array(
        _values() if values is None else values,
        geo_ref=GEO,
        variable_name="v",
        no_data_value=NDV,
        dims=ExtraDimensions(name="time", values=TIMES),
    )


def _integer_container(dtype: str, ndv: int) -> tuple:
    """An integer-typed container over `time`, with the no-data column `_values()` has."""
    rng = np.random.default_rng(11)
    values = rng.integers(0, 3, size=(NT, NY, NX)).astype(dtype)
    values[:, ALL_MASKED[0], ALL_MASKED[1]] = ndv
    values[1, 2, 3] = ndv
    container = NetCDF.from_array(
        values,
        geo_ref=GEO,
        variable_name="v",
        no_data_value=ndv,
        dims=ExtraDimensions(name="time", values=TIMES),
    )
    return container, values


class TestTheChunkedPathAgrees:
    """A file-backed variable coarsens through dask and must answer what memory answers."""

    @pytest.mark.parametrize(
        ("window", "boundary"),
        [(2, "exact"), (3, "trim"), (3, "pad")],
        ids=["exact", "trim", "pad"],
    )
    def test_file_backed_equals_in_memory(self, tmp_path, window, boundary):
        """The same stack, written and reopened, coarsens to the same cells and labels."""
        path = str(tmp_path / "stack.nc")
        _container().to_file(path)
        on_disk = (
            NetCDF.read_file(path)
            .coarsen("time", window, boundary=boundary)
            .get_variable("v")
        )
        in_memory = (
            _container().coarsen("time", window, boundary=boundary).get_variable("v")
        )
        assert_allclose(on_disk.read_array(), in_memory.read_array())
        assert (
            on_disk._band_dim_values_map["time"]
            == in_memory._band_dim_values_map["time"]
        )

    @pytest.mark.parametrize(
        ("how", "skipna", "kwargs"),
        [
            ("mean", True, {}),
            ("max", False, {}),
            ("count", True, {}),
            ("all", False, {}),
            ("any", True, {}),
            ("quantile", True, {"q": 0.5}),
        ],
        ids=["mean", "max-raw", "count", "all-raw", "any", "quantile"],
    )
    @pytest.mark.parametrize(
        ("dtype", "ndv"), [("int16", -1), ("uint8", 255)], ids=["int16", "uint8"]
    )
    def test_an_integer_band_pads_on_disk_as_in_memory(
        self, tmp_path, dtype, ndv, how, skipna, kwargs
    ):
        """A padded integer stack streams through dask and answers the in-memory cells."""
        container, _ = _integer_container(dtype, ndv)
        path = str(tmp_path / "stack.nc")
        container.to_file(path)
        store = NetCDF.read_file(path)
        lazy = NetCDF._materialize_variable_array(store.get_variable("v"), lazy=True)
        assert hasattr(lazy, "dask"), (
            f"expected a dask array, got {type(lazy).__name__}"
        )
        options = {"boundary": "pad", "how": how, "skipna": skipna, **kwargs}
        on_disk = store.coarsen("time", 3, **options).get_variable("v")
        in_memory = container.coarsen("time", 3, **options).get_variable("v")
        assert on_disk.dtype == in_memory.dtype, (on_disk.dtype, in_memory.dtype)
        assert_allclose(on_disk.read_array(), in_memory.read_array())

    @pytest.mark.parametrize("size", [2, 6], ids=["cut", "pad"])
    def test_resize_keeps_a_dask_array_lazy(self, tmp_path, size):
        """`_resize_axis` on a chunked read returns a dask array holding the numpy answer."""
        path = str(tmp_path / "stack.nc")
        _container().to_file(path)
        variable = NetCDF.read_file(path).get_variable("v")
        lazy = NetCDF._materialize_variable_array(variable, lazy=True)
        eager = NetCDF._materialize_variable_array(variable)
        result = _resize_axis(lazy, 0, size)
        assert hasattr(result, "dask"), (
            f"expected a dask array, got {type(result).__name__}"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            computed = np.asarray(result)
        assert_array_equal(computed, _resize_axis(eager, 0, size))

    def test_a_file_backed_variable_coarsens_to_a_named_variable(self, tmp_path):
        """`read_file(...).get_variable("v").coarsen(...)` streams and keeps the name `v`."""
        path = str(tmp_path / "stack.nc")
        _container().to_file(path)
        result = (
            NetCDF.read_file(path).get_variable("v").coarsen("time", 3, boundary="pad")
        )
        in_memory = _container().get_variable("v").coarsen("time", 3, boundary="pad")
        assert isinstance(result, Variable), type(result).__name__
        assert result._source_var_name == "v", result._source_var_name
        assert_allclose(result.read_array(), in_memory.read_array())
        assert result._band_dim_values_map["time"] == [6.0, 18.0], (
            result._band_dim_values_map
        )
