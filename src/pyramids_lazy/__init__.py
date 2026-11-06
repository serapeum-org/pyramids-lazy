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

from pyramids_lazy._seam import require_core_seam

# Must precede every submodule import below: each reaches core through `pyramids.lazy_support`, so
# without this an older pyramids-core fails as a raw ModuleNotFoundError naming neither cause nor fix.
require_core_seam()

from pyramids_lazy._lazy_cube import LazyNetCDF  # noqa: E402 - after the seam check
from pyramids_lazy.backend import (  # noqa: E402 - after the seam check, deliberately
    LazyComputeBackend,
    backend,
)

__all__ = [
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
