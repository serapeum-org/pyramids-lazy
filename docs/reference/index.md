# Reference

The API surface of `pyramids_lazy`, generated from the docstrings by
[mkdocstrings](https://mkdocstrings.github.io/).

What this package publishes is **one backend object**, not an API you call directly. It is
registered with `pyramids-core` through the `pyramids.compute_backend` entry-point group, so
installing the package is the whole integration: `pyramids` then dispatches every lazy operation to
it.

```python
from pyramids.netcdf import NetCDF

cube = NetCDF.read_file("era5.nc")
arr = cube.read_array(variable="pr", chunks="auto")  # → this package's read_array_lazy
```

So the backend contract below is the surface that matters. Each method forwards to one
implementation module, and each module has its own page.

| Backend method | Backs | Page |
|---|---|---|
| `read_array_lazy` | `NetCDF.read_array(chunks=…)` (not `Dataset`) | [Lazy raster reads](lazy-array.md) |
| `build_lazy_cube` | `NetCDF.chunk()` | [Lazy NetCDF cube](lazy-netcdf.md) |
| `to_dask_dataframe` | `NetCDF.to_dask_dataframe()` | [Dask dataframe view](dask-dataframe.md) |
| `open_mfdataset` | `NetCDF.open_mfdataset()` | [Multi-file reads](mfdataset.md) |
| `to_kerchunk`, `combine_kerchunk` | `NetCDF.to_kerchunk()`, `DatasetCollection.to_kerchunk()` | [Kerchunk manifests](kerchunk.md) |
| `write_dataset_to_zarr`, `read_dataset_from_zarr`, `classify_zarr_store` | `Dataset.to_zarr()` / `from_zarr()`, `read_file` GeoZarr routing | [Zarr (Dataset)](zarr.md) |
| `write_collection_to_zarr`, `read_collection_from_zarr` | `DatasetCollection.to_zarr()` / `from_zarr()` | [Zarr (DatasetCollection)](collection-zarr.md) |
| `read_vector_lazy`, `read_vector_parquet_lazy` | `FeatureCollection.read_file(backend="dask")` / `read_parquet(backend="dask")` | [Lazy vector reads](vector-read.md) |
| `lazy_netcdf_type`, `lazy_feature_collection_type` | the classes core forwards its public names to | [Backend contract](backend.md) |

## The contract itself

`pyramids.base._compute_backend.ComputeBackend` is the authoritative Protocol — the method
signatures here mirror it one-for-one, and it is the file to read before changing one. The backend
declares an `api_version` that core checks on resolution; adding a Protocol method is additive and
does not bump it, while removing a name or changing semantics does.

Two public view classes are re-exported from the package root, because core forwards its own public
`pyramids.netcdf.LazyNetCDF` and `pyramids.feature.LazyFeatureCollection` names — and their
`isinstance` checks — to them:

- [`LazyNetCDF`](lazy-netcdf.md) — a wrapper holding one dask array per gridded variable
- [`LazyFeatureCollection`](lazy-feature-collection.md) — a `dask_geopandas.GeoDataFrame` subclass

## Package root

::: pyramids_lazy
    options:
        show_root_heading: true
        show_source: false
        members:
            - get_compute_backend
            - REQUIRED_LAZY_SUPPORT_API_VERSION
        heading_level: 3
