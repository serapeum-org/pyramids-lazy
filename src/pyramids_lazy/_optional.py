"""Branded guards for this backend's own optional dependencies.

Core's ``extra_hint`` composes hints for *core's* extras, so it names ``pyramids-core[parquet]``.
The lazy-vector path ships in THIS distribution's ``[parquet]`` extra, so its hints have to name
``pyramids-lazy[parquet]`` instead. The guards live in their own module so a module-scope import can
be branded without ``_vector_read`` and ``_lazy_collection`` importing each other.
"""

from __future__ import annotations

# Imported at runtime rather than under TYPE_CHECKING. Both sides are nearly free, so this is a
# tie broken towards the simpler code: `types` costs ~0.9 ms (measured with -X importtime) on a
# module only reached via the [parquet] path, and keeping it here leaves the annotation resolvable
# by `typing.get_type_hints`, which deferring would turn into a NameError. Nothing in this repo
# calls `get_type_hints` today — it is kept as a property of the annotation, not for a caller.
from types import ModuleType


class LazyExtraNotInstalled(ModuleNotFoundError):
    """A dependency from one of this backend's optional extras is missing.

    Subclasses :class:`ModuleNotFoundError`, not just :class:`ImportError`, deliberately. Tooling
    treats the two differently: :func:`pytest.importorskip` skips on ``ModuleNotFoundError`` and
    **re-raises** every other ``ImportError`` so a real breakage is never swallowed, and plenty of
    library code guards optional imports with ``except ModuleNotFoundError``. Raising a plain
    ``ImportError`` here therefore turned "this extra is not installed" into a hard error for every
    such caller -- it aborted collection across pyramids-core's whole matrix once. Being a
    ``ModuleNotFoundError`` keeps those callers working while the message stays branded.
    """


_PARQUET_INSTALL = (
    "Install the lazy-vector dependencies with one of:\n"
    "  - PyPI:        pip install 'pyramids-lazy[parquet]'\n"
    # pyramids-lazy is not on conda-forge yet, so name the two conda packages the extra pulls rather
    # than a `conda install pyramids-lazy[parquet]` that would not resolve.
    "  - conda-forge: conda install -c conda-forge dask-geopandas pyarrow"
)


def parquet_hint(lead: str) -> str:
    """Compose the install hint for this distribution's ``[parquet]`` extra.

    Args:
        lead: The sentence naming the operation that needed the dependency, ending in a period.

    Returns:
        The lead followed by the two-line install block.
    """
    return f"{lead} {_PARQUET_INSTALL}"


def lazy_hint(lead: str, package: str) -> str:
    """Compose an install hint for one of this distribution's own runtime deps.

    Unlike :func:`parquet_hint` (which names the ``[parquet]`` *extra*), these dependencies —
    ``dask``, ``zarr``, ``kerchunk``, ``h5py`` and friends — live in ``pyramids-lazy``'s plain
    ``[project.dependencies]``. A user who reaches one of these guards therefore already has
    ``pyramids-lazy`` installed, so the actionable remedy is to install the one missing library
    (``pip install dask``); ``pip install pyramids-lazy`` would not reinstall an already-satisfied
    dependency, hence the ``--force-reinstall`` repair line as the fallback. No conda-forge line is
    offered: ``pyramids-lazy`` is not on conda-forge, and the package of that name there is core's
    old lazy extra, which carries no backend.

    Args:
        lead: The sentence naming the operation that needed the dependency, ending in a period.
        package: The pip install target(s) for the missing dependency, e.g. ``"dask"`` or
            ``"dask zarr"``.

    Returns:
        The lead followed by the two-line install block.

    Examples:
        - The lead is kept verbatim at the start and the named library is the pip target:
            ```python
            >>> from pyramids_lazy._optional import lazy_hint
            >>> hint = lazy_hint("open_mfdataset requires the optional 'dask' dependency.", "dask")
            >>> hint.startswith("open_mfdataset requires the optional 'dask' dependency.")
            True
            >>> "pip install dask" in hint
            True
            >>> "pip install --force-reinstall pyramids-lazy" in hint
            True

            ```
        - A space-joined ``package`` installs several libraries in one command, and no
          core extra or conda line is ever named:
            ```python
            >>> from pyramids_lazy._optional import lazy_hint
            >>> hint = lazy_hint("Zarr IO requires the optional deps.", "dask zarr")
            >>> "pip install dask zarr" in hint
            True
            >>> "pyramids-core[lazy]" in hint
            False
            >>> "conda" in hint
            False

            ```
    """
    return (
        f"{lead} The pyramids-lazy install is incomplete.\n"
        f"  - PyPI:        pip install {package}\n"
        f"  - or repair:   pip install --force-reinstall pyramids-lazy"
    )


def import_dask_geopandas() -> ModuleType:
    """Import and return ``dask_geopandas``, or raise a branded ``ImportError``.

    Called at module scope by every module that needs ``dask_geopandas``, so the branded hint is
    raised on a cold import too — not only by the call-time guards, which a warm ``sys.modules``
    is all that lets them be reached.

    Returns:
        The imported ``dask_geopandas`` module.

    Raises:
        LazyExtraNotInstalled: When ``dask_geopandas`` is not installed, naming
            ``pyramids-lazy[parquet]``. A ``ModuleNotFoundError`` subclass, so optional-import
            guards and :func:`pytest.importorskip` keep treating it as "absent" rather than
            "broken".

    Examples:
        - The result is the `dask_geopandas` module itself, so its API is reached straight off it:
            ```python
            >>> from pyramids_lazy._optional import import_dask_geopandas
            >>> module = import_dask_geopandas()  # doctest: +SKIP
            >>> module.__name__  # doctest: +SKIP
            'dask_geopandas'
            >>> module.GeoDataFrame.__name__  # doctest: +SKIP
            'GeoDataFrame'

            ```
        - Every call hands back the single module object `sys.modules` already holds, so calling it
          at module scope and again inside a call-time guard costs one import:
            ```python
            >>> from pyramids_lazy._optional import import_dask_geopandas
            >>> import_dask_geopandas() is import_dask_geopandas()  # doctest: +SKIP
            True

            ```
    """
    try:
        import dask_geopandas as module
    except ImportError as exc:
        raise LazyExtraNotInstalled(
            parquet_hint(
                "The lazy vector path requires the optional 'dask-geopandas' dependency."
            )
        ) from exc
    return module
