# Lazy vector reads

Backs `FeatureCollection.read_file(backend="dask")` and
`FeatureCollection.read_parquet(backend="dask")`. Both return a
[`LazyFeatureCollection`](lazy-feature-collection.md).

| | |
|---|---|
| Backend methods | `read_vector_lazy`, `read_vector_parquet_lazy` |
| Module | `pyramids_lazy._vector_read` |
| Extra required | `parquet` (`pyarrow`, `dask-geopandas`) |
| Returns | `LazyFeatureCollection` |

```python
from pyramids.feature import FeatureCollection

fc = FeatureCollection.read_parquet("parcels.parquet", backend="dask")
fc.npartitions
fc.geometry.area.mean().compute()
```

## Partitioning

Partitioning is resolved before the read: an explicit `npartitions` or a target partition size is
translated into the row-group or chunk layout the underlying reader wants, so a caller does not
have to know whether the source is OGR-readable or already Parquet. Passing neither leaves the
reader's own default in place rather than guessing a split.

These are the `backend="dask"` branches lifted out of core's `pyramids.feature._read`; the eager
branches stay in core, which is why a minimal install never evaluates this module.

## API

::: pyramids_lazy._vector_read.read_file_dask
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._vector_read.read_parquet_dask
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
