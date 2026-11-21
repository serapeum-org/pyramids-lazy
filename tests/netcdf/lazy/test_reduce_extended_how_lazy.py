"""Chunked (dask) reduce agreement + which-variables-stream, relocated from pyramids-core (#1590).

A file-backed variable reduces through the pyramids-lazy backend and must answer what the in-memory
numpy path answers, and only a variable still reading as its store is streamed. The eager reduce
tests stay in pyramids-core; only the chunked path lives here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from numpy.testing import assert_allclose
from pyramids.netcdf import ExtraDimensions, GeoReference, NetCDF
from pyramids.netcdf.netcdf import Container, Variable

pytestmark = pytest.mark.lazy

GEO = GeoReference(geo=(0.0, 1.0, 0.0, 3.0, 0.0, -1.0), epsg=4326)
NDV = -9999.0
TIMES = [0.0, 6.0, 12.0, 18.0]
NT, NY, NX = 4, 3, 4
ALL_MASKED = (0, 0)
ONCE_MASKED = (1, 2)
ERA5_T2M = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "netcdf"
    / "cf__5v__1d4-3d1__geog__y-desc.nc"
)
GEOS = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "netcdf"
    / "cf__9v__1d7-2d2__geos__y-desc.nc"
)


def _values() -> np.ndarray:
    """The `(time, y, x)` stack every test reduces, gaps included."""
    rng = np.random.default_rng(20260916)
    values = rng.integers(-3, 4, size=(NT, NY, NX)).astype(np.float64)
    values[:, ALL_MASKED[0], ALL_MASKED[1]] = NDV
    values[1, ONCE_MASKED[0], ONCE_MASKED[1]] = NDV
    return values


def _masked() -> np.ndarray:
    """`_values()` with every gap turned into NaN."""
    values = _values()
    return np.where(values == NDV, np.nan, values)


def _container(values: np.ndarray | None = None) -> Container:
    """An in-memory container holding `values` as variable `v` over `time`."""
    return NetCDF.from_array(
        _values() if values is None else values,
        geo_ref=GEO,
        variable_name="v",
        no_data_value=NDV,
        dims=ExtraDimensions(name="time", values=TIMES),
    )


def _integer_container(dtype: str, ndv: int | None) -> tuple[Container, np.ndarray]:
    """An integer-typed `(time, y, x)` container, with the usual two gaps when `ndv` is set."""
    rng = np.random.default_rng(20260916)
    values = rng.integers(0, 3, size=(NT, NY, NX)).astype(dtype)
    if ndv is not None:
        values[:, ALL_MASKED[0], ALL_MASKED[1]] = ndv
        values[1, ONCE_MASKED[0], ONCE_MASKED[1]] = ndv
    container = NetCDF.from_array(
        values,
        geo_ref=GEO,
        variable_name="v",
        no_data_value=ndv,
        dims=ExtraDimensions(name="time", values=TIMES),
    )
    return container, values


def _float_container_without_sentinel(values: np.ndarray) -> Container:
    """An in-memory float container that declares no no-data value at all."""
    return NetCDF.from_array(
        values,
        geo_ref=GEO,
        variable_name="v",
        no_data_value=None,
        dims=ExtraDimensions(name="time", values=TIMES),
    )


def _eager_mean(variable: NetCDF, dim: str) -> np.ndarray:
    """The NaN-aware mean along `dim` of an eager read of `variable`, as the expectation."""
    values = np.asarray(variable.read_array(), dtype=np.float64).reshape(
        *variable._band_dim_sizes, variable.rows, variable.columns
    )
    return np.nanmean(values, axis=list(variable._band_dim_names).index(dim))


class TestTheChunkedPathAgrees:
    """A file-backed variable reduces through dask and must answer what memory answers."""

    @pytest.mark.parametrize(
        ("how", "kwargs"),
        [
            ("median", {}),
            ("prod", {}),
            ("quantile", {"q": 0.25}),
            ("count", {}),
            ("all", {}),
            ("any", {}),
        ],
        ids=["median", "prod", "quantile", "count", "all", "any"],
    )
    def test_file_backed_equals_in_memory(self, tmp_path, how, kwargs):
        """The same stack, written to disk and reopened, reduces to the same cells."""
        path = str(tmp_path / "stack.nc")
        _container().to_file(path)
        on_disk = NetCDF.read_file(path).reduce("time", how, **kwargs).get_variable("v")
        in_memory = _container().reduce("time", how, **kwargs).get_variable("v")
        assert_allclose(on_disk.read_array(), in_memory.read_array())

    @pytest.mark.parametrize("skipna", [True, False])
    @pytest.mark.parametrize(
        ("how", "kwargs"),
        [
            ("count", {}),
            ("all", {}),
            ("any", {}),
            ("median", {}),
            ("quantile", {"q": 0.5}),
        ],
        ids=["count", "all", "any", "median", "quantile"],
    )
    @pytest.mark.parametrize(
        ("dtype", "ndv"), [("int16", -1), ("uint8", 255)], ids=["int16", "uint8"]
    )
    def test_an_integer_band_on_disk_equals_in_memory(
        self, tmp_path, dtype, ndv, how, kwargs, skipna
    ):
        """An integer stack streams through dask and answers the in-memory cells and dtype."""
        container, _ = _integer_container(dtype, ndv)
        path = str(tmp_path / "stack.nc")
        container.to_file(path)
        store = NetCDF.read_file(path)
        lazy = NetCDF._materialize_variable_array(store.get_variable("v"), lazy=True)
        assert hasattr(lazy, "dask"), (
            f"expected a dask array, got {type(lazy).__name__}"
        )
        on_disk = store.reduce("time", how, skipna=skipna, **kwargs).get_variable("v")
        in_memory = container.reduce("time", how, skipna=skipna, **kwargs).get_variable(
            "v"
        )
        assert on_disk.dtype == in_memory.dtype, (on_disk.dtype, in_memory.dtype)
        assert_allclose(on_disk.read_array(), in_memory.read_array())

    def test_a_file_backed_variable_reduces_to_a_named_variable(self, tmp_path):
        """`read_file(...).get_variable("v").reduce(...)` streams and keeps the name `v`."""
        path = str(tmp_path / "stack.nc")
        _container().to_file(path)
        variable = NetCDF.read_file(path).get_variable("v")
        result = variable.reduce("time", "quantile", q=0.25)
        in_memory = _container().get_variable("v").reduce("time", "quantile", q=0.25)
        assert isinstance(result, Variable), type(result).__name__
        assert result._source_var_name == "v", result._source_var_name
        assert_allclose(result.read_array(), in_memory.read_array())

    def test_a_file_without_a_sentinel_keeps_nan_for_an_empty_column(self, tmp_path):
        """A float stack declaring no no-data value reduces its all-NaN column to NaN."""
        path = str(tmp_path / "stack.nc")
        _float_container_without_sentinel(_masked()).to_file(path)
        result = NetCDF.read_file(path).reduce("time", "sum").get_variable("v")
        values = np.asarray(result.read_array())
        assert result.no_data_value[0] is None, result.no_data_value
        assert np.isnan(values[ALL_MASKED]), values[ALL_MASKED]
        assert not np.isnan(values[ONCE_MASKED]), values[ONCE_MASKED]


class TestWhichVariablesStream:
    """Only a variable still reading as its store is streamed; anything derived is read eagerly."""

    def test_a_fresh_variable_streams(self):
        """`get_variable` hands back the store's own view, which the chunked read reproduces."""
        variable = NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m")
        array = NetCDF._materialize_variable_array(variable, lazy=True)
        assert type(array).__module__.startswith("dask"), type(array)

    def test_a_geostationary_variable_streams(self):
        """A geostationary variable is copied into memory while it is built, and still streams."""
        variable = NetCDF.read_file(str(GEOS)).get_variable("CMI")
        array = NetCDF._materialize_variable_array(variable, lazy=True)
        assert type(array).__module__.startswith("dask"), type(array)

    def test_a_view_materialized_as_a_side_effect_still_streams(self):
        """`resample` copies its source's view into memory unchanged; the source still streams."""
        variable = NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m")
        _ = variable.resample(abs(variable.geotransform[1]) * 2)
        array = NetCDF._materialize_variable_array(variable, lazy=True)
        assert type(array).__module__.startswith("dask"), type(array)

    def test_a_variable_changed_in_place_is_read_eagerly(self):
        """An in-place `fill` replaces the raster's values, so the store no longer describes it."""
        variable = NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m")
        variable.fill(1.0, inplace=True)
        array = NetCDF._materialize_variable_array(variable, lazy=True)
        assert isinstance(array, np.ndarray), type(array)
        assert float(np.nanmax(array)) == 1.0

    @pytest.mark.parametrize(
        "derive",
        [
            pytest.param(lambda v: v.isel(valid_time=slice(4, 12)), id="isel"),
            pytest.param(lambda v: v.to_crs(3035), id="to_crs"),
            pytest.param(lambda v: v * 2.0, id="operator"),
        ],
    )
    def test_a_derived_variable_is_read_eagerly(self, derive):
        """A cut, a reprojection and an operator result are not the store's variable."""
        variable = derive(NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m"))
        array = NetCDF._materialize_variable_array(variable, lazy=True)
        assert isinstance(array, np.ndarray), type(array)

    def test_a_reprojection_materialized_later_reduces_its_own_values(self):
        """A `to_crs` result whose view a later `resample` copied still reduces its warped cells."""
        warped = NetCDF.read_file(str(ERA5_T2M)).get_variable("t2m").to_crs(3035)
        _ = warped.resample(abs(warped.geotransform[1]) * 2)
        assert warped._md_view_materialized, "resample should have copied the view"
        array = NetCDF._materialize_variable_array(warped, lazy=True)
        assert isinstance(array, np.ndarray), type(array)
        values = np.asarray(warped.reduce("valid_time", "mean").read_array())
        expected = _eager_mean(warped, "valid_time")
        assert values.shape == expected.shape, (values.shape, expected.shape)
        assert_allclose(values, expected)
