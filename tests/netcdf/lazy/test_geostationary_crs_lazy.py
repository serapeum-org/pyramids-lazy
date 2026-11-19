"""Geostationary `to_zarr` (backend write), relocated from pyramids-core (#1590 full decouple).

`Variable.to_zarr` writes through the pyramids-lazy backend; the eager geostationary-CRS tests stay
in pyramids-core.
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pytest
from osgeo import gdal
from pyramids.netcdf import NetCDF

pytestmark = pytest.mark.lazy

_GEOS_LON_0 = -75.0


def _attr_str(arr, name, value):
    """Write a scalar string attribute onto an MDIM array."""
    arr.CreateAttribute(name, [], gdal.ExtendedDataType.CreateString()).Write(value)


def _attr_f64(arr, name, value):
    """Write a scalar float64 attribute onto an MDIM array."""
    dt = gdal.ExtendedDataType.Create(gdal.GDT_Float64)
    arr.CreateAttribute(name, [], dt).Write(float(value))


def _write_geostationary_mdim(
    path: str, ny: int = 120, nx: int = 150, n_time: int | None = None
) -> None:
    """Write a minimal GOES-style geostationary NetCDF via the GDAL MDIM API."""
    ds = gdal.GetDriverByName("netCDF").CreateMultiDimensional(path)
    rg = ds.GetRootGroup()
    dy = rg.CreateDimension("y", "", "", ny)
    dx = rg.CreateDimension("x", "", "", nx)
    i16 = gdal.ExtendedDataType.Create(gdal.GDT_Int16)
    if n_time is not None:
        dt = rg.CreateDimension("time", "", "", n_time)
        tv = rg.CreateMDArray(
            "time", [dt], gdal.ExtendedDataType.Create(gdal.GDT_Int32)
        )
        tv.Write(np.arange(n_time, dtype=np.int32))
        _attr_str(tv, "standard_name", "time")
        _attr_str(tv, "units", "days since 2024-01-01")

    x = rg.CreateMDArray("x", [dx], i16)
    x.Write(np.arange(nx, dtype=np.int16))
    _attr_str(x, "units", "rad")
    _attr_str(x, "axis", "X")
    _attr_str(x, "standard_name", "projection_x_coordinate")
    _attr_f64(x, "scale_factor", 2.8e-05)
    _attr_f64(x, "add_offset", -0.045)

    y = rg.CreateMDArray("y", [dy], i16)
    y.Write(np.arange(ny, dtype=np.int16))
    _attr_str(y, "units", "rad")
    _attr_str(y, "axis", "Y")
    _attr_str(y, "standard_name", "projection_y_coordinate")
    _attr_f64(y, "scale_factor", -2.8e-05)
    _attr_f64(y, "add_offset", 0.065)

    gp = rg.CreateMDArray(
        "goes_imager_projection", [], gdal.ExtendedDataType.Create(gdal.GDT_Int32)
    )
    gp.Write(np.array(0, dtype=np.int32))
    _attr_str(gp, "grid_mapping_name", "geostationary")
    _attr_f64(gp, "perspective_point_height", 35786023.0)
    _attr_f64(gp, "semi_major_axis", 6378137.0)
    _attr_f64(gp, "semi_minor_axis", 6356752.31414)
    _attr_f64(gp, "inverse_flattening", 298.2572221)
    _attr_f64(gp, "latitude_of_projection_origin", 0.0)
    _attr_f64(gp, "longitude_of_projection_origin", _GEOS_LON_0)
    _attr_str(gp, "sweep_angle_axis", "x")

    cmi_dims = [dt, dy, dx] if n_time is not None else [dy, dx]
    cmi_shape = (n_time, ny, nx) if n_time is not None else (ny, nx)
    cmi = rg.CreateMDArray(
        "CMI_C02", cmi_dims, gdal.ExtendedDataType.Create(gdal.GDT_UInt16)
    )
    cmi.Write(np.zeros(cmi_shape, dtype=np.uint16))
    _attr_str(cmi, "grid_mapping", "goes_imager_projection")
    _attr_str(cmi, "coordinates", "y x")
    _attr_f64(cmi, "scale_factor", 0.00031746)
    _attr_f64(cmi, "add_offset", 0.0)


class TestGeostationaryContainerOps:
    def test_to_zarr_writes_geostationary_wkt(self, tmp_path):
        """`to_zarr` writes without an `int(None)` crash and stores the geostationary WKT."""
        path = str(tmp_path / "geos.nc")
        _write_geostationary_mdim(path)
        var = NetCDF.read_file(path).get_variable("CMI_C02")
        store = str(tmp_path / "geos.zarr")
        var.to_zarr(store)
        assert os.path.exists(store)
        # the geostationary CRS is preserved in the store's `spatial_ref` metadata
        meta = glob.glob(
            os.path.join(store, "**", "*.json"), recursive=True
        ) + glob.glob(os.path.join(store, "**", ".z*"), recursive=True)
        assert any(
            "Geostationary_Satellite" in open(f, encoding="utf-8").read() for f in meta
        )
