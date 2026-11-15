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
lazy = cube.chunk({"rows": 256, "cols": 256})
lazy.chunksizes  # an alias of .chunks: per dimension, merged across the variables
eager = lazy.compute()
```

`chunk` takes the same spec as `read_array`: `"auto"`, a single int, a sequence per axis, or a dict
keyed by `rows` / `cols` / `columns` / `bands` (case-insensitive) or by integer axis index. It is
**not** keyed by the file's dimension names — `{"time": 24}` raises `ValueError`.

Whether `reduce(dim, how=…)` stays lazy depends on what the view holds:

- **a single variable** (a cube pinned with `get_variable(…)`, or any view a composed op produced)
  composes lazily — it returns another `LazyNetCDF`, with no warning;
- **a multi-variable container** cannot compose per block, so it materialises and runs eagerly,
  returning the eager cube and warning that it has done so.

Call `compute()` first when you want the eager path to be explicit rather than implied.

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
