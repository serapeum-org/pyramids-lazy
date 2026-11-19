"""Lazy (dask) time-axis reduction builders for pyramids-core's DatasetCollection.

``DatasetCollection.reduce_time`` / ``mean`` / ``sum`` / ... reduce the lazy ``(T, B, R, C)`` cube over
the time axis. The six associative ops are :mod:`dask.array` reductions resolved by
:func:`resolve_dask_op`; ``median`` / ``quantile`` / ``percentile`` reuse the shared core
``reduce_axis``; ``mode`` and ``trend`` / ``sens_slope`` are numpy kernels mapped over the rechunked
time axis. pyramids-core builds the per-window label arrays and timestamps (pure numpy / pandas) and
delegates the lazy reduction and the single-pass :func:`dask.compute` here, so pyramids-core carries
no ``dask`` import.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from pyramids.lazy_support import _NAN_TABLE, _SUPPORTED_OPS, reduce_axis

from pyramids_lazy._optional import lazy_hint

_DASK_MISSING_MESSAGE = lazy_hint(
    "DatasetCollection reductions require the 'dask' dependency.", "dask"
)


def _dask_array() -> Any:
    """Return the ``dask.array`` module, or raise the branded install hint."""
    try:
        import dask.array as da
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    return da


def compute(*arrays: Any) -> Any:
    """Evaluate several lazy arrays in a single pass via :func:`dask.compute`.

    Args:
        *arrays: The per-window lazy reductions to evaluate together, so each source chunk is read
            once regardless of how the windows interleave.

    Returns:
        A tuple of the materialised results, in the order given.

    Raises:
        ImportError: When ``dask`` is not installed.
    """
    try:
        import dask
    except ImportError as exc:
        raise ImportError(_DASK_MISSING_MESSAGE) from exc
    return dask.compute(*arrays)


def resolve_dask_op(op_name: str, *, skipna: bool) -> Callable[..., Any]:
    """Return the :mod:`dask.array` callable for a reduction.

    Centralises the ``nan{op} if skipna else op`` name mangling and the ``getattr(dask.array, name)``
    resolution so every call site agrees on the supported ops and the nan-aware naming convention.

    Args:
        op_name: One of ``mean``, ``sum``, ``min``, ``max``, ``std``, ``var``.
        skipna: When True, return the nan-aware variant (e.g. ``nanmean``); otherwise the plain op.

    Returns:
        The resolved :mod:`dask.array` callable (e.g. ``dask.array.nanmean``).

    Raises:
        ValueError: If ``op_name`` is outside the supported set.
        ImportError: If the optional ``dask`` dependency is not installed.
    """
    if op_name not in _SUPPORTED_OPS:
        raise ValueError(
            f"Unsupported reduction {op_name!r}; supported: {_SUPPORTED_OPS}"
        )
    da = _dask_array()
    name = _NAN_TABLE[op_name] if skipna else op_name
    # getattr on a module is untyped; every name in _NAN_TABLE / _SUPPORTED_OPS
    # names a real dask.array reduction function.
    return cast("Callable[..., Any]", getattr(da, name))


def _mode_axis0(block: Any) -> Any:
    """Per-pixel modal value over axis 0, NaN-aware, smallest value on ties.

    Sorts each pixel column ascending (NaN sorts to the end), then walks the time axis once
    accumulating equal-value run lengths. Because equal values are adjacent after the sort, the
    longest run is the most frequent value; the strict ``>`` update keeps the first (hence smallest)
    value when two values tie on count. An all-gap column yields ``NaN``.

    Args:
        block: A ``(n_t, ...)`` array; axis 0 is the time axis to reduce.

    Returns:
        The ``(...)`` per-pixel modal array (float64).
    """
    arr = np.asarray(block, dtype="float64")
    n_t = arr.shape[0]
    flat = arr.reshape(n_t, -1)
    srt = np.sort(flat, axis=0)
    best_val = np.full(flat.shape[1], np.nan)
    best_len = np.zeros(flat.shape[1], dtype=np.int64)
    cur_val = srt[0].copy()
    cur_len = np.where(np.isnan(srt[0]), 0, 1).astype(np.int64)
    better = cur_len > best_len
    best_len = np.where(better, cur_len, best_len)
    best_val = np.where(better, cur_val, best_val)
    for i in range(1, n_t):
        row = srt[i]
        same = row == cur_val  # NaN compares False, so a gap breaks the run
        cur_len = np.where(same, cur_len + 1, np.where(np.isnan(row), 0, 1))
        cur_val = np.where(same, cur_val, row)
        better = cur_len > best_len  # strict: the earliest (smallest) run wins a tie
        best_len = np.where(better, cur_len, best_len)
        best_val = np.where(better, cur_val, best_val)
    return best_val.reshape(arr.shape[1:])


def _ols_slope(y: Any, x: Any, *, skipna: bool) -> Any:
    """Per-pixel ordinary-least-squares slope of ``y`` against ``x`` over axis 0."""
    valid = ~np.isnan(y)
    if not skipna:
        valid = valid & valid.all(axis=0)  # any gap invalidates the whole pixel
    count = valid.sum(axis=0)
    xm = np.where(valid, x, np.nan)
    ym = np.where(valid, y, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        x_dev = xm - np.nanmean(xm, axis=0)
        y_dev = ym - np.nanmean(ym, axis=0)
        num = np.nansum(np.where(valid, x_dev * y_dev, 0.0), axis=0)
        den = np.nansum(np.where(valid, x_dev * x_dev, 0.0), axis=0)
        slope = np.where((count >= 2) & (den > 0), num / den, np.nan)
    return slope


def _theil_sen_slope(y: Any, x: Any, *, skipna: bool) -> Any:
    """Per-pixel Theil–Sen slope: the median of pairwise slopes over axis 0."""
    n_t = y.shape[0]
    lower, upper = np.triu_indices(n_t, k=1)
    dx = x[upper] - x[lower]
    dy = y[upper] - y[lower]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        pair_slopes = np.where(dx == 0, np.nan, dy / dx)
        slope = np.nanmedian(pair_slopes, axis=0)
    if not skipna:
        slope = np.where(np.isnan(y).any(axis=0), np.nan, slope)
    return slope


def _trend_block(block: Any, *, x: Any, method: str, skipna: bool) -> Any:
    """Reduce axis 0 of ``block`` to a per-pixel slope against timestamps ``x``.

    Args:
        block: A ``(n_t, ...)`` array; axis 0 is the time axis.
        x: The ``(n_t,)`` numeric timestamps (day offsets) for this window.
        method: ``"trend"`` for least-squares, ``"sens_slope"`` for Theil–Sen.
        skipna: When True, fit on each pixel's non-gap timesteps (needs >= 2); when False, any gap
            makes that pixel ``NaN``.

    Returns:
        The ``(...)`` per-pixel slope array (float64), in value-units per day.
    """
    arr = np.asarray(block, dtype="float64")
    n_t = arr.shape[0]
    y = arr.reshape(n_t, -1)
    x_col = np.asarray(x, dtype="float64").reshape(n_t, 1)
    if method == "sens_slope":
        slope = _theil_sen_slope(y, x_col, skipna=skipna)
    else:
        slope = _ols_slope(y, x_col, skipna=skipna)
    return slope.reshape(arr.shape[1:])


def build_group_reduction(
    group: Any,
    op: str,
    *,
    skipna: bool,
    q: float | None = None,
    x: Any = None,
    min_count: int = 0,
) -> Any:
    """Build the lazy :mod:`dask.array` reduction of ``group`` over axis 0.

    One dispatcher so :meth:`DatasetCollection.reduce_time` can batch every window's reduction into a
    single :func:`dask.compute`, whatever the op. The six associative ops go through
    :func:`resolve_dask_op`; ``median`` / ``quantile`` / ``percentile`` reuse the shared
    ``reduce_axis`` (NaN / no-data-aware, percentile mapped onto the ``[0, 1]`` quantile); ``mode`` and
    ``trend`` / ``sens_slope`` are numpy kernels mapped over the time axis after rechunking it into a
    single block.

    Args:
        group: The window's lazy ``(n_t, band, rows, cols)`` dask slice.
        op: One of core's ``TIME_REDUCE_OPS``.
        skipna: Ignore the no-data value / NaN in the reduction.
        q: The level for ``percentile`` (``0 < q <= 100``) / ``quantile`` (``0 < q <= 1``); ``None``
            otherwise.
        x: The window's ``(n_t,)`` numeric day offsets, for the trend ops only.
        min_count: When above ``0`` and ``skipna`` is ``True``, a cell whose window holds fewer than
            ``min_count`` valid (non-NaN) samples is set to ``NaN``. ``0`` (default) gates nothing,
            and the gate is skipped under ``skipna=False``.

    Returns:
        The lazy ``(band, rows, cols)`` dask reduction for this window.

    Raises:
        ImportError: If the optional ``dask`` dependency is not installed.
    """
    da = _dask_array()
    if op in _SUPPORTED_OPS:
        result = resolve_dask_op(op, skipna=skipna)(group, axis=0)
    elif op in ("median", "quantile", "percentile"):
        if op == "median":
            frac = None
        else:
            # core's validate_time_op guarantees q is set for quantile / percentile.
            assert q is not None  # nosec B101
            frac = q / 100.0 if op == "percentile" else q
        how = "median" if op == "median" else "quantile"
        result = reduce_axis(group, 0, how, skipna, None, frac)
    elif op == "mode":
        result = da.map_blocks(
            _mode_axis0, group.rechunk({0: -1}), drop_axis=0, dtype="float64"
        )
    else:  # trend / sens_slope
        result = da.map_blocks(
            _trend_block,
            group.rechunk({0: -1}),
            drop_axis=0,
            dtype="float64",
            x=np.asarray(x, dtype="float64"),
            method=op,
            skipna=skipna,
        )
    return _gate_sparse_window(result, group, min_count, skipna)


def _gate_sparse_window(result: Any, group: Any, min_count: int, skipna: bool) -> Any:
    """NaN any cell whose window holds fewer than ``min_count`` valid (non-NaN) samples.

    The uniform ``min_count`` gate for every :func:`build_group_reduction` op. A no-op unless
    ``min_count > 0`` and ``skipna`` (a gap already propagates without ``skipna``). ``sum`` / ``min`` /
    ``max`` keep the cube's integer dtype, which cannot hold the NaN fill, so an integer result is
    promoted to float64 first (float results keep their dtype). Uses the NumPy API, which dispatches
    to :mod:`dask.array` on a lazy ``group``.

    Args:
        result: The reduced ``(band, rows, cols)`` array (numpy or dask).
        group: The window's ``(n_t, band, rows, cols)`` slice whose valid cells are counted.
        min_count: The threshold; ``0`` (or below) gates nothing.
        skipna: When ``False`` the gate is skipped.

    Returns:
        ``result`` with sub-threshold cells set to ``NaN``, or ``result`` unchanged.
    """
    if min_count <= 0 or not skipna:
        return result
    fill_dtype = (
        result.dtype if np.issubdtype(result.dtype, np.floating) else np.float64
    )
    valid_count = np.sum(~np.isnan(group), axis=0)
    return np.where(
        valid_count >= min_count,
        result.astype(fill_dtype),
        np.asarray(np.nan, dtype=fill_dtype),
    )
