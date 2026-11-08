"""Zarr read/write for DatasetCollection, exercised through the compute backend.

Covers the cube operations moved out of pyramids-core (``DatasetCollection.to_zarr`` /
``from_zarr``): the backend's ``write_collection_to_zarr`` / ``read_collection_from_zarr`` must
round-trip a multi-timestep cube and preserve its geobox and time length.
"""

import numpy as np
import pytest

pytest.importorskip("zarr")
pytest.importorskip("dask")

from pyramids.dataset import Dataset, DatasetCollection, GeoReference  # noqa: E402

from pyramids_lazy import backend  # noqa: E402


@pytest.fixture
def three_files_ramp(tmp_path):
    """Three GeoTIFFs filled with 1, 2, 3 (shape 3x4), as core's own cube tests use."""
    paths = []
    for i in range(3):
        ds = Dataset.from_array(
            np.full((3, 4), float(i + 1), dtype=np.float32),
            geo_ref=GeoReference(top_left_corner=(0.0, 3.0), cell_size=1.0, epsg=4326),
        )
        p = str(tmp_path / f"f{i}.tif")
        ds.to_file(p)
        paths.append(p)
    return paths


def test_collection_zarr_round_trip(three_files_ramp, tmp_path):
    """A cube written then read back through the backend preserves shape, time and values."""
    collection = DatasetCollection.from_files(three_files_ramp)
    store = str(tmp_path / "cube.zarr")

    result = backend.write_collection_to_zarr(
        collection,
        store,
        compute=True,
        mode="w",
        storage_options=None,
        compressor="auto",
        append_dim=None,
        region=None,
    )
    assert result is None

    recovered = backend.read_collection_from_zarr(
        DatasetCollection, store, storage_options=None
    )
    assert recovered.time_length == 3
    cube = np.asarray(recovered.data)
    assert cube.shape == (3, 1, 3, 4)
    # Each timestep is a constant ramp 1, 2, 3.
    assert [float(cube[t].flat[0]) for t in range(3)] == [1.0, 2.0, 3.0]


def test_collection_zarr_deferred_write_returns_delayed(three_files_ramp, tmp_path):
    """compute=False returns a dask Delayed that writes on compute()."""
    pytest.importorskip("dask")
    from dask.delayed import Delayed

    collection = DatasetCollection.from_files(three_files_ramp)
    store = str(tmp_path / "lazy.zarr")
    delayed = backend.write_collection_to_zarr(
        collection,
        store,
        compute=False,
        mode="w",
        storage_options=None,
        compressor="auto",
        append_dim=None,
        region=None,
    )
    assert isinstance(delayed, Delayed)
    delayed.compute()
    recovered = backend.read_collection_from_zarr(
        DatasetCollection, store, storage_options=None
    )
    assert recovered.time_length == 3
