# Backend contract

The object `pyramids-core` resolves and dispatches to. One instance, one method per lazy operation,
plus an `api_version` core checks before using it.

| | |
|---|---|
| Entry-point group | `pyramids.compute_backend` |
| Entry point | `lazy = pyramids_lazy.backend:backend` |
| Fallback | `pyramids_lazy.get_compute_backend()` |
| Contract version | `API_VERSION = 2` |
| Authoritative Protocol | `pyramids.base._compute_backend.ComputeBackend` |

Every method is a thin forwarder that imports its implementation module **inside the method body**,
so the dask/zarr stack is imported only when an operation is actually used — importing
`pyramids_lazy` itself does not drag in dask.

## Why a singleton

The Protocol describes a single object rather than a class discovered per source, mirroring core's
plot-backend seam rather than its raster/vector driver seam. Both discovery paths resolve to the
same module-level `backend` instance, so a test that patches one sees the other.

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
