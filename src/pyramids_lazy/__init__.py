"""pyramids-lazy: the lazy / dask compute backend for pyramids.

This package implements the ``ComputeBackend`` contract published by ``pyramids-core`` and registers
it under the ``pyramids.compute_backend`` entry-point group. Installing ``pyramids-lazy`` alongside
``pyramids-core`` turns on every lazy/dask operation (raster, raster-cube and vector) without the
dask/zarr stack ever being pulled into a bare ``pyramids-core`` install.

Discovery works two ways, both resolving to the same singleton :data:`backend`:

* the ``pyramids.compute_backend`` entry point (``pyramids_lazy.backend:backend``), the path core
  prefers; and
* :func:`get_compute_backend`, the first-party fallback core calls by name when no entry point is
  registered (e.g. an editable checkout on ``PYTHONPATH`` with no installed metadata).
"""

from pyramids_lazy.backend import LazyComputeBackend, backend

__all__ = ["LazyComputeBackend", "backend", "get_compute_backend"]

__version__ = "0.1.0"


def get_compute_backend() -> LazyComputeBackend:
    """Return the singleton compute backend (core's first-party fallback entry point).

    ``pyramids.base._compute_backend.require_compute_backend`` calls this by name when no backend is
    registered on the ``pyramids.compute_backend`` entry-point group, so the plugin is usable from a
    bare checkout on ``PYTHONPATH`` as well as from an installed distribution.
    """
    return backend
