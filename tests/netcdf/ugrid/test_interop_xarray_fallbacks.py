"""Lazy ``chunked_variables`` skip-path tests relocated from pyramids-core
(tests/netcdf/ugrid/test_interop_xarray_fallbacks.py, #1455).

The eager fallback tests stay in pyramids-core; only the dask ``chunked_variables`` skip paths live
here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.netcdf.ugrid.interop_xarray import chunked_variables
from pyramids.netcdf.ugrid.models import MeshVariable

pytestmark = pytest.mark.lazy


def _no_array() -> None:
    """A loader that yields no array, so the variable has a source but no data."""
    return None


def _depth(**kwargs) -> MeshVariable:
    """A static face variable named ``depth``, with any extra field overridden."""
    fields = {
        "name": "depth",
        "location": "face",
        "mesh_name": "mesh2d",
        "shape": (2,),
        "dimensions": ("mesh2d_nFaces",),
        "_data": np.array([2.0, 4.0]),
    }
    fields.update(kwargs)
    return MeshVariable(**fields)


class TestChunkedVariablesSkips:
    """The two ``chunked_variables`` skip paths, where a variable has nothing to chunk."""

    def test_a_metadata_only_variable_is_left_out(self):
        """A variable with neither an array nor a loader cannot be chunked, so it is skipped."""
        pytest.importorskip("dask.array")
        chunked = chunked_variables(
            {"depth": _depth(), "ghost": _depth(name="ghost", _data=None)}, chunks=1
        )
        assert set(chunked) == {"depth"}

    def test_a_loader_yielding_no_array_is_left_out(self):
        """A loader that answers None also leaves the variable out of the mapping."""
        pytest.importorskip("dask.array")
        chunked = chunked_variables(
            {"empty": _depth(name="empty", _data=None, _loader=_no_array)}, chunks=1
        )
        assert chunked == {}
