"""Lazy (zarr) MeshDataset round-trip relocated from pyramids-core
(tests/netcdf/ugrid/test_mesh_dataset_guards.py, #1455).

The eager MeshDataset guard tests stay in pyramids-core; only the zarr round-trip (which needs the
dask/zarr stack) lives here, run against released core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.netcdf.ugrid import MeshDataset

pytestmark = pytest.mark.lazy

NODE_X = np.array([0.0, 1.0, 1.0, 0.0])
NODE_Y = np.array([0.0, 0.0, 1.0, 1.0])
TRIANGLES = np.array([[0, 1, 2], [0, 2, 3]])


@pytest.fixture
def mesh() -> MeshDataset:
    """Two triangles carrying one static face variable."""
    return MeshDataset.from_arrays(
        node_x=NODE_X,
        node_y=NODE_Y,
        face_node_connectivity=TRIANGLES,
        data={"depth": np.array([2.0, 4.0])},
    )


class TestZarrRoundTrip:
    def test_from_zarr_reads_back_what_to_zarr_wrote(self, mesh, tmp_path):
        """The zarr pair opens the store once and rebuilds both the mesh and its variables."""
        pytest.importorskip("xarray")
        pytest.importorskip("zarr")
        store = str(tmp_path / "mesh.zarr")
        mesh.to_zarr(store)
        back = MeshDataset.from_zarr(store)
        assert (back.n_node, back.n_face) == (4, 2)
        assert back["depth"].data.tolist() == [2.0, 4.0]
