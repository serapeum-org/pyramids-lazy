"""Lazy (dask) overlap wrapper for pyramids-core's Dataset focal filters.

pyramids-core's focal filters (``focal_mean``, slope, aspect, hillshade, ...) run a per-block kernel
that blanks no-data to NaN, filters, and restores the sentinel. The eager path runs the kernel
directly; the lazy path tiles it with :func:`dask.array.map_overlap` so each interior block sees the
halo it needs. Core builds the no-data-aware kernel closure and reads the (lazy) source band; this
module adds only the ``map_overlap`` wrapping, so pyramids-core carries no ``dask`` import.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from pyramids.lazy_support import is_lazy

from pyramids_lazy._optional import lazy_hint

_DASK_MISSING_MESSAGE = lazy_hint(
    "Lazy focal filters (chunks=) require the 'dask' dependency.", "dask"
)


def focal_lazy(
    source: Any,
    guarded: Callable[[np.ndarray], np.ndarray],
    *,
    depth: int,
    dtype: Any,
) -> Any:
    """Tile a no-data-aware focal kernel over a band with :func:`dask.array.map_overlap`.

    Args:
        source: The band to filter — a :class:`dask.array.Array` (the lazy ``read_array(chunks=)``
            result) or an array-like, which is wrapped with ``da.from_array`` when not already lazy.
        guarded: The per-block kernel from core, ``guarded(block_2d) -> block_2d`` of the same shape,
            which blanks no-data to NaN, runs the filter, and restores the band's sentinel.
        depth: The overlap halo in pixels; must cover the kernel's footprint.
        dtype: The output dtype; the source is cast to it before filtering.

    Returns:
        A :class:`dask.array.Array` of the filtered band, materialising block-by-block on
        ``.compute()``.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    try:
        import dask.array as da
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    lazy = source
    if not is_lazy(lazy):
        lazy = da.from_array(np.asarray(lazy), chunks="auto")
    lazy = lazy.astype(dtype)
    # boundary="none": only interior block edges get a halo, so the kernels apply their own edge
    # policy on the raster border exactly as the eager path does. trim restores the original shape.
    return lazy.map_overlap(
        guarded, depth=depth, boundary="none", trim=True, dtype=dtype
    )
