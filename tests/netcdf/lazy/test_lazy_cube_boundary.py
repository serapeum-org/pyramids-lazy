"""Lazy cube boundary and argument validation — the non-composing half of `LazyNetCDF` (#1237).

Companion to `test_lazy_reduce_composition.py`, which covers the ops that *do* compose over dask.
This module covers the two paths that one leaves out:

- **the v1 lazy/eager boundary** — a multi-variable container cannot compose at the cube level, so
  every array-native op materialises the cube, warns once and runs eagerly;
- **argument validation** — the checks each op runs *before* it looks at the records, so a bad
  `boundary=` / `label=` / `decimals=` / `method=` is refused without touching the store.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from pyramids.netcdf import NetCDF

from pyramids_lazy._lazy_cube import LazyNetCDF
from tests._marks import requires_dask

pytestmark = pytest.mark.netcdf_lazy

CF = "tests/data/netcdf/cf__5v__1d4-4d1__y-asc.nc"  # temperature: (time=4, pressure_level=3, lat, lon)
# A rectilinear container with five gridded variables (area, msk_rgn, pr, tas, ua) — `time` is
# length one, so a window/difference op has to use `ua`'s `plev` (length 17) instead.
MULTIVAR = "tests/data/netcdf/cf__12v__1d4-2d5-3d2-4d1__y-asc.nc"


def _variable() -> NetCDF:
    """The on-disk `temperature` variable, band dims `(time, pressure_level)`."""
    return NetCDF.read_file(CF).get_variable("temperature")


def _container() -> LazyNetCDF:
    """A lazy view over the multi-variable container — several records, so nothing composes."""
    return NetCDF.read_file(MULTIVAR).chunk("auto")


class TestContainerBoundary:
    """A multi-variable container materialises at the boundary and runs the op eagerly."""

    @requires_dask
    @pytest.mark.parametrize(
        "run",
        [
            lambda lazy: lazy.coarsen("plev", 2, boundary="trim"),
            lambda lazy: lazy.rolling("time", 2),
            lambda lazy: lazy.diff("plev"),
            lambda lazy: lazy.cumsum("time"),
            lambda lazy: lazy.shift("time"),
            lambda lazy: lazy.rank("time"),
            lambda lazy: lazy.interpolate_na("time"),
            lambda lazy: lazy.interp("linear", plev=[100000.0, 92500.0]),
            lambda lazy: lazy.pad(time=1),
            lambda lazy: lazy.squeeze(),
            lambda lazy: lazy.isel(time=0),
            lambda lazy: lazy.sel(time=0, method="nearest"),
            lambda lazy: lazy.argmin("time"),
            lambda lazy: lazy.argmax("time"),
            lambda lazy: lazy.idxmin("time"),
            lambda lazy: lazy.idxmax("time"),
        ],
        ids=[
            "coarsen",
            "rolling",
            "diff",
            "cumsum",
            "shift",
            "rank",
            "interpolate_na",
            "interp",
            "pad",
            "squeeze",
            "isel",
            "sel",
            "argmin",
            "argmax",
            "idxmin",
            "idxmax",
        ],
    )
    def test_op_on_a_container_warns_and_returns_an_eager_cube(self, run):
        """Each array-native op on a container warns once and returns an eager `NetCDF`."""
        with pytest.warns(UserWarning, match="materialis"):
            result = run(_container())
        assert isinstance(result, NetCDF), (
            f"a container op materialises to an eager cube, got {type(result).__name__}"
        )

    @requires_dask
    @pytest.mark.parametrize(
        "run",
        [
            lambda lazy: lazy.clip(0.0, 1.0),
            lambda lazy: lazy.fillna(0.0),
            lambda lazy: lazy.round(1),
            lambda lazy: lazy * 2.0,
            lambda lazy: lazy + 1.0,
            lambda lazy: -lazy,
            lambda lazy: abs(lazy),
        ],
        ids=["clip", "fillna", "round", "mul", "add", "neg", "abs"],
    )
    def test_cellwise_op_on_a_container_warns_then_the_eager_cube_refuses(self, run):
        """A cell-wise op crosses the boundary — warning first, then the eager container's refusal."""
        lazy = _container()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(ValueError):
                run(lazy)
        assert [w for w in caught if "materialis" in str(w.message)], (
            "the boundary warning must fire before the eager cube refuses the op"
        )

    @requires_dask
    def test_the_boundary_warning_fires_once_across_different_ops(self):
        """The once-flag is per cube, not per op: a second boundary op on it stays silent."""
        lazy = _container()
        with pytest.warns(UserWarning, match="materialis"):
            lazy.cumsum("time")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            lazy.shift("time")
        assert not [w for w in caught if "materialis" in str(w.message)], (
            "a second boundary op on the same cube must not warn again"
        )


