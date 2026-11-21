"""Thread-safe lazy-read tests relocated from pyramids-core (#1590).

``read_array(chunks=, threadsafe=True)`` routes to this backend's ``read_raster_array_lazy``. The
eager thread-safe read tests and the MEM-rejection guard (which raises before the backend) stay in
pyramids-core; only the dask-materialising cases live here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset

pytestmark = pytest.mark.lazy


@pytest.fixture(scope="function")
def tiled_raster(tmp_path):
    """A 256x256 float32 ramp written as a 64x64-tiled GeoTIFF.

    Returns:
        tuple[Dataset, np.ndarray]: The opened dataset and its full array.
    """
    path = str(tmp_path / "tiled.tif")
    arr = np.arange(256 * 256, dtype="float32").reshape(256, 256)
    Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0, 256), cell_size=1.0, epsg=4326),
    ).to_file(path, creation_options=["TILED=YES", "BLOCKXSIZE=64", "BLOCKYSIZE=64"])
    return Dataset.read_file(path), arr


class TestThreadsafeLazyReads:
    """The dask wiring: chunks= + lock=False + threadsafe=True."""

    def test_lockfree_equals_locked(self, tiled_raster):
        """The lock-free per-thread-handle compute equals the locked default."""
        ds, _ = tiled_raster
        lockfree = ds.read_array(band=0, chunks=64, lock=False, threadsafe=True)
        locked = ds.read_array(band=0, chunks=64)
        np.testing.assert_array_equal(
            lockfree.compute(),
            locked.compute(),
            err_msg="lock-free chunked compute diverged from the locked default",
        )

    def test_default_lock_is_lockfree_when_threadsafe(self, tiled_raster):
        """threadsafe=True without an explicit lock still computes correctly.

        Test scenario:
            lock=None with threadsafe=True resolves to DummyLock (per-thread
            handles need no chunk lock), so the parallel compute must equal
            the locked default without the caller passing lock=False.
        """
        ds, arr = tiled_raster
        lazy = ds.read_array(band=0, chunks=64, threadsafe=True)
        np.testing.assert_array_equal(
            lazy.compute(),
            arr,
            err_msg="threadsafe default-lock compute returned wrong pixels",
        )
