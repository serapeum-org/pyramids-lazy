"""Mesh `to_dask_dataframe` (lazy sibling of the tidy frame), relocated from pyramids-core (#1590).

`MeshDataset.to_dask_dataframe` builds a dask dataframe; the eager mesh parity tests stay in
pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.netcdf.ugrid import MeshDataset

pytestmark = pytest.mark.lazy

_NODE_X = np.array([0.0, 1.0, 1.0, 0.0])
_NODE_Y = np.array([0.0, 0.0, 1.0, 1.0])
_TRIANGLES = np.array([[0, 1, 2], [0, 2, 3]])


def _mesh(values, units=None, nodata=None, location="face"):
    ds = MeshDataset.from_arrays(
        node_x=_NODE_X,
        node_y=_NODE_Y,
        face_node_connectivity=_TRIANGLES,
        data={"t": np.asarray(values, dtype="float64")},
        data_locations={"t": location},
        epsg=32636,
    )
    if units is not None:
        ds["t"].units = units
    if nodata is not None:
        ds["t"].nodata = nodata
    return ds


class TestToDaskDataframe:
    """The lazy sibling of the tidy frame."""

    def test_it_returns_a_dask_dataframe(self):
        dd = pytest.importorskip("dask.dataframe")
        frame = _mesh([1.0, 2.0]).to_dask_dataframe()
        assert isinstance(frame, dd.DataFrame)

    def test_it_computes_to_the_same_rows_as_to_dataframe(self):
        """The lazy and eager paths must agree, or one of them is wrong."""
        pytest.importorskip("dask.dataframe")
        ds = _mesh([1.0, 2.0])
        eager = ds.to_dataframe()
        lazy = ds.to_dask_dataframe().compute()
        assert list(lazy.columns) == list(eager.columns)
        assert len(lazy) == len(eager)

    def test_partitions_are_honoured(self):
        pytest.importorskip("dask.dataframe")
        assert _mesh([1.0, 2.0]).to_dask_dataframe(npartitions=2).npartitions == 2

    def test_zero_partitions_is_refused(self):
        subject = _mesh([1.0, 2.0])
        with pytest.raises(ValueError, match="at least 1"):
            subject.to_dask_dataframe(npartitions=0)