class TestSingleVariableBoundary:
    """A pinned variable still materialises for the cases this v1 cannot compose per block."""

    @requires_dask
    def test_an_integer_band_rounded_to_tens_materialises(self):
        """`round(-1)` on an integer band may widen based on the data, so it goes eager."""
        lazy = NetCDF.read_file(MULTIVAR).get_variable("msk_rgn").chunk("auto")
        with pytest.warns(UserWarning, match="materialis"):
            result = lazy.round(-1)
        assert isinstance(result, NetCDF), (
            f"a data-dependent widening round materialises, got {type(result).__name__}"
        )

    @requires_dask
    def test_a_raster_operand_materialises_and_matches_the_eager_product(self):
        """A non-scalar operand needs grid alignment, so `lazy * raster` goes eager, bit-for-bit."""
        var = _variable()
        with pytest.warns(UserWarning, match="materialis"):
            result = var.chunk("auto") * _variable()
        np.testing.assert_allclose(
            np.asarray(result.read_array()),
            np.asarray((var * _variable()).read_array()),
            equal_nan=True,
        )

    @requires_dask
    def test_interp_without_any_coordinates_materialises(self):
        """`interp()` with no `dim=targets` has nothing to compose, so it crosses the boundary."""
        lazy = _variable().chunk("auto")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(ValueError, match="at least one dimension"):
                lazy.interp("linear")
        assert [w for w in caught if "materialis" in str(w.message)], (
            "a coordinate-less interp must warn at the boundary before the eager refusal"
        )


class TestArgumentValidation:
    """Each op validates its arguments before it looks at the records or touches the store."""

    @requires_dask
    @pytest.mark.parametrize(
        "run, error, message",
        [
            (
                lambda lazy: lazy.coarsen("time", 2, boundary="nope"),
                ValueError,
                "boundary must be one of",
            ),
            (
                lambda lazy: lazy.diff("time", label="middle"),
                ValueError,
                "label must be one of",
            ),
            (
                lambda lazy: lazy.interp("linear", nope=[1.0]),
                ValueError,
                "not a band dimension",
            ),
            (
                lambda lazy: lazy.isel(time=[0]).interp("linear", time=[0.0]),
                ValueError,
                "needs at least 2 source steps",
            ),
            (lambda lazy: lazy.round(1.5), TypeError, "integer number of decimals"),
            (lambda lazy: lazy.round(True), TypeError, "integer number of decimals"),
            (
                lambda lazy: lazy.squeeze("time"),
                ValueError,
                "drops a dimension of length one",
            ),
            (
                lambda lazy: lazy.isel(),
                ValueError,
                "requires at least one keyword argument",
            ),
            (
                lambda lazy: lazy.sel(),
                ValueError,
                "requires at least one keyword argument",
            ),
            (
                lambda lazy: lazy.sel(method="bogus", time=0),
                ValueError,
                "must be None .exact. or 'nearest'",
            ),
            (
                lambda lazy: lazy.sel(tolerance=1.0, time=0),
                ValueError,
                "only meaningful with method='nearest'",
            ),
        ],
        ids=[
            "coarsen-boundary",
            "diff-label",
            "interp-unknown-dim",
            "interp-too-few-steps",
            "round-float",
            "round-bool",
            "squeeze-long-dim",
            "isel-no-indexers",
            "sel-no-selectors",
            "sel-bad-method",
            "sel-tolerance-without-nearest",
        ],
    )
    def test_invalid_argument_is_refused(self, run, error, message):
        """A bad argument raises the documented error without materialising the cube."""
        lazy = _variable().chunk("auto")
        with pytest.raises(error, match=message):
            run(lazy)
