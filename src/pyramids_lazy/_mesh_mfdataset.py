"""Lazy / dask-backed multi-file mesh opener -- the mesh sibling of ``_mfdataset``.

``MeshDataset.open_mfdataset`` in ``pyramids-core`` reads every file **eagerly**
(``[read_file(p) for p in paths]``) and joins them with :meth:`MeshDataset.concat`,
materialising the whole run into RAM before returning. This module is the lazy half of
serapeum-org/pyramids-core#1458: :func:`open_mesh_mfdataset` builds the *same* time-stacked
dataset but holds each temporal variable as a :class:`dask.array.Array` concatenated along its
time axis, so a long run (many ``*_map.nc``) is read out-of-core -- one file-block at a time at
compute time -- instead of all at once.

Design, mirroring the raster ``_mfdataset`` and the ``LazyNetCDF`` wrapper:

* It is deliberately **narrow**: one shared topology across the files, joined along time, exactly
  what core's eager ``open_mfdataset`` does -- no ``by_coords`` inference, no combine strategies.
* The return value is a :class:`LazyMeshDataset` *wrapper* (a mesh carries many data variables, so
  -- as with ``LazyNetCDF`` -- it is not a dask subclass). ``.compute()`` materialises it back to
  an eager :class:`~pyramids.netcdf.ugrid.MeshDataset` whose values and metadata **equal** the eager
  ``open_mfdataset`` result: the join and the rebuild go through core's own
  :meth:`MeshDataset._rebuild`, and the per-variable time-axis concatenation reproduces
  ``_concat_temporal_variable`` on ``.compute()``. (It is value-equal, not the same object: for a
  single file the eager path returns the original head dataset untouched, while this path always
  rebuilds; the values and ``time_values`` are equal either way.)
* ``dask`` is imported only at call time (as in ``_mfdataset``), so this module adds no eager dask
  import: ``import pyramids_lazy`` eagerly pulls this module's :class:`LazyMeshDataset` without this
  file importing dask at module scope. (The package itself is not dask-free on import -- ``_seam``
  pulls ``pyramids.lazy_support``, which already resolves dask -- but nothing here adds to that.)

Core reaches this through a ``getattr`` probe on the backend singleton (``open_mesh_mfdataset`` /
``lazy_mesh_type``), **not** a ``ComputeBackend`` Protocol method: the Protocol is
``runtime_checkable`` and core drops any backend failing its structural ``isinstance`` check, so a
Protocol addition would reject every already-released plugin. See serapeum-org/pyramids-core#1458.

Coupling note: this reaches core only through the *public* :class:`MeshDataset` class (as the raster
``_mfdataset`` reaches ``NetCDF``), but then drives it through private members that are **not** on the
versioned ``pyramids.lazy_support`` facade -- ``_data_variables``, ``_file_name``,
:meth:`MeshDataset._assert_same_topology`, :meth:`MeshDataset._rebuild`, and the hand-mirrored
``_TIME_UNITS_ATTR``. These are exactly what core's own ``concat`` / ``open_mfdataset`` use, so they
are de-facto stable, but a core rename would break this plugin with no ``LAZY_SUPPORT_API_VERSION``
guard to catch it at import. Graduating them onto the facade is tracked as follow-up; the parity
tests (whose fixtures stamp files with core's own ``TIME_UNITS_ATTR``) would fail loudly on a drift.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from pyramids_lazy._optional import lazy_hint

if TYPE_CHECKING:  # pragma: no cover - typing only
    import dask.array as da
    from pyramids.netcdf.ugrid import MeshDataset


_LAZY_IMPORT_ERROR = lazy_hint(
    "open_mesh_mfdataset requires the 'dask' dependency.", "dask"
)

# Replicated from `pyramids.netcdf.ugrid.dataset` so the lazy sort matches the eager
# `MeshDataset.open_mfdataset` exactly (issue #1378 / pyramids-core#1458). These are read off the
# public `MeshDataset` surface (`time_values`, variable `attributes`); the one private constant and
# the stamp-ordering rule are mirrored here rather than imported, to keep this module off core's
# internal `dataset` module (the lazy layer reaches core through its public classes + the
# `pyramids.lazy_support` facade, never a private module).
_TIME_UNITS_ATTR = "time_units"


def _stamp_order(stamp: Any) -> tuple:
    """Return a sortable ``(y, m, d, H, M, S, us)`` tuple for a decoded time stamp.

    Mirror of ``pyramids.netcdf.ugrid.dataset._stamp_order``: ``datetime64`` (Gregorian) and
    ``cftime`` (model calendars) do not compare across flavours, so the comparable calendar fields
    are used instead, which keeps a mixed run sortable.

    Args:
        stamp: A decoded time stamp (``numpy.datetime64`` or a ``cftime`` object).

    Returns:
        tuple: ``(year, month, day, hour, minute, second, microsecond)``.

    Examples:
        - A ``datetime64`` day decomposes into its calendar fields:
            ```python
            >>> import numpy as np
            >>> from pyramids_lazy._mesh_mfdataset import _stamp_order
            >>> _stamp_order(np.datetime64("2001-01-15"))
            (2001, 1, 15, 0, 0, 0, 0)

            ```
        - The tuples order chronologically, so a lexically-earlier file can sort later:
            ```python
            >>> import numpy as np
            >>> from pyramids_lazy._mesh_mfdataset import _stamp_order
            >>> _stamp_order(np.datetime64("2001-01-10")) > _stamp_order(np.datetime64("2001-01-02"))
            True

            ```
    """
    moment = stamp if hasattr(stamp, "year") else np.datetime64(stamp, "us").item()
    return (
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
        moment.microsecond,
    )


def _resolve_mesh_paths(paths: str | Path | list) -> list:
    """Normalise ``paths`` to a non-empty list, mirroring core's ``open_mfdataset`` resolution.

    A glob string is expanded and sorted; a string that is not a glob and matches nothing is kept
    as a single explicit path (so one filename passes through); an empty result is an error rather
    than a silently-empty read.

    Args:
        paths: A glob pattern (``"out/*_map.nc"``), a single path, or a sequence of paths.

    Returns:
        list: The resolved paths, in the order to stack them (before any time sort).

    Raises:
        ValueError: ``paths`` is an empty sequence, or a glob pattern that matches no file.

    Examples:
        - A plain (non-glob) filename passes through as a one-element list:
            ```python
            >>> from pyramids_lazy._mesh_mfdataset import _resolve_mesh_paths
            >>> _resolve_mesh_paths("run_t2.nc")
            ['run_t2.nc']

            ```
        - An explicit sequence keeps its given order (sorting happens later, if at all):
            ```python
            >>> from pyramids_lazy._mesh_mfdataset import _resolve_mesh_paths
            >>> _resolve_mesh_paths(["b.nc", "a.nc"])
            ['b.nc', 'a.nc']

            ```
        - An empty sequence is refused rather than read as an empty dataset:
            ```python
            >>> from pyramids_lazy._mesh_mfdataset import _resolve_mesh_paths
            >>> _resolve_mesh_paths([])
            Traceback (most recent call last):
                ...
            ValueError: open_mfdataset() got an empty list of paths; there is nothing to open.

            ```
    """
    if isinstance(paths, (str, Path)):
        pattern = str(paths)
        is_pattern = any(character in pattern for character in "*?[")
        matched = sorted(glob.glob(pattern)) if is_pattern else []
        if matched:
            resolved: list = list(matched)
        elif is_pattern:
            raise ValueError(
                f"open_mfdataset() got the pattern {pattern!r}, which matches no file."
            )
        else:
            resolved = [paths]
    else:
        resolved = list(paths)
    if not resolved:
        raise ValueError(
            "open_mfdataset() got an empty list of paths; there is nothing to open."
        )
    return resolved


def _sort_dated(datasets: list) -> list:
    """Order the **dated** datasets among themselves, leaving undated ones in place.

    Mirror of the sort in core's ``MeshDataset.open_mfdataset``: only a file carrying CF time
    units has a stamp that says where it belongs, so dated files are sorted by their first stamp
    and dropped back into the slots they occupied, while undated files (positional indices only)
    keep their given position.

    Args:
        datasets: The per-file datasets, in the order the paths were given.

    Returns:
        list: A new list with the dated files reordered chronologically in their own slots.
    """
    dated_slots = [
        index
        for index, dataset in enumerate(datasets)
        if dataset.time_values
        and any(
            _TIME_UNITS_ATTR in var.attributes
            for var in dataset._data_variables.values()
        )
    ]

    def first_stamp(dataset: MeshDataset) -> tuple:
        """The sort key of a dated dataset: its earliest stamp."""
        stamps = dataset.time_values
        assert stamps is not None  # dated_slots holds only non-empty-time datasets
        return _stamp_order(stamps[0])

    ordered = sorted((datasets[index] for index in dated_slots), key=first_stamp)
    result = list(datasets)
    for slot, dataset in zip(dated_slots, ordered, strict=True):
        result[slot] = dataset
    return result


def _load_mesh_variable(
    path: str,
    var_name: str,
    *,
    mesh_name: str | None = None,
    driver: str | None = None,
) -> np.typing.NDArray:
    """Re-open one mesh file and read one variable's full array (a pickle-safe dask block reader).

    Built per ``(file, variable)`` block and handed to :func:`dask.delayed`, so it must be
    importable and take only picklable arguments -- hence a module-level function over string
    arguments rather than a captured :class:`MeshVariable`. Reads through the public
    :meth:`MeshDataset.read_file`, re-selecting the same topology by name, so the lazy path never
    touches a core-internal reader.

    Args:
        path: The file to re-open.
        var_name: The variable to read from it.
        mesh_name: The topology to select, so a multi-mesh file reads the same one each block.
        driver: The mesh driver the original read used, forwarded unchanged.

    Returns:
        numpy.ndarray: The variable's full array for this file.
    """
    from pyramids.netcdf.ugrid import MeshDataset

    dataset = MeshDataset.read_file(path, mesh_name=mesh_name, driver=driver)
    return np.asarray(dataset._data_variables[var_name].load_array())


def _build_lazy_variables(
    parts: list,
    chunks: Any,
    driver: str | None,
) -> tuple[dict, dict]:
    """Build one lazy time-stacked dask array per temporal variable, mirroring ``concat``.

    Reproduces :meth:`MeshDataset.concat` / ``_concat_temporal_variable`` without materialising:
    each temporal variable's per-file arrays are stacked along its time axis as a
    :class:`dask.array.Array`, and its ``time_values`` coordinate is joined eagerly (the stamps are
    metadata, not array data). Static variables are **not** built here -- as in ``concat`` they are
    taken from the head dataset as-is at compute time.

    Args:
        parts: The per-file datasets, head first, already topology/variable-set validated.
        chunks: ``None`` keeps one dask chunk per file (the natural time-block chunking); anything
            else is passed to :meth:`dask.array.Array.rechunk` on the stacked array.
        driver: The mesh driver forwarded to each per-block re-read.

    Returns:
        tuple: ``(var_arrays, joined_time_values)`` -- the temporal variables' dask arrays keyed by
        name, and their joined ``time_values`` lists (``None`` when a part carried no coordinate).

    Raises:
        ImportError: When ``dask`` is not installed.
        ValueError: A variable is temporal in one file but static in another (either direction),
            matching ``concat``'s refusal.
    """
    try:
        import dask
        import dask.array as da
    except ImportError as exc:  # pragma: no cover - exercised via open_mesh_mfdataset
        raise ImportError(_LAZY_IMPORT_ERROR) from exc

    head = parts[0]
    var_arrays: dict = {}
    joined_time_values: dict = {}
    for name, var in head._data_variables.items():
        if not var.has_time:
            # Static in head: refuse if any other part has it temporal, rather than silently
            # dropping that part's time series (the mirror of the temporal-in-head case below).
            if any(part._data_variables[name].has_time for part in parts[1:]):
                raise ValueError(
                    f"open_mfdataset: variable {name!r} is static in one dataset but temporal "
                    "in another; they cannot be joined along time."
                )
            continue  # a static variable is taken from head as-is, at compute time
        axis = var.time_axis_index
        blocks = []
        times: list = []
        has_times = True
        for part in parts:
            part_var = part._data_variables[name]
            if not part_var.has_time:
                raise ValueError(
                    f"open_mfdataset: variable {name!r} is temporal in one dataset but static "
                    "in another; they cannot be joined along time."
                )
            source = part._file_name
            if (
                source is None
            ):  # pragma: no cover - every read_file dataset names its source
                raise ValueError(
                    f"open_mfdataset: cannot build a lazy read for {name!r}; the dataset for a "
                    "part has no source file to re-open. Lazy multi-file reads need on-disk files."
                )
            block = da.from_delayed(
                dask.delayed(_load_mesh_variable)(
                    source, name, mesh_name=part.mesh_name, driver=driver
                ),
                shape=part_var.shape,
                dtype=part_var.dtype,
            )
            blocks.append(block)
            part_times = part_var.attributes.get("time_values")
            if part_times is None:
                has_times = False
            else:
                times.extend(list(part_times))
        stacked = da.concatenate(blocks, axis=axis)
        if chunks is not None:
            stacked = stacked.rechunk(chunks)
        var_arrays[name] = stacked
        joined_time_values[name] = times if has_times else None
    return var_arrays, joined_time_values


class LazyMeshDataset:
    """A lazily-backed, time-stacked mesh dataset -- the mesh analogue of :class:`LazyNetCDF`.

    Holds the shared topology and metadata of the stacked run (from the head file) plus one
    :class:`dask.array.Array` per **temporal** variable, concatenated along its time axis across the
    files. A mesh carries many data variables on one topology, so -- like ``LazyNetCDF`` and unlike
    the raster ``open_mfdataset`` (which stacks a single variable into a bare dask array) -- this is
    a wrapper, not a dask subclass.

    Static (non-temporal) variables are not stacked: as in :meth:`MeshDataset.concat` they belong to
    every step and are carried from the head dataset unchanged, so they are not held here and are
    taken straight from the head at :meth:`compute`.

    ``.compute()`` / ``.load()`` materialise the wrapper back to an eager
    :class:`~pyramids.netcdf.ugrid.MeshDataset` whose values and metadata **equal** the eager
    ``MeshDataset.open_mfdataset`` result -- the rebuild goes through core's own
    :meth:`MeshDataset._rebuild`. (Value-equal, not the same object: for a single file the eager
    path returns the original head dataset untouched while this rebuilds; values are equal either
    way.)

    Examples:
        - Build one from a two-step run, inspect it lazily, then materialise and use it. The
          wrapper is produced by :func:`open_mesh_mfdataset`, never constructed directly:
            ```python
            >>> import os, tempfile, numpy as np
            >>> from pyramids.netcdf.ugrid import MeshDataset
            >>> from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset
            >>> step = MeshDataset.from_arrays(
            ...     node_x=np.array([0.0, 1.0, 1.0, 0.0]),
            ...     node_y=np.array([0.0, 0.0, 1.0, 1.0]),
            ...     face_node_connectivity=np.array([[0, 1, 2], [0, 2, 3]]),
            ...     data={"d": np.array([[1.0, 2.0]])},
            ...     data_locations={"d": "face"},
            ... )
            >>> step["d"].dimensions = ("time", "mesh2d_nFaces")
            >>> path = os.path.join(tempfile.mkdtemp(), "step.nc")
            >>> step.to_file(path)
            >>> lazy = open_mesh_mfdataset([path, path], sort=False)
            >>> lazy.data_variable_names
            ['d']
            >>> lazy["d"].chunks[0]
            (1, 1)
            >>> eager = lazy.compute()
            >>> eager["d"].data[:, 0].tolist()
            [1.0, 1.0]

            ```
    """

    def __init__(
        self, head: MeshDataset, var_arrays: dict, joined_time_values: dict
    ) -> None:
        """Assemble the wrapper from the head dataset and the stacked temporal variables.

        Args:
            head: The first file after the time sort; seeds the topology, global attributes, CRS,
                topology info and per-variable templates (exactly what ``concat`` keeps).
            var_arrays: One :class:`dask.array.Array` per temporal variable, keyed by name, each
                already concatenated along its time axis across the run.
            joined_time_values: The joined ``time_values`` coordinate per temporal variable
                (``None`` when some file carried no coordinate), applied at :meth:`compute`.
        """
        self._head = head
        self._var_arrays = var_arrays
        self._joined_time_values = joined_time_values

    @property
    def mesh(self) -> Any:
        """The shared mesh topology (from the head file)."""
        return self._head.mesh

    @property
    def crs(self) -> Any:
        """The dataset CRS (from the head file), or ``None``."""
        return self._head.crs

    @property
    def data_variable_names(self) -> list[str]:
        """Every data-variable name, temporal and static alike."""
        return self._head.data_variable_names

    @property
    def temporal_variable_names(self) -> list[str]:
        """The names of the variables stacked lazily along time."""
        return list(self._var_arrays)

    @property
    def temporal_variables(self) -> dict:
        """The lazy time-stacked dask array of each temporal variable, keyed by name.

        Only temporal variables appear here -- static ones are not stacked (see
        :attr:`temporal_variable_names`); :attr:`data_variable_names` lists both.
        """
        return dict(self._var_arrays)

    @property
    def chunks(self) -> dict:
        """The dask chunking of each temporal variable's stacked array, keyed by name."""
        return {name: array.chunks for name, array in self._var_arrays.items()}

    def __contains__(self, name: str) -> bool:
        """True when ``name`` is any data variable of the dataset."""
        return name in self._head

    def __getitem__(self, name: str) -> da.Array:
        """The lazy time-stacked dask array for a temporal variable.

        Args:
            name: A temporal variable name (see :attr:`temporal_variable_names`).

        Returns:
            dask.array.Array: The variable stacked along its time axis across the run.

        Raises:
            KeyError: ``name`` is unknown, or is a static variable (which is not stacked -- read it
                from the materialised dataset instead).
        """
        if name in self._var_arrays:
            return self._var_arrays[name]
        if name in self._head._data_variables:
            raise KeyError(
                f"{name!r} is a static (non-temporal) variable and is not stacked lazily; it is "
                "carried unchanged from the head file -- read it from `.compute()[name]`."
            )
        raise KeyError(name)

    def compute(self) -> MeshDataset:
        """Materialise the lazy stack into an eager :class:`MeshDataset`.

        Computes each temporal variable's dask array and rebuilds the dataset through core's own
        :meth:`MeshDataset._rebuild`, so the result is value-equal to the eager
        ``MeshDataset.open_mfdataset``: temporal variables carry the concatenated values and the
        joined ``time_values`` coordinate; static variables come from the head file unchanged. (For
        a single file the eager path returns the head dataset itself; this rebuilds an equal one.)

        Returns:
            MeshDataset: The time-stacked dataset, fully in memory.
        """
        new_variables: dict = {}
        for name, var in self._head._data_variables.items():
            if name not in self._var_arrays:
                new_variables[name] = var  # static: from head as-is, as concat does
                continue
            materialised = self._var_arrays[name].compute()
            new_var = var.with_data(np.asarray(materialised))
            times = self._joined_time_values.get(name)
            if times is not None:
                new_var.attributes = {**var.attributes, "time_values": times}
            elif "time_values" in new_var.attributes:
                # Some part had no time coordinate, so the joined axis has none; drop the stale,
                # too-short `time_values` carried over by `with_data` (mirrors concat).
                new_var.attributes = {
                    key: value
                    for key, value in new_var.attributes.items()
                    if key != "time_values"
                }
            new_variables[name] = new_var
        return self._head._rebuild(new_variables)

    def load(self) -> MeshDataset:
        """Alias for :meth:`compute`."""
        return self.compute()

    def __repr__(self) -> str:
        """A one-line summary naming the stacked temporal variables."""
        temporal = ", ".join(self._var_arrays) or "-"
        return f"<LazyMeshDataset temporal=[{temporal}] on {self._head.mesh_name!r}>"


