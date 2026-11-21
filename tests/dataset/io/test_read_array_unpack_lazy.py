"""Lazy scale/offset unpacking relocated from pyramids-core (#1590).

A lazy ``chunks=`` read must unpack to the same physical values as the eager read. This drives the
pyramids-lazy backend, so it lives here; the eager unpack tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset

pytestmark = pytest.mark.lazy


def _make(array: np.ndarray, scale=None, offset=None, no_data=None) -> Dataset:
    """Build an in-memory Dataset, optionally with per-band scale/offset/no-data."""
    ds = Dataset.from_array(
        array,
        geo_ref=GeoReference(top_left_corner=(0, 0), cell_size=1.0, epsg=4326),
    )
    if scale is not None:
        ds.scale = scale
    if offset is not None:
        ds.offset = offset
    if no_data is not None:
        ds.no_data_value = no_data
    return ds


@pytest.fixture
def packed_multi() -> Dataset:
    """A 3-band int16 raster with per-band differing scale/offset."""
    arr = np.arange(3 * 4).reshape(3, 2, 2).astype("int16")
    return _make(arr, scale=[0.1, 2.0, 1.0], offset=[0.0, -1.0, 0.0])


class TestReadArrayUnpack:
    def test_lazy_matches_eager(self, packed_multi, tmp_path):
        """A lazy (chunks) read computes to the same values as the eager one."""
        # The chunked path reopens the source by path, so back it with a real file.
        path = tmp_path / "packed.tif"
        packed_multi.to_file(str(path))
        ds = Dataset.read_file(str(path))
        lazy = ds.read_array(chunks="auto")
        assert hasattr(lazy, "compute"), "chunks= must return a lazy array"
        np.testing.assert_allclose(lazy.compute(), ds.read_array())
