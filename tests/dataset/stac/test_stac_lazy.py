"""Lazy STAC-cube test relocated from pyramids-core (#1590).

``DatasetCollection.from_stac(...).data`` builds the lazy cube through this backend's
``stack_lazy`` / ``read_raster_array_lazy``. The eager ``from_stac`` tests stay in pyramids-core;
only the dask-materialising case lives here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset, DatasetCollection

pytestmark = pytest.mark.lazy


@pytest.fixture
def three_tifs(tmp_path):
    """Three small GeoTIFFs, each with a different fill value."""
    paths = []
    for i in range(3):
        arr = np.full((3, 4), float(i + 1), dtype=np.float32)
        ds = Dataset.from_array(
            arr,
            geo_ref=GeoReference(top_left_corner=(0.0, 3.0), cell_size=1.0, epsg=4326),
        )
        p = str(tmp_path / f"tile_{i}.tif")
        ds.to_file(p)
        paths.append(p)
    return paths


@pytest.fixture
def stac_items(three_tifs):
    """Wrap three local GeoTIFFs as raw STAC JSON dict items.

    This fixture deliberately uses plain dicts rather than
    :class:`pystac.Item` objects — the pyramids ``from_stac`` loader
    is fully duck-typed and must work for callers who hand-build JSON
    from an HTTP response without touching pystac.
    """
    return [
        {
            "id": f"item-{i}",
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "assets": {"data": {"href": path}},
        }
        for i, path in enumerate(three_tifs)
    ]


class TestFromStac:
    def test_lazy_data_computes(self, stac_items):
        collection = DatasetCollection.from_stac(stac_items, asset="data")
        arr = collection.data.compute()
        assert arr.shape[0] == 3
        for i in range(3):
            assert (arr[i] == i + 1).all()


class TestCollectionGdalEnvLazyReads:
    """H4: the persisted gdal_env is installed around the tiled lazy `data` read."""

    def test_path_b_lazy_data_installs_env(self, three_tifs, monkeypatch):
        """Path B: the tiled `data` graph installs the collection's env around each open."""
        from osgeo import gdal

        from pyramids.dataset import collection as coll_mod

        captured: dict[str, str | None] = {}
        real_open = coll_mod.gdal_raster_open

        def spy(*args, **kwargs):
            captured["v"] = gdal.GetConfigOption("PYRAMIDS_TEST_KEY")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(coll_mod, "gdal_raster_open", spy)
        coll = coll_mod.DatasetCollection.from_files(
            three_tifs, gdal_env={"PYRAMIDS_TEST_KEY": "on"}
        )
        coll.data.compute()
        assert captured["v"] == "on", f"env not active during Path B open: {captured}"
