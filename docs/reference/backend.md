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

That is a statement about the per-operation modules, not about dask. **With the `[parquet]` extra
installed, importing `pyramids_lazy` pulls the whole dask stack in** — and not because of anything
in this package. Nothing in `pyramids_lazy`, and nothing in `pyramids-core`, imports dask at module
scope. The chain is:

1. `pyramids_lazy/__init__.py` re-exports `LazyNetCDF` eagerly, which imports `pyramids.lazy_support`;
2. that facade imports core subpackages, reaching `pyramids.feature.__init__`;
3. which probes for `dask_geopandas` at module scope — a `try`/`except OptionalPackageDoesNotExist`
   that sets the `_HAS_DASK_GEOPANDAS` flag core uses for dispatch;
4. and `dask_geopandas` imports dask.

So the cost appears only when the optional dependency is present. Blocking `dask_geopandas` to
simulate a bare install, the probe fails, the flag goes `False` and no dask is imported:

| Imported | Modules | `dask` present |
|---|---|---|
| `pyramids` | 306 | no |
| `pyramids.feature`, with `dask_geopandas` blocked | 855 | no |
| `pyramids.feature` | 1397 | yes |
| `pyramids.dataset` | 1690 | yes |
| `pyramids_lazy` | 1868 | yes |

Measured:

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
