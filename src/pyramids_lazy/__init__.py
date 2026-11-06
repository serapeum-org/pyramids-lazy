"""pyramids-lazy: the lazy / dask compute backend for pyramids.

This package implements the ``ComputeBackend`` contract published by ``pyramids-core`` and registers
it under the ``pyramids.compute_backend`` entry-point group. Installing ``pyramids-lazy`` alongside
``pyramids-core`` turns on every lazy/dask operation (raster, raster-cube and vector) without the
dask/zarr stack ever being pulled into a bare ``pyramids-core`` install.
"""

from pyramids_lazy.backend import LazyComputeBackend, backend

__all__ = ["LazyComputeBackend", "backend"]

__version__ = "0.1.0"
