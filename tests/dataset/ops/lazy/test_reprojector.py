"""Lazy (deferred) Reprojector/Aligner tests relocated from pyramids-core (#1590).

``compute=False`` routes the reprojection deferral through this backend's ``delayed``. The eager
Reprojector/Aligner tests, the plan-pickle tests, and the branded-ImportError guard stay in
pyramids-core; only the dask-materialising cases live here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest
from dask.delayed import Delayed
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset
from pyramids.dataset.ops.reproject import Aligner, Reprojector

pytestmark = pytest.mark.lazy


@pytest.fixture
def wgs84_dataset(tmp_path):
    # A deterministic gradient (not zeros) so the lazy==eager pixel comparison
    # actually verifies warped values, not just shape agreement on an all-zero array.
    arr = np.arange(16, dtype=np.float32).reshape(4, 4)
    ds = Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0.0, 4.0), cell_size=1.0, epsg=4326),
    )
    return ds


@pytest.fixture
def wgs84_dataset_fine(tmp_path):
    arr = np.arange(64, dtype=np.float32).reshape(8, 8)
    ds = Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0.0, 4.0), cell_size=0.5, epsg=4326),
    )
    return ds


class TestReprojectorLazy:
    """``compute=False`` returns :class:`dask.delayed.Delayed`."""

    def test_returns_delayed(self, wgs84_dataset):
        op = Reprojector(target_epsg=3857)
        result = op(wgs84_dataset, compute=False)
        assert isinstance(result, Delayed)

    def test_delayed_compute_equals_eager(self, wgs84_dataset):
        op = Reprojector(target_epsg=3857)
        eager = op(wgs84_dataset)
        lazy = op(wgs84_dataset, compute=False).compute()
        assert lazy.epsg == eager.epsg
        assert lazy.rows == eager.rows
        np.testing.assert_array_equal(lazy.read_array(), eager.read_array())


class TestAlignerLazy:
    """``Aligner(..., compute=False)`` returns :class:`Delayed`."""

    def test_returns_delayed(self, wgs84_dataset, wgs84_dataset_fine):
        aligner = Aligner(wgs84_dataset)
        result = aligner(wgs84_dataset_fine, compute=False)
        assert isinstance(result, Delayed)

    def test_delayed_compute_matches_eager(self, wgs84_dataset, wgs84_dataset_fine):
        aligner = Aligner(wgs84_dataset)
        eager = aligner(wgs84_dataset_fine)
        lazy = aligner(wgs84_dataset_fine, compute=False).compute()
        assert lazy.rows == eager.rows
        np.testing.assert_array_equal(lazy.read_array(), eager.read_array())
