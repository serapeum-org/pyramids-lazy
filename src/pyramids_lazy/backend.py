"""The pyramids compute backend: the lazy/dask operations core delegates to.

``pyramids-lazy`` registers the module-level :data:`backend` singleton under the
``pyramids.compute_backend`` entry-point group. ``pyramids-core`` discovers it through
``pyramids.base._compute_backend.require_compute_backend`` and routes every lazy/dask operation
through it, so the dask/zarr stack stays out of a bare ``pyramids-core`` install.

The method set mirrors the ``ComputeBackend`` Protocol core publishes -- that Protocol is the
authoritative contract. Bodies are moved here from core one operation at a time; a method still
raising :class:`NotImplementedError` marks an operation core has not yet handed over, and core does
not dispatch to it until it has, so no such placeholder is ever reached through the seam.
"""

from __future__ import annotations

from typing import Any

#: The contract version this backend implements. Must stay ``>=`` core's
#: ``COMPUTE_BACKEND_API_VERSION``; additive Protocol methods do not bump it, a breaking change does.
API_VERSION = 1

_WIP = "pyramids-lazy has not yet taken {op!r} over from pyramids-core"


class LazyComputeBackend:
    """Concrete ``pyramids.compute_backend`` backend for the dask/zarr lazy layer.

    See ``pyramids.base._compute_backend.ComputeBackend`` for the authoritative method contract and
    signatures; every method here mirrors it one-for-one.
    """

    api_version: int = API_VERSION

    def read_array_lazy(
        self, dataset: Any, *, chunks: Any, lock: Any = None, **kwargs: Any
    ) -> Any:
        """Lazy dask-backed read of a raster/variable (``Dataset``/``NetCDF`` ``read_array``)."""
        raise NotImplementedError(_WIP.format(op="read_array_lazy"))

    def build_lazy_cube(self, netcdf: Any, chunks: Any) -> Any:
        """Build the lazy cube view for ``NetCDF.chunk``."""
        raise NotImplementedError(_WIP.format(op="build_lazy_cube"))

    def to_dask_dataframe(
        self, netcdf: Any, *, variables: Any, dropna: bool, chunks: Any
    ) -> Any:
        """Back ``NetCDF.to_dask_dataframe``."""
        raise NotImplementedError(_WIP.format(op="to_dask_dataframe"))

    def to_kerchunk(self, source: Any, output_path: Any, **kwargs: Any) -> dict:
        """Back ``NetCDF.to_kerchunk`` (single-file kerchunk manifest)."""
        raise NotImplementedError(_WIP.format(op="to_kerchunk"))

    def combine_kerchunk(self, paths: Any, output_path: Any, **kwargs: Any) -> dict:
        """Back ``NetCDF.combine_kerchunk`` / ``DatasetCollection.to_kerchunk``."""
        raise NotImplementedError(_WIP.format(op="combine_kerchunk"))

    def open_mfdataset(self, paths: Any, variable: Any, **kwargs: Any) -> Any:
        """Back ``NetCDF.open_mfdataset`` (dask-stacked multi-file read)."""
        raise NotImplementedError(_WIP.format(op="open_mfdataset"))

    def write_dataset_to_zarr(self, dataset: Any, store: Any, **kwargs: Any) -> Any:
        """Back ``Dataset.to_zarr``."""
        from pyramids_lazy._zarr import write_dataset_to_zarr

        return write_dataset_to_zarr(dataset, store, **kwargs)

    def read_dataset_from_zarr(self, store: Any, **kwargs: Any) -> Any:
        """Back ``Dataset.from_zarr`` (returns a ``Dataset``)."""
        from pyramids_lazy._zarr import read_dataset_from_zarr

        return read_dataset_from_zarr(store, **kwargs)

    def write_collection_to_zarr(
        self, collection: Any, store: Any, **kwargs: Any
    ) -> Any:
        """Back ``DatasetCollection.to_zarr``."""
        raise NotImplementedError(_WIP.format(op="write_collection_to_zarr"))

    def read_collection_from_zarr(
        self, collection_cls: Any, store: Any, **kwargs: Any
    ) -> Any:
        """Back ``DatasetCollection.from_zarr`` (returns a ``DatasetCollection``)."""
        raise NotImplementedError(_WIP.format(op="read_collection_from_zarr"))

    def read_vector_lazy(self, source: Any, **kwargs: Any) -> Any:
        """Back ``FeatureCollection.read_file(backend="dask")``."""
        raise NotImplementedError(_WIP.format(op="read_vector_lazy"))

    def read_vector_parquet_lazy(self, source: Any, **kwargs: Any) -> Any:
        """Back ``FeatureCollection.read_parquet(backend="dask")``."""
        raise NotImplementedError(_WIP.format(op="read_vector_parquet_lazy"))


#: The singleton core resolves through the entry point. A single object, per the Protocol contract.
backend = LazyComputeBackend()
