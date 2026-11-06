"""Lazy (dask-geopandas) vector reads for FeatureCollection.

Moved out of ``pyramids.feature._read`` (the ``backend="dask"`` branches of ``read_file`` and
``read_parquet``) into the compute backend. Both return a :class:`LazyFeatureCollection`. Core
primitives come through the ``pyramids.lazy_support`` facade; ``dask_geopandas`` / ``pyarrow`` are the
plugin's own dependencies.
"""

from __future__ import annotations

import math
import os
from typing import Any

from pyramids.lazy_support import extra_hint, import_pyarrow

from pyramids_lazy._lazy_collection import LazyFeatureCollection

#: Target bytes per dask partition when the caller gives neither npartitions nor chunksize.
#: ``pyramids.configure_lazy_vector`` tunes this (reconciled with core at the cutover step).
_LAZY_TARGET_BYTES_PER_PARTITION: int = 128 * 1024 * 1024


def _import_dask_geopandas():
    """Import and return ``dask_geopandas`` or raise a pyramids-branded ImportError."""
    try:
        import dask_geopandas
    except ImportError as exc:
        raise ImportError(
            extra_hint(
                "backend='dask' requires the optional 'dask-geopandas' dependency.",
                "parquet",
            )
        ) from exc
    return dask_geopandas


def _require_pyarrow() -> None:
    """Raise a pyramids-branded ImportError if pyarrow is absent."""
    import_pyarrow(
        extra_hint(
            "GeoParquet support requires the optional 'pyarrow' dependency.",
            "parquet",
        )
    )


def _compact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``mapping`` with the ``None``-valued entries removed."""
    return {key: value for key, value in mapping.items() if value is not None}


def _resolve_lazy_partitioning(
    path: str, npartitions: int | None, chunksize: int | None
) -> dict[str, Any]:
    """Default ``npartitions`` from file size when neither knob is given."""
    kwargs: dict[str, Any] = {}
    if npartitions is not None:
        kwargs["npartitions"] = npartitions
    elif chunksize is not None:
        kwargs["chunksize"] = chunksize
    elif path.startswith(("/vsi", "http://", "https://", "s3://", "gs://", "az://")):
        kwargs["npartitions"] = 1
    else:
        try:
            size = os.path.getsize(path)
        except OSError:
            kwargs["npartitions"] = 1
        else:
            kwargs["npartitions"] = max(
                1, math.ceil(size / _LAZY_TARGET_BYTES_PER_PARTITION)
            )
    return kwargs


def read_file_dask(
    resolved: str,
    *,
    layer: str | int | None = None,
    bbox: Any = None,
    mask: Any = None,
    rows: slice | int | None = None,
    columns: list[str] | None = None,
    where: str | None = None,
    npartitions: int | None = None,
    chunksize: int | None = None,
) -> LazyFeatureCollection:
    """Dask backend for ``FeatureCollection.read_file``: reject unsupported filters, wrap as LazyFC."""
    unsupported = {
        "bbox": bbox,
        "mask": mask,
        "rows": rows,
        "columns": columns,
        "where": where,
        "layer": layer,
    }
    supplied = [k for k, v in unsupported.items() if v is not None]
    if supplied:
        raise ValueError(
            f"backend='dask' does not support filter kwargs "
            f"{supplied}. dask_geopandas.read_file has no "
            "pushdown story for these. Either omit them and "
            "filter post-load via .clip / .loc / .compute, or "
            "switch to read_parquet(backend='dask', filters=...)"
        )
    dask_geopandas = _import_dask_geopandas()
    partition_kwargs = _resolve_lazy_partitioning(resolved, npartitions, chunksize)
    dask_gdf = dask_geopandas.read_file(resolved, **partition_kwargs)
    return LazyFeatureCollection.from_dask_gdf(dask_gdf)


def read_parquet_dask(
    resolved: str,
    *,
    columns: list[str] | None = None,
    split_row_groups: bool | None = None,
    filters: list | None = None,
    blocksize: int | str | None = None,
    storage_options: dict | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> LazyFeatureCollection:
    """Dask backend for ``FeatureCollection.read_parquet``: wrap dask_geopandas as a LazyFC."""
    dask_geopandas = _import_dask_geopandas()
    dask_kwargs = _compact(
        {
            "columns": columns,
            "split_row_groups": split_row_groups,
            "filters": filters,
            "blocksize": blocksize,
            "storage_options": storage_options,
        }
    )
    dask_kwargs.update(extra_kwargs or {})
    _require_pyarrow()
    dask_gdf = dask_geopandas.read_parquet(resolved, **dask_kwargs)
    return LazyFeatureCollection.from_dask_gdf(dask_gdf)


__all__ = ["read_file_dask", "read_parquet_dask"]
