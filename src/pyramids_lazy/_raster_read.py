"""Lazy (dask) raster-read graph builders for pyramids-core's Dataset.

``Dataset.read_array(chunks=)`` and ``Dataset.map_blocks(chunks=)`` return dask arrays. pyramids-core
prepares the per-chunk reader, the pickle-safe file manager, the chunk lock, and the array
shape/dtype (none of which need dask); this module assembles the dask graph from those pieces, so
pyramids-core carries no ``dask`` import. The per-chunk reader (``ops.io._read_chunk``) is a
pyramids-core module-level function passed in, pickled by reference to dask workers.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from pyramids_lazy._optional import lazy_hint

_DASK_MISSING_MESSAGE = lazy_hint(
    "Lazy raster reads (chunks=) require the 'dask' dependency.", "dask"
)


def read_raster_array_lazy(
    read_chunk: Any,
    *,
    manager: Any,
    chunks: Any,
    shape: tuple[int, ...],
    previous_chunks: Any,
    dtype: Any,
    lock: Any,
    band: int | None,
    single_band: bool,
    gdal_env: dict[str, str] | None,
) -> Any:
    """Build a :class:`dask.array.Array` over a raster band from core-prepared pieces.

    Args:
        read_chunk: pyramids-core's module-level per-chunk reader (``ops.io._read_chunk``), pickled
            by reference to dask workers.
        manager: The pickle-safe ``CachingFileManager`` / ``ThreadLocalFileManager`` core built.
        chunks: The user's chunk spec, as accepted by :func:`dask.array.core.normalize_chunks`.
        shape: The array shape — ``(rows, cols)`` for a single band, ``(bands, rows, cols)`` else.
        previous_chunks: The on-disk block layout, so the default chunking aligns with GDAL's tiles.
        dtype: The band dtype; the output array's dtype.
        lock: The chunk IO lock (a ``SerializableLock`` / ``DummyLock`` / distributed lock).
        band: Zero-based band index for a single-band read, or ``None`` for an all-band 3-D read.
        single_band: ``True`` for a 2-D output, ``False`` for a 3-D one.
        gdal_env: The dataset's captured cloud config, carried into each chunk task, or ``None``.

    Returns:
        A :class:`dask.array.Array` whose blocks materialise through ``read_chunk``.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    try:
        import dask.array as da
        from dask.array.core import normalize_chunks
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    normalized = normalize_chunks(
        chunks, shape=shape, dtype=dtype, previous_chunks=previous_chunks
    )
    meta = np.empty((0,) * len(shape), dtype=dtype)
    return da.map_blocks(
        read_chunk,
        chunks=normalized,
        dtype=dtype,
        meta=meta,
        manager=manager,
        lock=lock,
        band=band,
        out_dtype=dtype,
        single_band=single_band,
        gdal_env=gdal_env,
    )


def map_blocks_lazy(
    func: Any, source: Any, *, dtype: Any, drop_axis: Any = None, new_axis: Any = None
) -> Any:
    """Apply ``func`` block-wise over a lazy raster array via :func:`dask.array.map_blocks`.

    Args:
        func: The per-block callable from the ``Dataset.map_blocks`` caller.
        source: The lazy source array (the ``read_array(chunks=)`` result).
        dtype: The result dtype.
        drop_axis: Axes ``func`` removes, or ``None``.
        new_axis: Axes ``func`` adds, or ``None``.

    Returns:
        A :class:`dask.array.Array` of the mapped result.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    try:
        import dask.array as da
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    kwargs: dict[str, Any] = {"dtype": dtype}
    if drop_axis is not None:
        kwargs["drop_axis"] = drop_axis
    if new_axis is not None:
        kwargs["new_axis"] = new_axis
    return da.map_blocks(func, source, **kwargs)
