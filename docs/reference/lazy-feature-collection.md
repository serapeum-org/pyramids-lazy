# Lazy feature collection

The lazy vector type returned by the [lazy vector reads](vector-read.md).

| | |
|---|---|
| Module | `pyramids_lazy._lazy_collection` |
| Public as | `pyramids.feature.LazyFeatureCollection` |
| Extra required | `parquet` (`pyarrow`, `dask-geopandas`) |

## What it is — and is not

A `LazyFeatureCollection` **is** a `dask_geopandas.GeoDataFrame`. It is **not** a
`FeatureCollection` and **not** a `geopandas.GeoDataFrame`. Both it and the eager
`FeatureCollection` satisfy core's `SpatialObject` protocol, so a consumer accepts either through
`isinstance(x, SpatialObject)` rather than by checking for a concrete class.

That subclass relationship is why the whole dask-geopandas API is available directly on the object
— `npartitions`, `spatial_shuffle()`, `compute()`, the geometry accessors — rather than being
re-exposed method by method.

## Why the import is deferred

This module hard-imports `dask_geopandas`, which only the `parquet` extra installs. The class is
therefore resolved through a module-level `__getattr__` on the package root, so importing
`pyramids_lazy` on a minimal install never evaluates this file. `LazyNetCDF`, which needs only
dask, is imported eagerly instead.

Pickling is handled explicitly: a reconstruction hook rebuilds the subclass rather than letting
dask's default reduce return a plain `dask_geopandas.GeoDataFrame`, so a round trip through a
distributed worker preserves the type.

## API

::: pyramids_lazy._lazy_collection.LazyFeatureCollection
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
