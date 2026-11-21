"""Lazy ``dropna`` test relocated from pyramids-core (tests/netcdf/selection/test_missing_data.py, #1455).

Only the dask-backed ``dropna`` single-compute test moves here; the eager fill/dropna/flag tests stay
in pyramids-core.
"""

from __future__ import annotations

import dask.array as dask_array
import numpy as np
import pytest
from pyramids.netcdf import ExtraDimensions, GeoReference, NetCDF

pytestmark = pytest.mark.lazy

GEO = GeoReference(geo=(0.0, 1.0, 0.0, 1.0, 0.0, -1.0), epsg=4326)
TIMES = [0.0, 6.0, 12.0, 18.0]


def _column(values: list[float], no_data_value: float | None = None) -> NetCDF:
    """A one-cell variable over `time`, holding `values`."""
    array = np.array(values, dtype="float64")
    if no_data_value is not None:
        array = np.where(np.isnan(array), no_data_value, array)
    return NetCDF.from_array(
        array.reshape(len(values), 1, 1),
        geo_ref=GEO,
        variable_name="t",
        no_data_value=no_data_value,
        dims=ExtraDimensions(name="time", values=TIMES[: len(values)]),
    ).get_variable("t")


class _CountedBlock:
    """A dask block function that records every compute of the graph it sits in."""

    def __init__(self) -> None:
        """Start the count at zero."""
        self.computes = 0

    def __call__(self, block: np.ndarray) -> np.ndarray:
        """Return `block` unchanged, counting the call."""
        self.computes += 1
        return block


class TestDropna:
    def test_a_lazily_backed_variable_is_computed_once(self, monkeypatch):
        """Counting the gaps and taking the survivors read one array, not the graph twice."""
        variable = _column([1.0, np.nan, 3.0, 4.0])
        cells = np.asarray(variable.read_array(), dtype="float64").reshape(4, 1, 1)
        counter = _CountedBlock()
        lazy = dask_array.from_array(cells, chunks=cells.shape).map_blocks(
            counter, dtype=cells.dtype, meta=np.empty((0, 0, 0), dtype=cells.dtype)
        )
        monkeypatch.setattr(
            NetCDF, "_materialize_variable_array", staticmethod(lambda *_, **__: lazy)
        )
        kept = variable.dropna("time")
        assert counter.computes == 1, f"the graph was computed {counter.computes} times"
        assert kept._band_dim_values_map["time"] == [0.0, 12.0, 18.0]
