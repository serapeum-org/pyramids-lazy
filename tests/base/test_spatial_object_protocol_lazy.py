"""LazyFeatureCollection protocol tests, relocated from pyramids-core (#1590).

The eager SpatialObject protocol tests stay in core; these need dask-geopandas (the lazy vector
stack behind pyramids-lazy[parquet]), so they live with the backend.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from pyramids.base.protocols import LazySpatialObject, SpatialObject

# Module-marked `parquet_lazy`: the conftest hook auto-skips these when the [parquet] extra
# (dask-geopandas) is absent, with the catalogued reason — so no per-test skipif is needed (a
# hand-rolled one would emit an uncatalogued skip reason and trip the no-silent-skip guard).
pytestmark = pytest.mark.parquet_lazy


class TestLazySpatialObject:
    """:class:`LazyFeatureCollection` satisfies :class:`LazySpatialObject`."""

    def test_lazy_feature_collection_satisfies_lazy_protocol(self):
        """`isinstance(lfc, LazySpatialObject)` is True at runtime."""
        import dask_geopandas as dg

        from pyramids.feature import LazyFeatureCollection

        gdf = gpd.GeoDataFrame(
            {"v": [1]},
            geometry=[box(0, 0, 1, 1)],
            crs="EPSG:4326",
        )
        ddf = dg.from_geopandas(gdf, npartitions=1)
        lfc = LazyFeatureCollection.from_dask_gdf(ddf)
        assert isinstance(lfc, LazySpatialObject)

    def test_lazy_feature_collection_does_not_satisfy_eager_protocol(self):
        """LazyFC no longer fakes eager `top_left_corner`."""
        import dask_geopandas as dg

        from pyramids.feature import LazyFeatureCollection

        gdf = gpd.GeoDataFrame(
            {"v": [1]},
            geometry=[box(0, 0, 1, 1)],
            crs="EPSG:4326",
        )
        ddf = dg.from_geopandas(gdf, npartitions=1)
        lfc = LazyFeatureCollection.from_dask_gdf(ddf)
        assert not isinstance(lfc, SpatialObject)

    def test_generic_consumer_accepts_lazy_via_union(self):
        """A function typed against `SpatialObject | LazySpatialObject` accepts both."""
        import dask_geopandas as dg

        from pyramids.feature import LazyFeatureCollection

        gdf = gpd.GeoDataFrame(
            {"v": [1]},
            geometry=[box(500000.0, 3400000.0, 510000.0, 3410000.0)],
            crs="EPSG:32636",
        )
        ddf = dg.from_geopandas(gdf, npartitions=1)
        lfc = LazyFeatureCollection.from_dask_gdf(ddf)

        def epsg_of(obj: SpatialObject | LazySpatialObject) -> int | None:
            return obj.epsg

        assert epsg_of(lfc) == 32636


class TestComputeTotalBounds:
    """The explicit `compute_total_bounds()` helper forces reduction."""

    def test_returns_four_element_array(self):
        """The helper materialises the lazy Scalar into a concrete array."""
        import dask_geopandas as dg

        from pyramids.feature import LazyFeatureCollection

        gdf = gpd.GeoDataFrame(
            {"v": [1, 2]},
            geometry=[box(0, 0, 1, 1), box(2, 3, 4, 5)],
            crs="EPSG:4326",
        )
        ddf = dg.from_geopandas(gdf, npartitions=1)
        lfc = LazyFeatureCollection.from_dask_gdf(ddf)
        bounds = lfc.compute_total_bounds()
        assert isinstance(bounds, np.ndarray)
        assert bounds.shape == (4,)
        assert bounds.tolist() == [0.0, 0.0, 4.0, 5.0]

    def test_top_left_corner_property_is_gone(self):
        """Eager-only `top_left_corner` no longer exists on LazyFC."""
        import dask_geopandas as dg

        from pyramids.feature import LazyFeatureCollection

        gdf = gpd.GeoDataFrame(
            {"v": [1]},
            geometry=[box(0, 0, 1, 1)],
            crs="EPSG:4326",
        )
        ddf = dg.from_geopandas(gdf, npartitions=1)
        lfc = LazyFeatureCollection.from_dask_gdf(ddf)
        assert not hasattr(lfc, "top_left_corner")
        with pytest.raises(AttributeError, match="top_left_corner"):
            lfc.top_left_corner  # noqa: B018
