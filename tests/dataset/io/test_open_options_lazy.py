"""Lazy open-option propagation relocated from pyramids-core (#1590).

A lazy ``chunks=`` read and the lazy ``.data`` cube reopen the file inside dask tasks, so the captured
GDAL open options must travel with them. These drive the pyramids-lazy backend, so they live here; the
eager open-option tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.dataset import Dataset, DatasetCollection
from pyramids.dataset import collection as collection_module
from pyramids.dataset.engines import io as io_engine
from tests._helpers import write_raster

pytestmark = pytest.mark.lazy

_REAL_GT = (100.0, 1.0, 0.0, 200.0, 0.0, -1.0)
_IDENTITY_GT = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


@pytest.fixture
def georeferenced_tif(tmp_path):
    """An 8x8 GeoTIFF whose internal georef is `_REAL_GT`."""
    return write_raster(
        tmp_path / "geo.tif",
        np.ones((8, 8), "float32"),
        (100.0, 200.0),
        cell_size=1.0,
    )


class TestReadFileOpenOptions:
    """`Dataset.read_file(open_options=...)` on the lazy chunked path."""

    def test_options_survive_lazy_chunked_reopen(self, georeferenced_tif, mocker):
        """A lazy chunked read forwards the captured options to the per-chunk opener."""
        dataset = Dataset.read_file(
            georeferenced_tif, open_options={"GEOREF_SOURCES": "NONE"}
        )
        real = io_engine.gdal_raster_open
        seen: list = []

        def spy(*args, **kwargs):
            seen.append(kwargs.get("open_options"))
            return real(*args, **kwargs)

        mocker.patch.object(io_engine, "gdal_raster_open", side_effect=spy)
        lazy = dataset.read_array(chunks=4)
        assert hasattr(lazy, "dask"), "expected a lazy dask array"
        result = np.asarray(lazy.compute())
        assert result.shape == (8, 8), f"unexpected shape {result.shape}"
        assert np.allclose(result, 1.0), "lazy chunked read altered the values"
        assert seen, "the per-chunk reopen opener was never called"
        assert all(o == ("GEOREF_SOURCES=NONE",) for o in seen), seen


class TestCollectionOpenOptions:
    """`DatasetCollection.from_files(open_options=...)` on the lazy `.data` path."""

    @pytest.fixture
    def two_files(self, tmp_path):
        """Two georeferenced GeoTIFFs sharing one header."""
        return [
            write_raster(
                tmp_path / f"g{i}.tif", np.ones((8, 8), "float32"), (100.0, 200.0)
            )
            for i in range(2)
        ]

    def test_lazy_data_forwards_the_option(self, two_files, mocker):
        """The lazy `.data` graph threads the option into every per-timestep open."""
        collection = DatasetCollection.from_files(
            two_files, open_options={"GEOREF_SOURCES": "NONE"}
        )
        real = collection_module._lazy_timestep
        seen: list = []

        def spy(path, meta, gdal_env, lock, **kwargs):
            seen.append(kwargs.get("open_options"))
            return real(path, meta, gdal_env, lock, **kwargs)

        mocker.patch.object(collection_module, "_lazy_timestep", side_effect=spy)
        _ = collection.data
        assert seen == [("GEOREF_SOURCES=NONE",), ("GEOREF_SOURCES=NONE",)], seen
