"""Lazy CLI from-zarr test relocated from pyramids-core (#1590 full decouple).

`pyramids raster import zarr` reads a store through the pyramids-lazy backend. The eager import
commands and the missing-extra contract tests stay in pyramids-core.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pyramids.cli import main
from pyramids.dataset import Dataset, GeoReference

pytestmark = pytest.mark.lazy


def _band_tif(directory: Path, name: str, value: int, shape=(4, 5)) -> str:
    """Write a small single-band GeoTIFF on a 1-degree WGS84 grid and return its path."""
    path = str(directory / name)
    Dataset.from_array(
        np.full(shape, value, dtype="int16"),
        geo_ref=GeoReference(top_left_corner=(0.0, 10.0), cell_size=1.0, epsg=4326),
        path=path,
    ).close()
    return path


class TestFromZarr:
    """`pyramids raster import zarr` — needs the pyramids-lazy backend."""

    def test_happy_path(self, tmp_path):
        """A pyramids-written Zarr store reads back into an openable raster."""
        store = str(tmp_path / "store.zarr")
        Dataset.read_file(_band_tif(tmp_path, "src.tif", 7)).to_zarr(store)
        out = str(tmp_path / "o.tif")
        rc = main(["raster", "import", "zarr", store, out])
        assert rc == 0
        assert Dataset.read_file(out).band_count == 1
