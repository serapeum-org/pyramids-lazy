"""Deferred (``dask.delayed``) wrapper for eager pyramids-core Dataset operations.

The ``Dataset`` / ``DatasetCollection`` deferred paths — ``Dataset.to_file(compute=False)`` and the
``Reprojector`` / ``Aligner`` ``compute=False`` calls — wrap an *eager* core body in
:func:`dask.delayed` so its execution can be scheduled into a graph rather than run inline. The eager
bodies stay in pyramids-core (``_write_to_file_sync``, ``_apply_plan``, ``_align_sync``); this module
adds only the ``dask.delayed`` wrapping, so pyramids-core itself carries no ``dask`` import.
"""

from __future__ import annotations

from typing import Any

from pyramids_lazy._optional import lazy_hint

_DASK_MISSING_MESSAGE = lazy_hint(
    "Deferred (compute=False) operations require the 'dask' dependency.", "dask"
)


def delayed(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Wrap an eager callable in :func:`dask.delayed` and apply it to ``args`` / ``kwargs``.

    Args:
        func: A picklable, module-level eager callable from pyramids-core (the synchronous
            file-write or reproject body). It is pickled to the worker by reference, so it must be
            importable there — core keeps these bodies at module scope for exactly this reason.
        *args: Positional arguments forwarded to ``func`` at compute time.
        **kwargs: Keyword arguments forwarded to ``func`` at compute time.

    Returns:
        A :class:`dask.delayed.Delayed` that materialises ``func(*args, **kwargs)`` on ``.compute()``.

    Raises:
        ImportError: When ``dask`` is not installed (the pyramids-lazy install is incomplete).
    """
    try:
        import dask
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    return dask.delayed(func)(*args, **kwargs)
