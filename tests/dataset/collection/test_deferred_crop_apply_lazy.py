"""Deferred (compute=False) crop/apply relocated from pyramids-core (#1590).

``apply``/``crop`` with ``compute=False`` build a dask.delayed via the pyramids-lazy backend. These
drive the backend, so they live here; the eager apply/crop and time-axis tests stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.dataset import Dataset, GeoReference
from pyramids.dataset.collection import DatasetCollection

pytestmark = pytest.mark.lazy


def build_dataset(value: float) -> Dataset:
    """A 4x4 raster filled with `value`."""
    return Dataset.from_array(
        np.full((4, 4), value, dtype="float32"),
        geo_ref=GeoReference(top_left_corner=(0.0, 4.0), cell_size=1.0, epsg=4326),
    )


@pytest.fixture
def collection() -> DatasetCollection:
    """A three-timestep collection of identical small rasters."""
    datasets = [build_dataset(i) for i in range(3)]
    return DatasetCollection(datasets[0], time_length=len(datasets), datasets=datasets)


class TestApplyDeferred:
    """`apply` honours `compute=False`."""

    def test_deferred_returns_something_computable(self, collection: DatasetCollection):
        """`compute=False` defers, and computing it yields the collection."""
        import dask

        deferred = collection.apply(lambda a: a + 1, compute=False)

        assert not isinstance(deferred, DatasetCollection)
        computed = dask.compute(deferred, scheduler="synchronous")[0]
        assert isinstance(computed, DatasetCollection)
        assert len(computed.datasets) == 3

    def test_deferred_rejects_inplace(self, collection: DatasetCollection):
        """The `compute=False` + `inplace=True` combination stays refused."""
        with pytest.raises(ValueError, match="cannot be combined with inplace"):
            collection.apply(lambda a: a + 1, inplace=True, compute=False)


class TestCropDeferred:
    """`crop` honours `compute=False`."""

    def test_deferred_rejects_inplace(self, collection: DatasetCollection):
        """The same guard applies to crop."""
        with pytest.raises(ValueError, match="cannot be combined with inplace"):
            collection.crop(
                bbox=[0.0, 0.0, 2.0, 2.0], epsg=4326, inplace=True, compute=False
            )
