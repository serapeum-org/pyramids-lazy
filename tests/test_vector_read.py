"""Lazy vector reads through the compute backend.

Covers the operations moved out of pyramids-core (``FeatureCollection.read_file(backend="dask")`` and
``read_parquet(backend="dask")``): the backend's ``read_vector_lazy`` / ``read_vector_parquet_lazy``
must return a lazy ``LazyFeatureCollection`` that computes to the right features.
"""

import pytest

dask_geopandas = pytest.importorskip("dask_geopandas")
pytest.importorskip("pyarrow")
import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from pyramids_lazy import backend  # noqa: E402
from pyramids_lazy._lazy_collection import LazyFeatureCollection  # noqa: E402


@pytest.fixture
def points_gdf():
    """A tiny 3-row point GeoDataFrame in EPSG:4326."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )


def test_read_vector_lazy_from_geojson(points_gdf, tmp_path):
    """read_vector_lazy returns a LazyFeatureCollection that computes to 3 rows."""
    path = str(tmp_path / "pts.geojson")
    points_gdf.to_file(path, driver="GeoJSON")
    lazy = backend.read_vector_lazy(path)
    assert isinstance(lazy, LazyFeatureCollection)
    computed = lazy.compute()
    assert len(computed) == 3


def test_read_vector_lazy_rejects_pushdown_filters(points_gdf, tmp_path):
    """Unsupported filter kwargs raise a clear ValueError (dask_geopandas has no pushdown)."""
    path = str(tmp_path / "pts.geojson")
    points_gdf.to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="does not support filter kwargs"):
        backend.read_vector_lazy(path, columns=["id"])


def test_read_vector_parquet_lazy(points_gdf, tmp_path):
    """read_vector_parquet_lazy returns a LazyFeatureCollection that computes to 3 rows."""
    path = str(tmp_path / "pts.parquet")
    points_gdf.to_parquet(path)
    lazy = backend.read_vector_parquet_lazy(path)
    assert isinstance(lazy, LazyFeatureCollection)
    assert len(lazy.compute()) == 3
