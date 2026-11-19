"""Lazy overview reads / to_zarr pyramid relocated from pyramids-core (#1590).

A chunked read of an overview level and ``to_zarr`` with overview factors drive the pyramids-lazy
backend, so these live here; the eager overview tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest
from osgeo import gdal, osr
from pyramids.dataset import Dataset
from pyramids.errors import OverviewTargetError

pytestmark = pytest.mark.lazy


def _overviewed_raster(tmp_path, name: str = "ovr_src.tif") -> str:
    """Write a georeferenced 3-band raster and build two overview levels on it."""
    path = str(tmp_path / name)
    raster = gdal.GetDriverByName("GTiff").Create(path, 64, 64, 3, gdal.GDT_Float32)
    raster.SetGeoTransform((10.0, 0.5, 0.0, 80.0, 0.0, -0.5))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    raster.SetProjection(srs.ExportToWkt())
    for index in range(3):
        band = raster.GetRasterBand(index + 1)
        band.SetNoDataValue(-9999.0)
        band.WriteArray(np.full((64, 64), float(index + 1), dtype="float32"))
    raster = None

    handle = gdal.Open(path, gdal.GA_Update)
    handle.BuildOverviews("AVERAGE", [2, 4])
    handle = None
    return path


def _plain_raster(tmp_path, name: str) -> str:
    """Write a georeferenced 2-band raster that carries no overviews at all."""
    path = str(tmp_path / name)
    raster = gdal.GetDriverByName("GTiff").Create(path, 64, 64, 2, gdal.GDT_Float32)
    raster.SetGeoTransform((10.0, 0.5, 0.0, 80.0, 0.0, -0.5))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    raster.SetProjection(srs.ExportToWkt())
    for index in range(2):
        band = raster.GetRasterBand(index + 1)
        band.WriteArray(np.full((64, 64), float(index + 1), dtype="float32"))
    raster = None
    return path


class TestCreateOverviewsPathlessGuard:
    def test_to_zarr_still_writes_a_pyramid_for_a_vrt_with_a_path(
        self, tmp_path, monkeypatch
    ):
        """The pre-flight refuses unusable targets, not the VRT driver."""
        import zarr

        monkeypatch.chdir(tmp_path)
        source = _plain_raster(tmp_path, "zarr_vrt_src.tif")
        vrt_path = tmp_path / "zarr_usable.vrt"
        gdal.Translate(str(vrt_path), source, format="VRT").FlushCache()
        store = tmp_path / "usable.zarr"
        dataset = Dataset.read_file(str(vrt_path))
        try:
            dataset.to_zarr(str(store), overview_factors=[2])
            keys = set(zarr.open_group(str(store), mode="r").array_keys())
            assert {"data", "data_2"} <= keys, (
                f"a path-ful VRT can hold its levels, so the pyramid must be written, got {keys}"
            )
        finally:
            dataset.close()

    def test_to_zarr_with_overview_factors_surfaces_the_refusal(
        self, tmp_path, monkeypatch
    ):
        """`to_zarr(overview_factors=...)` builds levels, so it refuses the same shapes."""
        monkeypatch.chdir(tmp_path)
        source = _overviewed_raster(tmp_path, "zarr_guard_src.tif")
        xml = gdal.Translate("", str(source), format="VRT").GetMetadata("xml:VRT")[0]
        dataset = Dataset.read_file(xml)
        try:
            with pytest.raises(OverviewTargetError, match="nowhere to go"):
                dataset.to_zarr(str(tmp_path / "out.zarr"), overview_factors=[2])
        finally:
            dataset.close()

    def test_to_zarr_refuses_the_target_before_writing_anything(
        self, tmp_path, monkeypatch
    ):
        """The refusal is taken pre-flight, so no half-written store is left behind."""
        monkeypatch.chdir(tmp_path)
        source = _overviewed_raster(tmp_path, "zarr_preflight_src.tif")
        xml = gdal.Translate("", str(source), format="VRT").GetMetadata("xml:VRT")[0]
        store = tmp_path / "preflight.zarr"
        dataset = Dataset.read_file(xml)
        try:
            with pytest.raises(OverviewTargetError, match="nowhere to go") as excinfo:
                dataset.to_zarr(str(store), overview_factors=[2])
            assert "stores no pixels of its own" in str(excinfo.value), (
                f"to_zarr builds levels, so it must carry the building diagnosis, got: {excinfo.value}"
            )
            assert not store.exists(), (
                "the refusal must leave no store behind, found "
                f"{sorted(p.name for p in store.iterdir())}"
            )
        finally:
            dataset.close()

    def test_to_zarr_reports_the_unusable_target_before_the_compute_argument(
        self, tmp_path, monkeypatch
    ):
        """The target the caller cannot fix is reported ahead of the argument they can."""
        monkeypatch.chdir(tmp_path)
        source = _overviewed_raster(tmp_path, "zarr_ordering_src.tif")
        xml = gdal.Translate("", str(source), format="VRT").GetMetadata("xml:VRT")[0]
        store = tmp_path / "ordering.zarr"
        dataset = Dataset.read_file(xml)
        try:
            with pytest.raises(OverviewTargetError, match="nowhere to go"):
                dataset.to_zarr(str(store), overview_factors=[2], compute=False)
            assert not store.exists(), "neither guard may leave a store behind"
        finally:
            dataset.close()


class TestGetOverviewDataset:
    def test_lazy_level_refuses_a_chunked_read(self, tmp_path):
        """A chunked read of the lazy level never hands back full-resolution pixels."""
        dataset = Dataset.read_file(_overviewed_raster(tmp_path, "chunked.tif"))
        overview = None
        try:
            overview = dataset.get_overview_dataset(band=0, overview_index=0)
            lazy = overview.read_array(band=0, chunks=16)
            assert lazy.shape == (32, 32), (
                f"the graph must be shaped from the level, got {lazy.shape}"
            )
            with pytest.raises(RuntimeError, match="No such file or directory"):
                lazy.compute()
        finally:
            if overview is not None:
                overview.close()
            dataset.close()
