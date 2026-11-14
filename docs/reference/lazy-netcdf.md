# Lazy NetCDF cube

Backs `NetCDF.chunk()`. A `LazyNetCDF` is the lazy twin of an eager cube: one `dask.array.Array`
per gridded variable, alongside the eager cube it was built from.

| | |
|---|---|
| Backend method | `build_lazy_cube` |
| Module | `pyramids_lazy._lazy_cube` |
| Returns | `LazyNetCDF` |
| Public as | `pyramids.netcdf.LazyNetCDF` |

```python
from pyramids.netcdf import NetCDF

cube = NetCDF.read_file("era5.nc")
lazy = cube.chunk({"time": 24})
lazy.chunksizes
eager = lazy.mean(dim="time").compute()
```

## A wrapper, not a subclass

`LazyFeatureCollection` can subclass `dask_geopandas.GeoDataFrame` because a feature collection
*is* one frame. A cube is many arrays, so there is no dask type to subclass — this is a wrapper
instead, holding the lazy arrays plus the eager cube that is both the source of its geo and band
metadata and the twin its lifecycle materialises back to.

The dask lifecycle members live here: `chunks` and `chunksizes` read the chunking off the arrays,
`compute()` and `load()` return the eager cube, `persist()` realises the graph in worker memory
while staying lazy, and `unify_chunks()` re-chunks every variable to a common grid.

## API

::: pyramids_lazy._lazy_cube.LazyNetCDF
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._lazy_cube.build_lazy_cube
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