def open_mesh_mfdataset(
    paths: str | Path | list,
    *,
    chunks: Any = None,
    driver: str | None = None,
    mesh_name: str | None = None,
    sort: bool = True,
) -> LazyMeshDataset:
    """Open many mesh files sharing one topology and stack them lazily along time.

    The lazy sibling of core's eager :meth:`MeshDataset.open_mfdataset` (serapeum-org/pyramids-core
    #1458). The files are read **metadata-only** (``read_file`` defers every variable's values), the
    run is ordered exactly as the eager path orders it, and each temporal variable is then held as a
    :class:`dask.array.Array` concatenated along its time axis -- so the values are read out-of-core
    at :meth:`LazyMeshDataset.compute` time, one file at a time, instead of all at once.

    Args:
        paths: A glob pattern (``"out/*_map.nc"``), a single path, or a sequence of paths. A glob is
            expanded and sorted; a pattern matching nothing is an error.
        chunks: ``None`` (the default) keeps one dask chunk per file -- the natural time-block
            chunking for a run written one file per step/day. Anything else is forwarded to
            :meth:`dask.array.Array.rechunk` on each stacked variable.
        driver: Name of a registered mesh driver, bypassing detection; forwarded to every read.
            ``None`` detects.
        mesh_name: Which topology to take from each file. ``None`` takes the first; the head file's
            resolved topology name is then used to re-select the same one on every lazy block read.
        sort: Order the dated files by their first time stamp before stacking (undated files keep
            their given position), matching the eager ``open_mfdataset``. ``False`` keeps the given
            order.

    Returns:
        LazyMeshDataset: The lazily time-stacked dataset. ``.compute()`` yields the same
        :class:`MeshDataset` as the eager ``MeshDataset.open_mfdataset``.

    Raises:
        ImportError: When ``dask`` is not installed.
        ValueError: ``paths`` is empty / a glob matches nothing, the files carry different variable
            sets, or a variable is temporal in one file and static in another.
        AlignmentError: A file sits on a different topology than the head file.

    Examples:
        - Stack one temporal step with itself and read the lazy shape, then materialise it. The
          stack is two time steps long but nothing is read from disk until ``.compute()``:
            ```python
            >>> import os, tempfile, numpy as np
            >>> from pyramids.netcdf.ugrid import MeshDataset
            >>> from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset
            >>> step = MeshDataset.from_arrays(
            ...     node_x=np.array([0.0, 1.0, 1.0, 0.0]),
            ...     node_y=np.array([0.0, 0.0, 1.0, 1.0]),
            ...     face_node_connectivity=np.array([[0, 1, 2], [0, 2, 3]]),
            ...     data={"d": np.array([[1.0, 2.0]])},
            ...     data_locations={"d": "face"},
            ... )
            >>> step["d"].dimensions = ("time", "mesh2d_nFaces")
            >>> path = os.path.join(tempfile.mkdtemp(), "step.nc")
            >>> step.to_file(path)
            >>> lazy = open_mesh_mfdataset([path, path], sort=False)
            >>> lazy.temporal_variable_names
            ['d']
            >>> lazy["d"].shape
            (2, 2)
            >>> lazy.compute()["d"].n_time_steps
            2

            ```
        - The realistic entry point is a glob over a run written one file per day:
            ```python
            >>> from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset
            >>> lazy = open_mesh_mfdataset("out/*_map.nc")        # doctest: +SKIP
            >>> waterlevels = lazy["waterlevel"].mean(axis=0).compute()  # doctest: +SKIP
            ```
    """
    from pyramids.netcdf.ugrid import MeshDataset

    resolved = _resolve_mesh_paths(paths)
    datasets = [
        MeshDataset.read_file(path, driver=driver, mesh_name=mesh_name)
        for path in resolved
    ]
    if sort:
        datasets = _sort_dated(datasets)

    head, *rest = datasets
    # Validate the join eagerly (metadata only), exactly as `concat` does, so a mismatched run is
    # refused at graph-build time with the same errors rather than failing deep in `.compute()`.
    for other in rest:
        head._assert_same_topology(other, "open_mfdataset")
        if set(other._data_variables) != set(head._data_variables):
            raise ValueError(
                "open_mfdataset requires the same variables in every file; got "
                f"{sorted(head._data_variables)} vs {sorted(other._data_variables)}."
            )

    var_arrays, joined_time_values = _build_lazy_variables(datasets, chunks, driver)
    return LazyMeshDataset(head, var_arrays, joined_time_values)
