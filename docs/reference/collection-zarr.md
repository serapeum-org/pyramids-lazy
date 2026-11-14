# Zarr — DatasetCollection

Backs `DatasetCollection.to_zarr()` and `DatasetCollection.from_zarr()`. The 4-D
`(time, band, y, x)` cube, one dask chunk per Zarr chunk.

| | |
|---|---|
| Backend methods | `write_collection_to_zarr`, `read_collection_from_zarr` |
| Module | `pyramids_lazy._collection_zarr` |
| Returns | a `DatasetCollection` on read; `None` (or a `Delayed`) on write |

```python
from pyramids.dataset import DatasetCollection

cube = DatasetCollection.read_multiple_files("rasters", with_order=False)
cube.to_zarr("cube.zarr")
back = DatasetCollection.from_zarr("cube.zarr")
```

## Region append

A collection can be extended along an existing axis without rewriting the store: the new data is
written into a region of the existing arrays, and the collection metadata (`_meta`, `_files`) is
re-finalised afterwards so a later read reconstructs the full cube. The region is translated to
per-axis slices, and an unknown dimension name is refused with a `ValueError`.

Chunk-grid alignment is **not** validated. A region that straddles a chunk boundary is written as
given, so it is the caller's job to append on the chunk grid.

As with the single-`Dataset` path, the pyramids geobox is persisted as GeoZarr attributes so
standard readers can reconstruct it.

## API

::: pyramids_lazy._collection_zarr.write_collection_to_zarr
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._collection_zarr.read_collection_from_zarr
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
