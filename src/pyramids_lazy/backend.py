"""The pyramids compute backend: the lazy/dask operations core delegates to.

``pyramids-lazy`` registers the module-level :data:`backend` singleton under the
``pyramids.compute_backend`` entry-point group. ``pyramids-core`` discovers it through
``pyramids.base._compute_backend.require_compute_backend`` and routes every lazy/dask operation
through it, so the dask/zarr stack stays out of a bare ``pyramids-core`` install.

The method set mirrors the ``ComputeBackend`` Protocol core publishes -- that Protocol is the
authoritative contract. All v1 operations are implemented: each method forwards to the relocated body
in a sibling ``pyramids_lazy`` module (``_zarr``, ``_collection_zarr``, ``_vector_read``, ``_lazy``,
``_mfdataset``, ``_kerchunk_facade``, ``_lazy_cube``, ``_to_dask_dataframe``), which import every core
primitive through the ``pyramids.lazy_support`` facade.
"""

from __future__ import annotations

from typing import Any

#: The contract version this backend implements. Must stay ``>=`` core's
#: ``COMPUTE_BACKEND_API_VERSION``; additive Protocol methods do not bump it, a breaking change does.
#: v2: the required ``lazy_netcdf_type`` / ``lazy_feature_collection_type`` accessors (core's
#: public-class forwarding seam), published as methods below.
API_VERSION = 2


# Each op method imports its body module function-locally on purpose: `import pyramids_lazy`
# eagerly pulls only `_lazy_cube` (for LazyNetCDF), so deferring the rest keeps the heavier op
# modules and their deps (kerchunk, dask-geopandas, pyarrow) out of import until the op runs.
# Not for a cycle -- there is none -- so no `# noqa`; it is a load-cost choice.
class LazyComputeBackend:
    """Concrete ``pyramids.compute_backend`` backend for the dask/zarr lazy layer.

    See ``pyramids.base._compute_backend.ComputeBackend`` for the authoritative method contract and
    signatures; every method here mirrors it one-for-one.
    """

    api_version: int = API_VERSION

    def lazy_netcdf_type(self) -> type:
        """Return the class :meth:`build_lazy_cube` produces.

        Core forwards its public ``pyramids.netcdf.LazyNetCDF`` name (and the ``isinstance`` checks
        that go with it) to this, instead of importing ``pyramids_lazy`` itself, so the entry-point
        seam stays honest about which backend is installed.
        """
        from pyramids_lazy._lazy_cube import LazyNetCDF

        return LazyNetCDF

    def lazy_feature_collection_type(self) -> type:
        """Return the class the lazy vector reads produce.

        Backs core's public ``pyramids.feature.LazyFeatureCollection`` and ``is_lazy_fc``.

        A method, NOT a property: ``runtime_checkable`` isinstance evaluates data members with
        ``getattr``, so as a property this body ran whenever core merely type-checked the backend.
        Importing the module needs ``dask-geopandas`` (the ``[parquet]`` extra), so that made the
        whole backend fail its structural check in every env without that extra -- and core, which
        treats a failing backend as one to skip, then fell back to eager execution for raster and
        cube work that never needed vectors at all. As a method the import is deferred to the call,
        where the branded hint is the right answer.
        """
        from pyramids_lazy._lazy_collection import LazyFeatureCollection

        return LazyFeatureCollection

    def read_array_lazy(
        self, dataset: Any, *, chunks: Any, lock: Any = None, **kwargs: Any
    ) -> Any:
        """Lazy dask-backed read of a raster/variable (``Dataset``/``NetCDF`` ``read_array``).

        ``dataset`` is accepted for the Protocol; the object-specific prep (path, variable name,
        spatial-dim orientation, manager hook, cloud env) is done by core and forwarded in
        ``kwargs`` to :func:`build_lazy_array`.
        """
        from pyramids_lazy._lazy import build_lazy_array

        return build_lazy_array(chunks=chunks, lock=lock, **kwargs)

    def build_lazy_cube(self, netcdf: Any, chunks: Any) -> Any:
        """Build the lazy cube view for ``NetCDF.chunk`` (returns a ``LazyNetCDF``)."""
        from pyramids_lazy._lazy_cube import build_lazy_cube

        return build_lazy_cube(netcdf, chunks)

    def to_dask_dataframe(
        self, netcdf: Any, *, variables: Any, dropna: bool, chunks: Any
    ) -> Any:
        """Back ``NetCDF.to_dask_dataframe``."""
        from pyramids_lazy._to_dask_dataframe import to_dask_dataframe

        return to_dask_dataframe(
            netcdf, variables=variables, dropna=dropna, chunks=chunks
        )

    def to_kerchunk(self, source: Any, output_path: Any, **kwargs: Any) -> dict:
        """Back ``NetCDF.to_kerchunk`` (single-file kerchunk manifest)."""
        from pyramids_lazy._kerchunk_facade import to_kerchunk

        return to_kerchunk(source, output_path, **kwargs)

    def combine_kerchunk(self, paths: Any, output_path: Any, **kwargs: Any) -> dict:
        """Back ``NetCDF.combine_kerchunk`` / ``DatasetCollection.to_kerchunk``."""
        from pyramids_lazy._kerchunk_facade import combine_kerchunk

        return combine_kerchunk(paths, output_path, **kwargs)

    def open_mfdataset(self, paths: Any, variable: Any, **kwargs: Any) -> Any:
        """Back ``NetCDF.open_mfdataset`` (dask-stacked multi-file read)."""
        from pyramids_lazy._mfdataset import open_mfdataset

        return open_mfdataset(paths, variable, **kwargs)

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
        from pyramids_lazy._collection_zarr import write_collection_to_zarr

        return write_collection_to_zarr(collection, store, **kwargs)

    def read_collection_from_zarr(
        self, collection_cls: Any, store: Any, **kwargs: Any
    ) -> Any:
        """Back ``DatasetCollection.from_zarr`` (returns a ``DatasetCollection``)."""
        from pyramids_lazy._collection_zarr import read_collection_from_zarr

        return read_collection_from_zarr(collection_cls, store, **kwargs)

    def read_vector_lazy(self, source: Any, **kwargs: Any) -> Any:
        """Back ``FeatureCollection.read_file(backend="dask")``."""
        from pyramids_lazy._vector_read import read_file_dask

        return read_file_dask(source, **kwargs)

    def read_vector_parquet_lazy(self, source: Any, **kwargs: Any) -> Any:
        """Back ``FeatureCollection.read_parquet(backend="dask")``."""
        from pyramids_lazy._vector_read import read_parquet_dask

        return read_parquet_dask(source, **kwargs)


#: The singleton core resolves through the entry point. A single object, per the Protocol contract.
backend = LazyComputeBackend()
