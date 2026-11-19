"""Lazy STAC Zarr-asset loader tests relocated from pyramids-core (#1590).

``load_asset`` on a ``.zarr`` asset reads through pyramids' GeoZarr reader, which builds a lazy
Dataset / DatasetCollection via this backend. The eager STAC-loader tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset, DatasetCollection
from pyramids.stac._loader import load_asset

pytestmark = pytest.mark.lazy


class TestLoadZarrAsset:
    """STAC Zarr assets load via pyramids' GeoZarr reader (FR-9)."""

    def _raster_zarr(self, tmp_path):
        ds = Dataset.from_array(
            np.arange(12, dtype=np.float32).reshape(3, 4),
            geo_ref=GeoReference(top_left_corner=(0.0, 3.0), cell_size=1.0, epsg=4326),
        )
        tif = str(tmp_path / "r.tif")
        ds.to_file(tif)
        store = str(tmp_path / "r.zarr")
        Dataset.read_file(tif).to_zarr(store)
        return store

    def _cube_zarr(self, tmp_path):
        paths = []
        for i in range(2):
            p = str(tmp_path / f"t{i}.tif")
            Dataset.from_array(
                np.full((3, 4), float(i), dtype=np.float32),
                geo_ref=GeoReference(
                    top_left_corner=(0.0, 3.0), cell_size=1.0, epsg=4326
                ),
            ).to_file(p)
            paths.append(p)
        store = str(tmp_path / "c.zarr")
        DatasetCollection.from_files(paths).to_zarr(store)
        return store

    def test_raster_zarr_loads_as_dataset(self, tmp_path):
        """A 3-D raster Zarr asset loads as a Dataset (FR-9).

        Test scenario:
            ``load_asset`` on a single-raster ``.zarr`` returns a ``Dataset``
            with the right EPSG, via pyramids' reader (not GDAL/NetCDF).
        """
        out = load_asset(
            {"href": self._raster_zarr(tmp_path), "type": "application/zarr"}
        )
        assert isinstance(out, Dataset), f"expected Dataset, got {type(out).__name__}"
        assert out.epsg == 4326, f"epsg {out.epsg}"

    def test_cube_zarr_loads_as_collection(self, tmp_path):
        """A 4-D cube Zarr asset loads as a lazy DatasetCollection (FR-9).

        Test scenario:
            ``load_asset`` on a ``(time, band, y, x)`` cube ``.zarr`` returns a
            ``DatasetCollection`` with the right ``time_length``.
        """
        out = load_asset(
            {"href": self._cube_zarr(tmp_path), "type": "application/zarr"}
        )
        assert isinstance(out, DatasetCollection), f"got {type(out).__name__}"
        assert out.time_length == 2, f"time_length {out.time_length}"
