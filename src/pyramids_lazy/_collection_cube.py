"""Lazy (dask) cube primitives for pyramids-core's DatasetCollection.

``DatasetCollection.data`` returns a lazy ``(T, B, R, C)`` dask cube: per-timestep spatially-tiled
arrays (built by the shared raster reader) stacked over time, a resident-scene stack for an in-memory
collection, or a direct read of a Zarr-backed cube. pyramids-core orchestrates which path applies and
prepares each timestep; this module provides the three dask primitives those paths need (``stack``,
``from_array``, ``from_zarr``), so pyramids-core carries no ``dask`` import.
"""

from __future__ import annotations

from typing import Any

from pyramids_lazy._optional import lazy_hint

_DASK_MISSING_MESSAGE = lazy_hint(
    "DatasetCollection.data requires the 'dask' dependency.", "dask"
)


def _dask_array() -> Any:
    """Return the ``dask.array`` module, or raise the branded install hint."""
    try:
        import dask.array as da
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    return da


def stack_lazy(arrays: Any, *, axis: int = 0) -> Any:
    """Stack lazy per-timestep arrays along a new axis via :func:`dask.array.stack`.

    Args:
        arrays: The per-timestep lazy arrays to stack (an iterable).
        axis: The new axis to stack along; ``0`` for the collection's time axis.

    Returns:
        A :class:`dask.array.Array` with one extra leading axis.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    return _dask_array().stack(list(arrays), axis=axis)


def from_array_lazy(array: Any, *, chunks: Any) -> Any:
    """Wrap an in-memory array as a lazy array via :func:`dask.array.from_array`.

    Args:
        array: The resident NumPy array for one timestep.
        chunks: The chunk spec; the whole-scene shape wraps it as a single chunk.

    Returns:
        A :class:`dask.array.Array` backed by ``array``.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    return _dask_array().from_array(array, chunks=chunks)


def read_zarr_cube(store: Any, *, component: str = "data") -> Any:
    """Read a Zarr-backed ``(T, B, R, C)`` cube lazily via :func:`dask.array.from_zarr`.

    Args:
        store: The Zarr store a ``from_zarr``-built collection holds.
        component: The array component inside the store; the cube is at ``"data"``.

    Returns:
        A :class:`dask.array.Array` over the stored cube.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    return _dask_array().from_zarr(store, component=component)
