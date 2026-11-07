"""Zarr read/write for Dataset, exercised through the compute backend.

These cover the operations moved out of pyramids-core (``Dataset.to_zarr`` / ``Dataset.from_zarr``):
the backend's ``write_dataset_to_zarr`` / ``read_dataset_from_zarr`` must round-trip a raster and
preserve geo-metadata, driven exactly as core will dispatch to them.
"""

import numpy as np
import pytest

pytest.importorskip("zarr")
pytest.importorskip("dask")

from pyramids.dataset import Dataset, GeoReference  # noqa: E402

from pyramids_lazy import backend  # noqa: E402


@pytest.fixture
def source_tif(tmp_path):
    """A small file-backed 4x4 float32 Dataset (to_zarr reopens the source by path)."""
    tif = tmp_path / "src.tif"
    Dataset.from_array(
        np.arange(16, dtype="float32").reshape(4, 4),
        geo_ref=GeoReference(top_left_corner=(0.0, 4.0), cell_size=1.0, epsg=4326),
        path=str(tif),
    ).close()
    return Dataset.read_file(str(tif))


def test_dataset_zarr_round_trip(source_tif, tmp_path):
    """A raster written then read back through the backend preserves values and geobox."""
    store = str(tmp_path / "ds.zarr")
    result = backend.write_dataset_to_zarr(
        source_tif,
        store,
        compute=True,
        mode="w",
        chunks="auto",
        storage_options=None,
        compressor="auto",
        overview_factors=None,
        overview_resampling="average",
    )
    assert result is None  # compute=True writes immediately
    recovered = backend.read_dataset_from_zarr(
        store, chunks=None, storage_options=None, level=1, data_name=None
    )
    assert (recovered.rows, recovered.columns) == (4, 4)
    assert recovered.epsg == 4326
    assert np.array_equal(
        np.asarray(recovered.read_array()),
        np.arange(16, dtype="float32").reshape(4, 4),
    )


def test_overview_levels_round_trip(source_tif, tmp_path):
    """to_zarr(overview_factors=...) writes decimated levels read back via level=."""
    store = str(tmp_path / "ov.zarr")
    backend.write_dataset_to_zarr(
        source_tif,
        store,
        compute=True,
        mode="w",
        chunks="auto",
        storage_options=None,
        compressor="auto",
        overview_factors=[2],
        overview_resampling="average",
    )
    level2 = backend.read_dataset_from_zarr(
        store, chunks=None, storage_options=None, level=2, data_name=None
    )
    assert (level2.rows, level2.columns) == (2, 2)
