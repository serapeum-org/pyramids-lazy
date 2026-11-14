# Backend contract

The object `pyramids-core` resolves and dispatches to. One instance exposing one method per lazy
operation, two class accessors (`lazy_netcdf_type` and `lazy_feature_collection_type`, which hand
core the classes it forwards its own public names to) and an `api_version` core checks before
using it.

| | |
|---|---|
| Entry-point group | `pyramids.compute_backend` |
| Entry point | `lazy = pyramids_lazy.backend:backend` |
| Fallback | `pyramids_lazy.get_compute_backend()` |
| Contract version | `API_VERSION = 2` |
| Authoritative Protocol | `pyramids.base._compute_backend.ComputeBackend` |

Every method is a thin forwarder that imports its implementation module **inside the method body**,
so an operation's own module (`_zarr`, `_kerchunk_facade`, `_vector_read`, …) is loaded only when
that operation is first used.

That is a statement about the per-operation modules, not about dask. Importing `pyramids_lazy` does
pull dask in: the package re-exports `LazyNetCDF` eagerly, `_lazy_cube` imports
`pyramids.lazy_support` at module scope, and that facade imports dask itself — plus
`dask_geopandas` and `pyarrow` when the `[parquet]` extra is installed. Measured:

Measured in two separate interpreters:

```bash
$ python -c "import sys, pyramids;      print(len(sys.modules), 'dask' in sys.modules)"
306 False
$ python -c "import sys, pyramids_lazy; print(len(sys.modules), 'dask' in sys.modules)"
1868 True
```

What the deferred imports buy is therefore narrower than "no dask on import": zarr stays out until
a Zarr operation runs, and the per-operation modules stay unloaded until used.

## Why a singleton

The Protocol describes a single object rather than a class discovered per source, mirroring core's
plot-backend seam rather than its raster/vector driver seam. Both discovery paths resolve to the
same module-level `backend` instance.

That is a statement about which object they return, not about patching. Core caches the backend it
resolved, so a test that replaces `pyramids_lazy.backend` after resolution will not be seen by core
until the cache is cleared (`clear_compute_backend_cache`).

## API

::: pyramids_lazy.backend.LazyComputeBackend
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy.get_compute_backend
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

## The core seam guard

The package pins the facade contract at import time and fails loudly — telling the user to upgrade
`pyramids-core` — rather than later, on a missing attribute deep inside a lazy call.

::: pyramids_lazy._seam.require_core_seam
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

## Optional-extra handling

::: pyramids_lazy._optional
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
