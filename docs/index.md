# pyramids-lazy

The lazy / dask compute backend for [pyramids](https://github.com/serapeum-org/pyramids-core).
`pyramids-core` ships the raster, cube and vector data model and every eager operation that follows
from data already in hand; what it deliberately does *not* ship is the dask/zarr stack. This package
is that stack, and nothing else.

Installing it alongside `pyramids-core` turns on every lazy operation — lazy raster and variable
reads, lazy NetCDF cubes, `to_dask_dataframe`, kerchunk manifests, multi-file dask reads, Zarr
read/write for `Dataset` and `DatasetCollection`, and lazy (dask-geopandas) vector reads — without
dask, distributed, zarr or kerchunk ever being pulled into a bare `pyramids-core` install.

## No import required

There is no API here you call directly, and no configuration step. `pyramids-core` resolves the
backend at runtime and routes its lazy operations to it:

```python
from pyramids.netcdf import NetCDF

cube = NetCDF.read_file("era5.nc")
lazy = cube.chunk({"time": 24})  # dispatched to this package's build_lazy_cube
```

Resolution happens two ways, both landing on the same singleton:

| Path | Mechanism |
|---|---|
| Preferred | the `pyramids.compute_backend` entry point → `pyramids_lazy.backend:backend` |
| Fallback | an optional import of `pyramids_lazy`, whose `get_compute_backend()` returns the backend |

The fallback exists so an editable checkout on `PYTHONPATH`, with no installed distribution
metadata, still works. With neither available, core's lazy operations raise a branded
`OptionalPackageDoesNotExist` telling the user to install the lazy stack.

## The layer boundary

The split is the point of the package, so it is worth stating precisely.

| Concern | Lives in |
|---|---|
| `Dataset`, `NetCDF`, `DatasetCollection`, `FeatureCollection` and every eager operation | `pyramids-core` |
| The `ComputeBackend` Protocol, its resolver, caching and collision rules | `pyramids.base._compute_backend` |
| The stable re-export boundary the backend imports core through | `pyramids.lazy_support` |
| Lazy dask-array reads, lazy cubes, Zarr I/O, kerchunk, lazy vector reads | `pyramids_lazy` |

Core publishes the contract; this package implements it. The coupling is one-directional —
`pyramids-lazy` depends on `pyramids-core`, never the reverse — so core's release cadence stays
independent of the dask/zarr stack.

Two further constraints are worth knowing before changing anything here:

- **Core is reached only through `pyramids.lazy_support`.** That facade re-exports — never
  reimplements — the exact core primitives the lazy modules need, and is versioned by
  `LAZY_SUPPORT_API_VERSION`. Reaching into a core internal directly couples this package to
  core's private layout.
- **GDAL is not a dependency.** The published wheel inherits the GDAL that `pyramids-core` vendors;
  declaring a second `libgdal` would risk an ABI mismatch rather than a version conflict.

## Status

`0.1.0`, released to PyPI. The v2 backend contract covers the raster (`Dataset`), raster-cube
(`NetCDF`) and vector (`FeatureCollection`) lazy operations. `MeshDataset`'s own lazy surface is
intentionally outside it and is deferred to a later contract revision.
