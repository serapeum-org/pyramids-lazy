"""Lazy gdal_env propagation relocated from pyramids-core (#1590).

A lazy ``chunks=`` read opens the file inside the dask task, so the captured ``gdal_env`` must travel
with the task. This drives the pyramids-lazy backend, so it lives here; the eager gdal_env capture
tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.dataset import Dataset

from tests._helpers import write_raster

pytestmark = pytest.mark.lazy

ENV = {"AWS_REQUEST_PAYER": "requester"}


@pytest.fixture
def tif(tmp_path):
    """A small single-band GeoTIFF path (4x4 EPSG:4326 raster filled with 7.0)."""
    return write_raster(
        tmp_path / "signed.tif", np.full((4, 4), 7.0, "float32"), (0.0, 4.0)
    )


class TestGdalEnvOnLazyReads:
    """The captured config travels with the dask tasks of a lazy read."""

    def test_chunk_tasks_receive_the_env(self, tif, monkeypatch):
        """`_read_chunk` is handed the dataset's captured config."""
        from pyramids.dataset.ops import io as ops_io

        seen = {}
        original = ops_io._read_chunk

        def spy(block_info=None, *, gdal_env=None, **kwargs):
            seen["gdal_env"] = gdal_env
            return original(block_info, gdal_env=gdal_env, **kwargs)

        monkeypatch.setattr(ops_io, "_read_chunk", spy)
        Dataset.read_file(tif, gdal_env=ENV).read_array(chunks=-1).compute()
        assert seen["gdal_env"] == ENV, f"env not shipped to the chunk task: {seen}"

    def test_unsigned_chunk_tasks_receive_none(self, tif, monkeypatch):
        """An ordinary lazy read ships no config at all."""
        from pyramids.dataset.ops import io as ops_io

        seen = {}
        original = ops_io._read_chunk

        def spy(block_info=None, *, gdal_env=None, **kwargs):
            seen["gdal_env"] = gdal_env
            return original(block_info, gdal_env=gdal_env, **kwargs)

        monkeypatch.setattr(ops_io, "_read_chunk", spy)
        Dataset.read_file(tif).read_array(chunks=-1).compute()
        assert seen["gdal_env"] is None, f"unexpected env on an unsigned read: {seen}"
