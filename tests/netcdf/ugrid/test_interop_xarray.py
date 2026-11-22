"""Lazy (zarr / dask) ugrid interop-xarray tests relocated from pyramids-core
(tests/netcdf/ugrid/test_interop_xarray.py, #1455).

The eager to_xarray/from_xarray and connectivity-encoding tests stay in pyramids-core; only the Zarr
round-trip and chunked-variable (dask) classes live here, run against released core.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest
from pyramids.netcdf.ugrid import interop_xarray
from pyramids.netcdf.ugrid.connectivity import Connectivity
from pyramids.netcdf.ugrid.dataset import MeshDataset
from pyramids.netcdf.ugrid.mesh import Mesh2d
from pyramids.netcdf.ugrid.models import MeshVariable

pytestmark = pytest.mark.lazy

_SAMPLE_NC = Path("tests/data/netcdf/ugrid/ugrid.nc")


def _mesh_with_two_variables() -> tuple[Mesh2d, dict[str, MeshVariable]]:
    """Build a 2-triangle mesh plus a static and a temporal face variable."""
    node_x = np.array([0.0, 1.0, 0.5, 1.5, 2.0])
    node_y = np.array([0.0, 0.0, 1.0, 1.0, 0.0])
    faces = np.array([[0, 1, 2], [1, 4, 3]], dtype=np.intp)
    mesh = Mesh2d(
        node_x=node_x,
        node_y=node_y,
        face_node_connectivity=Connectivity(
            data=faces,
            fill_value=-1,
            cf_role="face_node_connectivity",
            original_start_index=0,
        ),
    )
    static = MeshVariable(
        name="depth",
        location="face",
        mesh_name="mesh2d",
        shape=(2,),
        units="m",
        nodata=-999.0,
        dimensions=("mesh2d_nFaces",),
        _data=np.array([1.5, 2.5]),
    )
    temporal = MeshVariable(
        name="level",
        location="face",
        mesh_name="mesh2d",
        shape=(3, 2),
        units="m",
        dimensions=("time", "mesh2d_nFaces"),
        _data=np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
    )
    return mesh, {"depth": static, "level": temporal}


@pytest.mark.interop
class TestZarrRoundTrip:
    """The Zarr round trip; its ``to_zarr`` / ``from_zarr`` path needs xarray (the ``[interop]`` extra)."""

    def test_to_zarr_from_zarr(self, tmp_path):
        """A mesh and its variables round-trip through a Zarr store on disk."""
        pytest.importorskip("zarr")
        mesh, data_vars = _mesh_with_two_variables()
        store = str(tmp_path / "mesh.zarr")

        interop_xarray.to_zarr(mesh, data_vars, store)
        mesh2, data_vars2 = interop_xarray.from_zarr(store)

        np.testing.assert_array_equal(mesh2.node_x, mesh.node_x)
        np.testing.assert_array_equal(
            mesh2.face_node_connectivity.data, mesh.face_node_connectivity.data
        )
        np.testing.assert_array_equal(data_vars2["depth"].data, data_vars["depth"].data)
        np.testing.assert_array_equal(data_vars2["level"].data, data_vars["level"].data)
        assert data_vars2["level"].has_time is True

    def test_mixed_element_fill_survives_the_round_trip(self, tmp_path):
        """A padded face row and a padded edge row both keep their fill cells as ``-1``."""
        pytest.importorskip("zarr")
        node_x = np.array([0.0, 1.0, 0.5, 1.5])
        node_y = np.array([0.0, 0.0, 1.0, 1.0])
        internal_faces = np.array([[0, 1, 2, -1], [0, 1, 3, 2]], dtype=np.intp)
        internal_edges = np.array([[0, 1, -1], [1, 2, -1]], dtype=np.intp)
        mesh = Mesh2d(
            node_x=node_x,
            node_y=node_y,
            face_node_connectivity=Connectivity(
                data=internal_faces,
                fill_value=-1,
                cf_role="face_node_connectivity",
                original_start_index=0,
            ),
            edge_node_connectivity=Connectivity(
                data=internal_edges,
                fill_value=-1,
                cf_role="edge_node_connectivity",
                original_start_index=0,
            ),
        )
        store = str(tmp_path / "mixed.zarr")
        interop_xarray.to_zarr(mesh, {}, store)

        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=RuntimeWarning)
            mesh2, _ = interop_xarray.from_zarr(store)

        np.testing.assert_array_equal(mesh2.face_node_connectivity.data, internal_faces)
        assert mesh2.edge_node_connectivity is not None, (
            "edge_node_connectivity did not survive the round trip"
        )
        np.testing.assert_array_equal(mesh2.edge_node_connectivity.data, internal_edges)


class TestChunkedVariables:
    """The dask ``chunked_variables`` lazy path, skipped without ``pyramids-lazy``."""

    def test_chunked_read_computes_matching_reduction(self):
        """A chunked read of the sample file yields dask arrays whose reduction matches eager."""
        da = pytest.importorskip("dask.array")
        ug = MeshDataset.read_file(str(_SAMPLE_NC))
        data_vars = ug.data_vars

        chunked = interop_xarray.chunked_variables(data_vars, chunks="auto")

        assert chunked, "expected at least one chunked variable from the sample file"
        name = next(iter(chunked))
        lazy = chunked[name]
        assert isinstance(lazy, da.Array)

        eager = np.asarray(ug[name].data, dtype="float64")
        np.testing.assert_allclose(
            float(lazy.astype("float64").mean().compute()), float(np.nanmean(eager))
        )

    def test_in_memory_variable_is_wrapped_as_dask(self):
        """An in-memory variable (no source path) is wrapped via ``dask.array.from_array``."""
        da = pytest.importorskip("dask.array")
        _, data_vars = _mesh_with_two_variables()

        chunked = interop_xarray.chunked_variables(data_vars, chunks=1)

        assert isinstance(chunked["level"], da.Array)
