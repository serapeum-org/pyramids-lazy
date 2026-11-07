"""Zarr read / write for :class:`~pyramids.dataset.collection.DatasetCollection`.

Moved out of ``pyramids.dataset.collection`` (the ``to_zarr`` / ``from_zarr`` methods and their
lazy-only module helpers) into the compute backend. The 4-D ``(time, band, y, x)`` cube is written
one dask chunk per Zarr chunk -- the only truly parallel raster output path -- with the pyramids
geobox persisted as GeoZarr attributes so standard readers can reconstruct it.

Every core primitive is imported through the ``pyramids.lazy_support`` facade (never core internals).
The functions operate on the ``DatasetCollection`` object / class handed in by the backend, reading
its ``data`` / ``_meta`` / ``_files`` state, exactly as the original methods did through ``self`` /
``cls``.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, cast

import numpy as np
from pyramids.dataset import Dataset
from pyramids.lazy_support import (
    ZARR_SCHEMA_VERSION,
    GeoReference,
    RasterMeta,
    _resolve_store,
    finalize_zarr_metadata,
    geobox_crs,
    import_zarr,
    lazy_extra_hint,
    normalize_compressors,
    read_geobox,
)


def _finalize_collection_metadata(resolved_store: Any, meta: Any, files: list) -> None:
    """Write pyramids GeoTransform / CRS root attrs on a freshly-written cube Zarr.

    Module-level so the :func:`dask.delayed` path can pickle it cleanly. Sets ``crs_wkt``,
    ``GeoTransform``, ``epsg``, ``nodata``, ``band_names``, ``time_length`` + a pyramids version
    marker on the ``data`` array + root group.
    """
    finalize_zarr_metadata(
        resolved_store,
        root_attrs={
            "pyramids_zarr_version": ZARR_SCHEMA_VERSION,
            "time_length": int(len(files)),
            "pyramids_file_list": list(files),
        },
        data_attrs={
            # 0 is the geobox's documented "no authority code" sentinel; emit it here too so a
            # store does not record absence two ways.
            "epsg": int(meta.epsg) if meta.epsg is not None else 0,
            "GeoTransform": " ".join(str(v) for v in meta.geotransform),
            "crs_wkt": meta.crs.to_wkt() if meta.crs is not None else "",
            "nodata": [None if v is None else float(v) for v in meta.nodata],
            "band_names": list(meta.band_names) if meta.band_names else [],
            "dtype": str(meta.dtype),
        },
        epsg=int(meta.epsg) if meta.epsg is not None else None,
        geotransform=tuple(float(v) for v in meta.geotransform),
        crs_wkt=meta.crs.to_wkt() if meta.crs is not None else "",
        rows=int(meta.rows),
        cols=int(meta.columns),
        dims=["time", "band", "y", "x"],
    )


def _finalize_append_metadata(
    resolved_store: Any, new_time_length: int, added_files: list
) -> None:
    """Update root attrs after appending timesteps to an existing cube store.

    Bumps ``time_length`` to the new total and extends ``pyramids_file_list``; the geobox / data
    attrs already exist from the initial write. Module-level so the deferred (``compute=False``) path
    can pickle it.
    """
    import zarr

    root = zarr.open_group(resolved_store, mode="a")
    root.attrs["time_length"] = int(new_time_length)
    existing_files = list(cast(list, root.attrs.get("pyramids_file_list", [])))
    root.attrs["pyramids_file_list"] = existing_files + list(added_files)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Consolidated metadata is currently not part"
        )
        zarr.consolidate_metadata(resolved_store)


def _append_region(
    data: Any,
    resolved_store: Any,
    old_t: int,
    new_total: int,
    slices: Any,
    added_files: list,
) -> None:
    """Atomically grow, stream-write, and finalize a zarr time-append.

    The deferred (``compute=False``) append routes through this single task so the store's ``data``
    shape only grows when the append actually runs; on any failure the resize is rolled back. ``data``
    stays a lazy dask array and is streamed into the region via ``to_zarr(..., compute=False)`` driven
    with ``scheduler="synchronous"`` so a nested compute cannot deadlock under a threaded /
    distributed outer scheduler. Idempotent under recompute.
    """
    import zarr

    root = zarr.open_group(resolved_store, mode="a")
    arr = root["data"]
    if not isinstance(arr, zarr.Array):
        raise TypeError(f"expected a zarr Array at 'data' in {resolved_store!r}")
    if int(arr.shape[0]) == new_total:
        return
    arr.resize((new_total, *arr.shape[1:]))
    try:
        store_task = data.to_zarr(arr, region=slices, overwrite=False, compute=False)
        store_task.compute(scheduler="synchronous")
        _finalize_append_metadata(resolved_store, new_total, added_files)
    except Exception:
        arr.resize((old_t, *arr.shape[1:]))
        raise


def _region_to_slices(region: dict, ndim: int) -> tuple:
    """Convert a ``{dim: slice}`` region dict to a positional slice tuple.

    Maps the cube's named axes (``time``/``band``/``y``/``x``) to positions 0..3; unmentioned dims
    default to ``slice(None)`` (the whole axis).
    """
    dim_axis = {"time": 0, "band": 1, "y": 2, "x": 3}
    slices: list = [slice(None)] * ndim
    for dim, sl in region.items():
        if dim not in dim_axis:
            raise ValueError(
                f"unknown region dim {dim!r}; expected one of {tuple(dim_axis)}"
            )
        slices[dim_axis[dim]] = sl
    return tuple(slices)


def _finalize_after_write(
    data_result: Any, resolved_store: Any, meta: Any, files: list
) -> None:
    """Run metadata finalize AFTER the data write completes.

    Wrapping both in one :func:`dask.delayed` makes the dependency explicit: the metadata write
    cannot start until ``data_result`` is materialised, so there is no race.
    """
    del data_result  # consumed as a dependency only
    _finalize_collection_metadata(resolved_store, meta, files)


def _append_to_zarr(
    collection: Any, resolved_store: Any, data: Any, append_dim: str, compute: bool
) -> Any:
    """Append this cube's timesteps to an existing store along ``append_dim``.

    Resizes the existing ``data`` array along the time axis and writes the new block into the
    appended region, then bumps ``time_length`` and extends ``pyramids_file_list``.
    """
    import dask
    import zarr

    if collection._files is None:
        raise ValueError(
            "append-to-zarr needs the collection's file list; it is None, which means the "
            "collection was not built from files."
        )

    if append_dim != "time":
        raise ValueError(
            f"append_dim must be 'time' for a (T, B, Y, X) cube; got {append_dim!r}"
        )
    root = zarr.open_group(resolved_store, mode="a")
    existing = root["data"]
    if not isinstance(existing, zarr.Array):
        raise TypeError(
            f"expected the 'data' node in {resolved_store!r} to be a zarr "
            f"Array, got a Group -- the store is not a pyramids cube"
        )
    old_t = int(existing.shape[0])
    new_total = old_t + int(data.shape[0])
    slices = (slice(old_t, new_total),) + (slice(None),) * (data.ndim - 1)

    if compute:
        existing.resize((new_total, *existing.shape[1:]))
        try:
            data.to_zarr(existing, region=slices, overwrite=False, compute=True)
            _finalize_append_metadata(resolved_store, new_total, collection._files)
        except Exception:
            existing.resize((old_t, *existing.shape[1:]))
            raise
        return None

    files = collection._files

    @dask.delayed
    def _deferred_append() -> None:
        _append_region(data, resolved_store, old_t, new_total, slices, files)

    return _deferred_append()


def write_collection_to_zarr(
    collection: Any,
    store: str | Path | Any,
    *,
    compute: bool = True,
    mode: str = "w",
    storage_options: dict | None = None,
    compressor: Any = "auto",
    append_dim: str | None = None,
    region: dict | None = None,
) -> Any:
    """Serialise the 4-D ``(T, B, R, C)`` cube of ``collection`` to a Zarr store.

    Each dask chunk in ``collection.data`` lands in an independent Zarr chunk file. Geobox metadata
    is written as attributes on the root group + the ``data`` array. See
    :meth:`DatasetCollection.to_zarr` for the full argument contract.
    """
    files = collection._require_files("to_zarr")
    import_zarr(
        lazy_extra_hint(
            "DatasetCollection.to_zarr requires the optional 'zarr' dependency."
        )
    )
    if mode == "a" and append_dim is None and region is None:
        raise ValueError(
            "mode='a' requires append_dim='time' or region=... (append_dim / "
            "region semantics); use mode='w' to (over)write the whole cube."
        )
    data = collection.data
    resolved_store = _resolve_store(store, storage_options)
    codec_kwargs = normalize_compressors(compressor)

    if append_dim is not None:
        return _append_to_zarr(collection, resolved_store, data, append_dim, compute)
    if region is not None:
        import zarr

        existing = zarr.open_group(resolved_store, mode="a")["data"]
        return data.to_zarr(
            existing,
            region=_region_to_slices(region, data.ndim),
            overwrite=False,
            compute=compute,
        )

    write_result = data.to_zarr(
        resolved_store,
        component="data",
        overwrite=(mode == "w"),
        compute=compute,
        **codec_kwargs,
    )
    if compute:
        _finalize_collection_metadata(resolved_store, collection._meta, files)
        result: Any = None
    else:
        import dask

        result = dask.delayed(_finalize_after_write)(
            write_result,
            resolved_store,
            collection._meta,
            files,
        )
    return result


def read_collection_from_zarr(
    collection_cls: Any,
    store: str | Path | Any,
    *,
    storage_options: dict | None = None,
) -> Any:
    """Open a pyramids-written cube Zarr store into a lazy ``DatasetCollection``.

    Inverse of :func:`write_collection_to_zarr`. The 4-D ``(time, band, y, x)`` ``data`` array is
    read lazily straight from the store, and the geobox is recovered from the GeoZarr ``spatial_ref``
    mapping. ``collection_cls`` is the ``DatasetCollection`` class to construct.
    """
    import_zarr(
        lazy_extra_hint(
            "DatasetCollection.from_zarr requires the optional 'zarr' dependency."
        )
    )
    import zarr

    resolved = _resolve_store(store, storage_options)
    root = zarr.open_group(resolved, mode="r")
    data_arr = root["data"]
    if not isinstance(data_arr, zarr.Array):
        raise TypeError(
            f"expected the 'data' node in {resolved!r} to be a zarr Array, "
            f"got a Group -- the store is not a pyramids cube"
        )
    data_attrs = dict(data_arr.attrs)
    geobox = read_geobox(root, data_name="data")
    time_length, bands, rows, cols = (int(v) for v in data_arr.shape)
    time_length = int(
        cast("int | float | str", root.attrs.get("time_length", time_length))
    )

    nodata_list = cast("list | None", data_attrs.get("nodata"))
    if nodata_list and any(v is not None for v in nodata_list):
        no_data_value: Any = list(nodata_list)
    else:
        no_data_value = None
    dtype = np.dtype(cast(str, data_attrs.get("dtype", "float32")))
    template_arr = np.zeros((bands, rows, cols), dtype=dtype)
    geo_6 = cast(
        "tuple[float, float, float, float, float, float]",
        tuple(float(v) for v in geobox["geotransform"]),
    )
    template = Dataset.from_array(
        template_arr if bands > 1 else template_arr[0],
        no_data_value=no_data_value,
        geo_ref=GeoReference(geo=geo_6, epsg=geobox_crs(geobox)),
    )
    if geobox["crs_wkt"]:
        template.crs = geobox["crs_wkt"]
    template.bands.apply_names(
        cast("list | None", data_attrs.get("band_names")),
        source="cube Zarr store",
    )
    meta = RasterMeta.from_dataset(template)
    return collection_cls(template, time_length, meta=meta, zarr_store=resolved)


__all__ = ["write_collection_to_zarr", "read_collection_from_zarr"]
