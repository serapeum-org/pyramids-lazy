# Lazy vector reads

Backs `FeatureCollection.read_file(backend="dask")` and
`FeatureCollection.read_parquet(backend="dask")`. Both return a
[`LazyFeatureCollection`](lazy-feature-collection.md).

| | |
|---|---|
| Backend methods | `read_vector_lazy`, `read_vector_parquet_lazy` |
| Module | `pyramids_lazy._vector_read` |
| Needs | `read_file` → `dask-geopandas`; `read_parquet` → `dask-geopandas` **and** `pyarrow` |
| Returns | `LazyFeatureCollection` |

```python
from pyramids.feature import FeatureCollection

fc = FeatureCollection.read_parquet("parcels.parquet", backend="dask")
fc.npartitions
fc.geometry.area.mean().compute()
```

## Partitioning

Partitioning is resolved before the read, and the knobs are passed straight through to
`dask_geopandas` rather than translated:

- an explicit `npartitions` wins; failing that, an explicit `chunksize` is used;
- with neither, the split **is** guessed — a remote path (`/vsi`, `http(s)`, `s3`, `gs`, `az`) gets
  `npartitions=1`, and a local file gets `ceil(filesize / target_bytes_per_partition)`, falling
  back to `1` if the size cannot be read.

So a default read of a local file is already partitioned by size; pass `npartitions` explicitly
when you want a particular layout.

## Filters are refused, not ignored

`read_file(backend="dask")` raises `ValueError` if given `bbox`, `mask`, `rows`, `columns`, `where`
or `layer` — `dask_geopandas.read_file` has no pushdown for them, and silently dropping a filter
would return more rows than asked for. Filter after loading (`.clip` / `.loc` / `.compute`), or use
`read_parquet(backend="dask", filters=…)`.

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
