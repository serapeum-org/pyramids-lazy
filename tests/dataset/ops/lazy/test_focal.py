"""Lazy (dask) focal tests relocated from pyramids-core (#1590).

These exercise ``Dataset.focal_mean`` / ``slope`` / ``hillshade`` on the ``chunks=`` path, which
routes to this backend's ``focal_lazy``. The eager focal tests (and the branded-ImportError guard)
stay in pyramids-core; only the dask-materialising cases live here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset

pytestmark = pytest.mark.lazy


@pytest.fixture
def constant_raster(tmp_path):
    """5×5 raster of constant value — focal_mean must equal that value."""
    arr = np.full((5, 5), 7.0, dtype=np.float32)
    ds = Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0.0, 5.0), cell_size=1.0, epsg=4326),
    )
    path = str(tmp_path / "const.tif")
    ds.to_file(path)
    return Dataset.read_file(path)


@pytest.fixture
def ramp_raster(tmp_path):
    """5×5 ramp along the x-axis so slope is non-zero."""
    arr = np.tile(np.arange(5, dtype=np.float32), (5, 1))
    ds = Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0.0, 5.0), cell_size=1.0, epsg=4326),
    )
    path = str(tmp_path / "ramp.tif")
    ds.to_file(path)
    return Dataset.read_file(path)


class TestFocalMean:
    def test_lazy_matches_eager(self, constant_raster):
        eager = constant_raster.focal_mean(radius=1)
        lazy = constant_raster.focal_mean(radius=1, chunks="auto").compute()
        assert np.allclose(eager, lazy)


class TestSlope:
    def test_lazy_slope_equals_eager_interior(self, ramp_raster):
        """Interior cells agree; edge cells can differ due to boundary.

        ``map_overlap`` uses the reflect-halo inside each chunk,
        whereas ``np.gradient`` uses forward/backward differences at
        the outermost rows/cols. Compare the interior only.
        """
        eager = ramp_raster.slope()
        lazy = ramp_raster.slope(chunks="auto").compute()
        assert np.allclose(eager[1:-1, 1:-1], lazy[1:-1, 1:-1])


class TestHillshade:
    def test_lazy_matches_eager_interior(self, ramp_raster):
        """Same boundary caveat as slope — compare interior cells."""
        eager = ramp_raster.hillshade()
        lazy = ramp_raster.hillshade(chunks="auto").compute()
        assert np.allclose(eager[1:-1, 1:-1], lazy[1:-1, 1:-1])


class TestEagerAndLazyAgree:
    """L4/S4: ``chunks=`` is a memory choice, not a change of result."""

    @pytest.fixture(scope="function")
    def dem(self, tmp_path) -> Dataset:
        """A 16x16 float DEM with one no-data cell, written to disk.

        Returns:
            Dataset: the raster read back from disk.
        """
        array = np.random.default_rng(3).random((16, 16)) * 100.0
        array[8, 8] = -9999.0
        path = tmp_path / "dem.tif"
        Dataset.from_array(
            array.astype("float32"),
            no_data_value=-9999.0,
            geo_ref=GeoReference(
                top_left_corner=(0.0, 16.0), cell_size=1.0, epsg=32636
            ),
        ).to_file(str(path))
        return Dataset.read_file(str(path))

    @pytest.mark.parametrize(
        "kernel", ["focal_mean", "focal_std", "slope", "aspect", "hillshade"]
    )
    def test_every_kernel_matches_its_chunked_self(self, dem, kernel):
        """Chunking a kernel must not move a single cell.

        Args:
            dem: The raster fixture.
            kernel: Name of the `Dataset` method under test.

        Test scenario:
            Two things made this fail. `focal_std` chains two box filters, so
            its footprint is twice the radius and a radius-wide halo left block
            edges filtering a truncated neighbourhood. And `map_overlap` padded
            the raster's own border, which handed an edge block a neighbourhood
            the eager path never sees -- 59/256 cells of `slope`, all on the
            border.
        """
        eager = np.asarray(getattr(dem, kernel)())
        lazy = np.asarray(getattr(dem, kernel)(chunks=(4, 4)))
        mismatched = int((~np.isclose(eager, lazy, equal_nan=True)).sum())
        assert mismatched == 0, (
            f"{kernel} disagrees with its chunked self at {mismatched}/256 cells"
        )

    @pytest.mark.parametrize("chunks", [(2, 2), (4, 4), (5, 7), (16, 16)])
    def test_the_result_is_independent_of_the_chunk_shape(self, dem, chunks):
        """Any chunking gives the same answer, including uneven ones.

        Args:
            dem: The raster fixture.
            chunks: The chunk shape to compare against the eager path.

        Test scenario:
            Chunk shapes that do not divide the raster evenly leave partial
            edge blocks, which is where a halo or offset error surfaces.
        """
        eager = np.asarray(dem.focal_std(radius=1))
        lazy = np.asarray(dem.focal_std(radius=1, chunks=chunks))
        np.testing.assert_allclose(eager, lazy, equal_nan=True)
