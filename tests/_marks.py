"""Optional-dependency probes, skipif decorators, and marker mapping.

One source of truth for every `[project.optional-dependencies]` group.

Two consumers:

* Inline `@requires_<extra>` decorators — kept for test files that
  already guard individual methods with `pytest.mark.skipif(...)`.
* `pytest_collection_modifyitems` in :mod:`tests.conftest` — reads
  :data:`EXTRA_MARKERS` to auto-apply a skip when a test is tagged with
  `@pytest.mark.<extra>` but the extra is not installed.

Marker names use underscores (valid Python identifiers required by
`pytest.mark.<name>`), while the PyPI extra names use hyphens. The
mapping below spells both out explicitly.
"""

from __future__ import annotations

import importlib.util

import pytest


def _has(*module_names: str) -> bool:
    """Return True iff every named top-level module is importable.

    Uses :func:`importlib.util.find_spec` so the probe never triggers a
    heavy import — cheap to run during collection. A dotted name whose
    parent package is missing raises :class:`ModuleNotFoundError`
    (rather than returning `None`), so the guard catches that and
    reports the module as absent.
    """
    found = True
    for name in module_names:
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            found = False
            break
    return found


HAS_DASK = _has("dask")
HAS_DASK_ARRAY = _has("dask.array")
HAS_DASK_GEOPANDAS = _has("dask_geopandas")
HAS_ZARR = _has("zarr")
HAS_KERCHUNK = _has("kerchunk")
HAS_H5PY = _has("h5py")
HAS_PYARROW = _has("pyarrow")
HAS_XARRAY = _has("xarray")


# Per-extra installed? flag. Values are True when EVERY underlying
# module of that extra is importable — so `netcdf_lazy` requires
# kerchunk AND h5py AND the lazy stack, all of which now ship in
# `pip install pyramids-lazy`.
_HAS_LAZY = HAS_DASK_ARRAY and HAS_ZARR
_HAS_NETCDF_LAZY = _HAS_LAZY and HAS_KERCHUNK and HAS_H5PY
_HAS_PARQUET = HAS_PYARROW
_HAS_PARQUET_LAZY = _HAS_LAZY and _HAS_PARQUET and HAS_DASK_GEOPANDAS


# `dask`, `zarr`, `kerchunk` and `h5py` are HARD dependencies -- they sit in
# `[project] dependencies`, so `pip install pyramids-lazy` always brings them and
# `_HAS_LAZY` / `_HAS_NETCDF_LAZY` cannot be False in an installed environment. These
# are therefore plain CATEGORY markers, not conditional skips.
#
# The distinction matters: a `skipif` on a dependency that cannot be missing is dead
# weight at best, and at worst it is a silent-skip waiting to happen -- if one of
# those packages ever broke or was dropped from the metadata, several hundred tests
# would quietly report "skipped" and CI would stay green. As plain markers they
# instead fail loudly on the missing import, which is the correct signal, while
# `-m lazy` / `-m netcdf_lazy` selection keeps working exactly as before.
requires_lazy = pytest.mark.lazy
requires_netcdf_lazy = pytest.mark.netcdf_lazy

# The `[parquet]` extra is the only genuinely optional dependency set: the `base`
# environment installs without it on purpose, to exercise the no-extra shape. These
# two stay conditional, and they are the only skips the gate can ever see.
requires_parquet = pytest.mark.skipif(
    not _HAS_PARQUET, reason="pyramids-lazy[parquet] not installed"
)
requires_parquet_lazy = pytest.mark.skipif(
    not _HAS_PARQUET_LAZY,
    reason="dask-geopandas (pyramids-lazy[parquet]) not installed",
)

# `interop` is the other genuinely optional set: `xarray` lives in the test-only `interop`
# dependency-group, not in `[project] dependencies`, so `pip install pyramids-lazy` does NOT
# bring it. The pixi test envs add the `interop` feature (xarray present → these tests run),
# but the pure-wheel gate installs only the wheel + published runtime deps, so there xarray is
# absent and the xarray-interop tests must skip rather than raise OptionalPackageDoesNotExist.
requires_interop = pytest.mark.skipif(
    not HAS_XARRAY, reason="xarray (pyramids-lazy[interop]) not installed"
)


# `dask.array` is part of the hard `dask` dependency, so this is a category marker too.
# It used to carry its own skipif to distinguish "needs dask.array but not zarr"; that
# distinction is now carried by the marker alone.
requires_dask_array = pytest.mark.lazy

# dask.dataframe (dask-expr) imports pyarrow, which ships in the `parquet` extra. dask
# itself is hard, so pyarrow is the only real condition here.
requires_dask_dataframe = pytest.mark.skipif(
    not HAS_PYARROW,
    reason="dask.dataframe needs pyarrow (pyramids-lazy[parquet])",
)

# Legacy aliases for existing callsites that import the older names.
# Remove in a follow-up once the open call-sites migrate.
requires_dask = requires_lazy
requires_dask_geopandas = requires_parquet_lazy
requires_kerchunk = pytest.mark.netcdf_lazy
requires_pyarrow = requires_parquet


# Marker-name → skipif-decorator mapping consumed by the collection hook in
# `tests/conftest.py`. A test annotated with `@pytest.mark.<name>` auto-gains the
# matching skip when its extra is not installed.
#
# Only the genuinely optional extras appear here -- `parquet` (pyarrow / dask-geopandas) and
# `interop` (xarray). `lazy` and `netcdf_lazy` are deliberately absent: their dependencies are
# hard, so auto-applying a skip for them could only ever hide a broken install behind a green
# "skipped" line. They remain registered markers for `-m` selection.
EXTRA_MARKERS: dict[str, pytest.MarkDecorator] = {
    "parquet": requires_parquet,
    "parquet_lazy": requires_parquet_lazy,
    "interop": requires_interop,
}
