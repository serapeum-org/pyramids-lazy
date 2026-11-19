"""Lazy (``chunks=``) curvilinear-crop tests relocated from pyramids-core (#1455/#1590).

The curvilinear crop's ``chunks=`` path routes through the pyramids-lazy backend. The eager
curvilinear-crop tests (and the rectilinear guards) stay in pyramids-core; only the dask-materialising
cases live here, run against released core.
"""

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Polygon

from pyramids.feature import FeatureCollection
from pyramids.netcdf import NetCDF
from pyramids.netcdf.engines.selection import _read_curvilinear_window

pytestmark = pytest.mark.lazy

ROMS = "cf__8v__1d3-2d3-3d1-4d1__curv-stag.nc"
RASM = "none__4v__1d1-2d2-3d1__curv.nc"


def _fc(coords):
    return FeatureCollection(gpd.GeoDataFrame(geometry=[Polygon(coords)], crs=4326))


def test_curvilinear_window_eager_matches_lazy_layout(sample):
    """`_read_curvilinear_window` keeps the native `(*band_sizes, rows, cols)` on both paths (M2, #1241)."""
    nc = NetCDF.read_file(sample(ROMS))
    try:
        salt = nc.get_variable("salt")
        assert tuple(salt._band_dim_sizes) == (2, 6), "fixture must be multi-band-dim"
        r1, c1 = min(4, salt.rows), min(6, salt.columns)
        eager = _read_curvilinear_window(salt, 0, r1, 0, c1, None)
        lazy = _read_curvilinear_window(salt, 0, r1, 0, c1, "auto")
        assert eager.shape == (2, 6, r1, c1), (
            f"eager window must keep the band axes, got {eager.shape}"
        )
        assert eager.shape == lazy.shape, f"eager {eager.shape} != lazy {lazy.shape}"
        np.testing.assert_array_equal(eager, lazy)
    finally:
        nc.close()


def test_roms_curvilinear_crop_lazy_matches_eager(sample):
    """``chunks=`` reads the cropped window through the lazy/dask path and matches the eager crop."""
    nc = NetCDF.read_file(sample(ROMS))
    try:
        aoi = [(-91, 28), (-88, 28), (-88, 30.5), (-91, 30.5)]
        eager_var = nc.get_variable("salt").crop(_fc(aoi))
        lazy_var = nc.get_variable("salt").crop(_fc(aoi), chunks="auto")
        eager = np.asarray(eager_var.read_array())
        lazy = np.asarray(lazy_var.read_array())
        assert tuple(eager_var._band_dim_sizes) == tuple(lazy_var._band_dim_sizes), (
            f"band structure differs: {eager_var._band_dim_sizes} vs {lazy_var._band_dim_sizes}"
        )
        assert lazy.shape == eager.shape
        assert np.allclose(lazy, eager, equal_nan=True)
    finally:
        nc.close()


def test_rasm_crop_lazy_matches_eager(sample):
    """rasm curvilinear crop: the ``chunks=`` (lazy) path matches the eager crop."""
    nc = NetCDF.read_file(sample(RASM))
    try:
        aoi = [(200, 40), (300, 40), (300, 70), (200, 70)]
        eager = np.asarray(nc.get_variable("Tair").crop(_fc(aoi)).read_array())
        lazy = np.asarray(
            nc.get_variable("Tair").crop(_fc(aoi), chunks="auto").read_array()
        )
        assert lazy.shape == eager.shape, f"{lazy.shape} vs {eager.shape}"
        assert np.allclose(lazy, eager, equal_nan=True)
    finally:
        nc.close()
