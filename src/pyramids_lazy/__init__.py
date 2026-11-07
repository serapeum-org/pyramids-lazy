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

The lazy view classes are also public here so core can forward its public ``pyramids.netcdf.LazyNetCDF``
/ ``pyramids.feature.LazyFeatureCollection`` names (and their ``isinstance`` checks) to them.
:class:`LazyNetCDF` imports eagerly (it needs only dask at call time); ``LazyFeatureCollection`` is
resolved lazily via module ``__getattr__`` because its module hard-imports ``dask_geopandas`` (the
``[parquet]`` extra).
"""

from typing import Any

# Importing `_seam` runs the core-contract check, and a straight `import` sorts ahead of the
# `from` imports below — so the check happens before any submodule reaches
# `pyramids.lazy_support`, with no statement between the imports and nothing to silence.
import pyramids_lazy._seam as _seam
from pyramids_lazy._lazy_cube import LazyNetCDF
from pyramids_lazy.backend import LazyComputeBackend, backend

#: The `pyramids.lazy_support` contract this build requires, re-exported for diagnostics.
REQUIRED_LAZY_SUPPORT_API_VERSION = _seam.REQUIRED_LAZY_SUPPORT_API_VERSION

__all__ = [
    "REQUIRED_LAZY_SUPPORT_API_VERSION",
    "LazyComputeBackend",
    "LazyFeatureCollection",
    "LazyNetCDF",
    "backend",
    "get_compute_backend",
]

__version__ = "0.1.0"


def get_compute_backend() -> LazyComputeBackend:
    """Return the singleton compute backend (core's first-party fallback entry point).

    ``pyramids.base._compute_backend.require_compute_backend`` calls this by name when no backend is
    registered on the ``pyramids.compute_backend`` entry-point group, so the plugin is usable from a
    bare checkout on ``PYTHONPATH`` as well as from an installed distribution.
    """
    return backend


def __getattr__(name: str) -> Any:
    """Resolve ``LazyFeatureCollection`` lazily (its module needs the ``[parquet]`` dask-geopandas)."""
    if name == "LazyFeatureCollection":
        from pyramids_lazy._lazy_collection import LazyFeatureCollection

        return LazyFeatureCollection
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
