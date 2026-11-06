"""The dask-geopandas guard must be reachable on a cold import, not only when cached."""

from __future__ import annotations

import builtins
import sys

import pytest

_LAZY_MODULES = (
    "pyramids_lazy._vector_read",
    "pyramids_lazy._lazy_collection",
    "dask_geopandas",
)


@pytest.fixture
def without_dask_geopandas(monkeypatch):
    """Drop the cached lazy-vector modules and make `import dask_geopandas` fail."""
    for name in _LAZY_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dask_geopandas":
            raise ImportError("No module named 'dask_geopandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestTheGuardSurvivesAColdImport:
    """Importing the lazy-vector modules without dask-geopandas gives the branded hint."""

    def test_importing_vector_read_is_branded(self, without_dask_geopandas):
        """A cold `import pyramids_lazy._vector_read` names the plugin's parquet extra."""
        with pytest.raises(ImportError, match=r"pyramids-lazy\[parquet\]"):
            import pyramids_lazy._vector_read  # noqa: F401

    def test_importing_lazy_collection_is_branded(self, without_dask_geopandas):
        """A cold `import pyramids_lazy._lazy_collection` names the plugin's parquet extra."""
        with pytest.raises(ImportError, match=r"pyramids-lazy\[parquet\]"):
            import pyramids_lazy._lazy_collection  # noqa: F401
