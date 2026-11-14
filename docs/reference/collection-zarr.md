# Zarr — DatasetCollection

Backs `DatasetCollection.to_zarr()` and `DatasetCollection.from_zarr()`. The 4-D
`(time, band, y, x)` cube, one dask chunk per Zarr chunk.

| | |
|---|---|
| Backend methods | `write_collection_to_zarr`, `read_collection_from_zarr` |
| Module | `pyramids_lazy._collection_zarr` |
| Returns | a `DatasetCollection` on read |

```python
from pyramids.dataset.collection import DatasetCollection

cube = DatasetCollection.read_separate_files("rasters/*.tif", with_order=False)
cube.to_zarr("cube.zarr")
back = DatasetCollection.from_zarr("cube.zarr")
```

## Region append

A collection can be extended along an existing axis without rewriting the store: the new data is
written into a region of the existing arrays, and the collection metadata (`_meta`, `_files`) is
re-finalised afterwards so a later read reconstructs the full cube. The region is translated to
per-axis slices, so an append that does not line up on the chunk grid is refused rather than
silently writing a partial chunk.

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
