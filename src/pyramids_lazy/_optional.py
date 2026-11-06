"""Branded guards for this backend's own optional dependencies.

Core's ``extra_hint`` composes hints for *core's* extras, so it names ``pyramids-core[parquet]``.
The lazy-vector path ships in THIS distribution's ``[parquet]`` extra, so its hints have to name
``pyramids-lazy[parquet]`` instead. The guards live in their own module so a module-scope import can
be branded without ``_vector_read`` and ``_lazy_collection`` importing each other.
"""

from __future__ import annotations

_PARQUET_INSTALL = (
    "Install with one of:\n"
    "  - PyPI:        pip install 'pyramids-lazy[parquet]'\n"
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


def import_dask_geopandas():
    """Import and return ``dask_geopandas``, or raise a branded ``ImportError``.

    Called at module scope by every module that needs ``dask_geopandas``, so the branded hint is
    raised on a cold import too — not only by the call-time guards, which a warm ``sys.modules``
    is all that lets them be reached.

    Returns:
        The imported ``dask_geopandas`` module.

    Raises:
        ImportError: When ``dask_geopandas`` is not installed, naming ``pyramids-lazy[parquet]``.
    """
    try:
        import dask_geopandas as module
    except ImportError as exc:
        raise ImportError(
            parquet_hint(
                "The lazy vector path requires the optional 'dask-geopandas' dependency."
            )
        ) from exc
    return module
