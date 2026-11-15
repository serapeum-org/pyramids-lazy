"""Benchmarks for the Zarr compute-backend ops — the only fully-parallel raster write path.

Measures ``write_dataset_to_zarr`` (plain + with overview levels) and ``read_dataset_from_zarr``,
driven exactly as pyramids-core dispatches to them. These are pyramids-lazy's own hot paths.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("zarr")
pytest.importorskip("dask")

from pyramids_lazy import backend  # noqa: E402


@pytest.mark.benchmark(group="zarr-write")
def test_write_dataset_to_zarr(benchmark, source_dataset, tmp_path):
    """Full parallel Zarr write of the raster (no overviews)."""
    store = str(tmp_path / "ds.zarr")
    benchmark(
        lambda: backend.write_dataset_to_zarr(
            source_dataset,
            store,
            compute=True,
            mode="w",
            chunks="auto",
            storage_options=None,
            compressor="auto",
            overview_factors=None,
            overview_resampling="average",
        )
    )


@pytest.mark.benchmark(group="zarr-write")
def test_write_dataset_to_zarr_with_overviews(benchmark, source_dataset, tmp_path):
    """Zarr write that also builds decimated overview levels [2, 4]."""
    # Rounds 2..N rebuild overviews over the same source handle (and its .tif.ovr sidecar written on
    # round 1), so this measures steady-state overview builds, not a cold first build.
    store = str(tmp_path / "ov.zarr")
    benchmark(
        lambda: backend.write_dataset_to_zarr(
            source_dataset,
            store,
            compute=True,
            mode="w",
            chunks="auto",
            storage_options=None,
            compressor="auto",
            overview_factors=[2, 4],
            overview_resampling="average",
        )
    )


@pytest.mark.benchmark(group="zarr-read")
def test_read_dataset_from_zarr(benchmark, source_dataset, tmp_path):
    """Read a written store back and materialise the array (round-trip read cost)."""
    store = str(tmp_path / "rd.zarr")
    backend.write_dataset_to_zarr(
        source_dataset,
        store,
        compute=True,
        mode="w",
        chunks="auto",
        storage_options=None,
        compressor="auto",
        overview_factors=None,
        overview_resampling="average",
    )

    def _read():
        recovered = backend.read_dataset_from_zarr(
            store, chunks=None, storage_options=None, level=1, data_name=None
        )
        return np.asarray(recovered.read_array())

    benchmark(_read)
