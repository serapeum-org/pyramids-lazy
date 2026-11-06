"""Lazy ``NetCDF.to_dask_dataframe`` — the tidy dask-dataframe view of a cube.

Moved out of pyramids-core's ``netcdf.engines.interop`` (the lazy part of the Interop engine). Builds a
tidy :class:`dask.dataframe.DataFrame` (dimension coordinates as columns beside the value columns, on
a flat index), reading values lazily through ``NetCDF.read_array``. Core primitives (``_is_container``,
``_is_in_memory``, ``_frame_values``, ``_frame_axes``, ``_read_no_data``, ``import_dask``,
``require_optional``) come through the ``pyramids.lazy_support`` facade; it operates on the ``NetCDF``
object the backend passes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pyramids.lazy_support import (
    _frame_axes,
    _frame_values,
    _is_container,
    _is_in_memory,
    _read_no_data,
    import_dask,
    require_optional,
)


def _frame_variables(nc: Any, variables: Any) -> list[str]:
    """The data variables that become columns, in order (never empty). See NetCDF.to_dataframe."""
    if _is_container(nc):
        subject = "container"
        available = list(nc._spatial_variable_names(nc._working_group()))
        if not available:
            raise ValueError(
                "to_dataframe() needs at least one gridded variable, and this "
                "container has none."
            )
    else:
        subject = "variable"
        available = [nc._source_var_name or "variable"]
    if variables is None:
        names = available
    else:
        names = [variables] if isinstance(variables, str) else list(variables)
        unknown = [name for name in names if name not in available]
        if unknown:
            raise ValueError(
                f"to_dataframe() cannot take {unknown!r} as columns: this {subject}'s "
                f"gridded variables are {available}."
            )
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(
                f"to_dataframe() was asked for {repeated!r} more than once, and a "
                f"name can only become one column. Pass each name at most once."
            )
        if not names:
            raise ValueError(
                f"to_dataframe() was given an empty selection, and a frame needs at "
                f"least one column. Pass `variables=None` for all of them, or one of "
                f"{available}."
            )
    return names


def _lazy_value_columns(
    nc: Any, names: list[str], first: Any, in_memory: bool, chunks: Any, da: Any
) -> dict[str, Any]:
    """One flat float64 dask array per variable, gaps as NaN, in (*bands, y, x) ravel order."""
    value_arrays: dict[str, Any] = {}
    for name in names:
        var = nc._require_raster_variable(name) if _is_container(nc) else nc
        if tuple(var._band_dim_names) != tuple(first._band_dim_names):
            raise ValueError(
                f"to_dask_dataframe() needs the variables to share their band dimensions, so "
                f"their cells line up on one index: {names[0]!r} has "
                f"{tuple(first._band_dim_names)} and {name!r} has {tuple(var._band_dim_names)}. "
                f"Pass `variables=` to choose a set that agrees."
            )
        if in_memory:
            arr = da.from_array(
                np.asarray(_frame_values(nc, var), dtype="float64").reshape(-1),
                chunks="auto",
            )
        else:
            read = (
                nc.read_array(name, chunks=chunks)
                if _is_container(nc)
                else nc.read_array(chunks=chunks)
            )
            arr = read.astype("float64").reshape(-1)
            sentinel = _read_no_data(var)
            if sentinel is not None and not np.isnan(sentinel):
                arr = da.where(arr == sentinel, np.nan, arr)
        value_arrays[name] = arr
    return value_arrays


def _lazy_dimension_columns(first: Any, ref_chunks: Any, da: Any) -> dict[str, Any]:
    """One lazy dask column per dimension, the cartesian product in from_product order."""
    axis_names, levels = _frame_axes(first)
    sizes = [len(values) for values in levels]
    columns: dict[str, Any] = {}
    for position, (dim, values) in enumerate(zip(axis_names, levels)):
        inner = int(np.prod(sizes[position + 1 :], dtype="int64"))
        outer = int(np.prod(sizes[:position], dtype="int64"))
        base = da.from_array(np.asarray(values), chunks=-1)
        columns[dim] = da.tile(da.repeat(base, inner), outer).rechunk(ref_chunks)
    return columns


def to_dask_dataframe(
    nc: Any, *, variables: Any = None, dropna: bool = False, chunks: Any = "auto"
) -> Any:
    """Hand ``nc``'s cube to dask in tidy form — the lazy sibling of ``to_dataframe``.

    Returns a :class:`dask.dataframe.DataFrame` with the dimension coordinates as plain columns
    beside the value columns on a flat index. Values are read lazily through ``NetCDF.read_array``.
    """
    import_dask("to_dask_dataframe() needs dask; install the `lazy` extra.")
    require_optional(
        "dask.dataframe",
        "to_dask_dataframe() needs dask.dataframe, which requires pyarrow — install the "
        "`parquet` extra (e.g. `pip install pyramids-core[parquet]`).",
    )
    import dask.array as da
    import dask.dataframe as dd

    names = _frame_variables(nc, variables)
    first = nc._require_raster_variable(names[0]) if _is_container(nc) else nc
    in_memory = _is_in_memory(nc)
    value_arrays = _lazy_value_columns(nc, names, first, in_memory, chunks, da)
    ref_chunks = next(iter(value_arrays.values())).chunks
    columns = _lazy_dimension_columns(first, ref_chunks, da)
    for name, array in value_arrays.items():
        columns[name] = array.rechunk(ref_chunks)
    frame = dd.concat(
        [dd.from_dask_array(col, columns=[label]) for label, col in columns.items()],
        axis=1,
    )
    return frame.dropna(subset=list(names), how="all") if dropna else frame


__all__ = ["to_dask_dataframe"]
