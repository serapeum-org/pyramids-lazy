"""Fixtures and GDAL-cache control for the pyramids-lazy performance benchmarks.

These benchmarks are deliberately *outside* ``testpaths = ["tests"]``, so the ``test`` / ``main`` /
CI test tasks never collect them. Run them explicitly with ``pixi run -e dev benchmark`` (or
``pytest benchmarks``). They measure the compute backend's own hot paths — the hand-built dask graph
(``build_lazy_array``), the only fully-parallel raster Zarr write, and the lazy NetCDF read — for
regression detection, not profiling.

Two methodology choices make the numbers comparable run to run:

* ``_pin_gdal_cache`` fixes ``GDAL_CACHEMAX`` for the whole session, so results do not drift with the
  ambient block-cache size.
* the raster is synthetic and built from a fixed seed at a representative size, reused read-only (a
  warm GDAL block cache — a consistent regime). The NetCDF read reuses a committed fixture.
"""

from __future__ import annotations

# isort: off
import pyramids  # noqa: F401  # vendored-GDAL bootstrap: must precede any osgeo import
from osgeo import gdal  # noqa: E402

# isort: on
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from pyramids.dataset import Dataset, GeoReference  # noqa: E402

# 1024x1024 float32 ~= 4 MB: big enough that a Zarr write/read is measurable above fixture overhead,
# small enough that a full sweep finishes in seconds.
RASTER_SHAPE = (1024, 1024)
_GDAL_CACHE_BYTES = 256 * 1024 * 1024

# A small committed 3-D NetCDF fixture (the same one the lazy-read tests use), resolved from the repo
# root where ``pixi run`` invokes pytest.
NETCDF_FIXTURE = "tests/data/netcdf/cf__4v__1d3-3d1__proj__y-desc.nc"


@pytest.fixture(scope="session", autouse=True)
def _pin_gdal_cache():
    """Pin ``GDAL_CACHEMAX`` for the session so I/O timings are reproducible."""
    previous = gdal.GetCacheMax()
    gdal.SetCacheMax(_GDAL_CACHE_BYTES)
    yield
    gdal.SetCacheMax(previous)


@pytest.fixture(scope="session")
def _geo_ref() -> GeoReference:
    """A north-up WGS84 georeference anchored at the origin."""
    return GeoReference(top_left_corner=(0.0, 0.0), cell_size=0.01, epsg=4326)


@pytest.fixture(scope="session")
def on_disk_tif(_geo_ref: GeoReference, tmp_path_factory) -> str:
    """Path to a GeoTIFF written once from a deterministic ``RASTER_SHAPE`` float32 array."""
    array = np.random.default_rng(0).random(RASTER_SHAPE, dtype=np.float32)
    path = tmp_path_factory.mktemp("bench_src") / "raster.tif"
    Dataset.from_array(
        array, geo_ref=_geo_ref, no_data_value=-9999.0, path=str(path)
    ).close()
    return str(path)


@pytest.fixture
def source_dataset(on_disk_tif: str) -> Dataset:
    """A fresh file-backed ``Dataset`` handle (``to_zarr`` reopens the source by path)."""
    return Dataset.read_file(on_disk_tif)


@pytest.fixture(scope="session")
def netcdf_path() -> str:
    """Repo-root-relative path to the committed 3-D NetCDF fixture."""
    return NETCDF_FIXTURE
