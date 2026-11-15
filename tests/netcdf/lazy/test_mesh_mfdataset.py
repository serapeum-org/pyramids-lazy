"""Tests for the lazy mesh multi-file opener (:func:`open_mesh_mfdataset`).

The lazy half of serapeum-org/pyramids-core#1458: ``MeshDataset.open_mfdataset`` reads a run
eagerly and joins it with ``concat``; ``open_mesh_mfdataset`` builds the same time-stacked dataset
but holds each temporal variable as a :class:`dask.array.Array`, read out-of-core at ``.compute()``.

Core's ``MeshDataset.open_mfdataset(chunks=...)`` forwarder is the pyramids-core half and does not
exist yet, so these tests call :func:`open_mesh_mfdataset` **directly** and assert parity against the
eager ``MeshDataset.open_mfdataset`` that ships today. The fixture mirrors core's own
``tests/netcdf/ugrid/test_parity_batch.py`` (three single-step files whose names sort WRONG lexically
-- ``t10`` before ``t2`` -- so the dated sort is actually exercised).
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.netcdf.ugrid import MeshDataset
from pyramids.netcdf.ugrid.dataset import TIME_CALENDAR_ATTR, TIME_UNITS_ATTR

from tests._marks import requires_dask

pytestmark = pytest.mark.lazy

_NODE_X = np.array([0.0, 1.0, 1.0, 0.0])
_NODE_Y = np.array([0.0, 0.0, 1.0, 1.0])
_TRIANGLES = np.array([[0, 1, 2], [0, 2, 3]])


def _write_run(directory, *, dated: bool) -> list:
    """Write three single-step mesh files whose names sort wrong lexically (t10 before t2)."""
    paths = []
    for label, day in (("t9", 9), ("t10", 10), ("t2", 2)):
        ds = MeshDataset.from_arrays(
            node_x=_NODE_X,
            node_y=_NODE_Y,
            face_node_connectivity=_TRIANGLES,
            data={"d": np.array([[float(day), float(day) * 2]])},
            data_locations={"d": "face"},
        )
        ds["d"].dimensions = ("time", "mesh2d_nFaces")
        if dated:
            ds["d"].attributes = {
                **ds["d"].attributes,
                "time_values": [np.datetime64(f"2001-01-{day:02d}")],
                TIME_UNITS_ATTR: "days since 2001-01-01",
                TIME_CALENDAR_ATTR: "standard",
            }
        path = directory / f"run_{label}.nc"
        ds.to_file(str(path))
        paths.append(path)
    return paths


def _write_static_and_temporal(directory) -> list:
    """Write two dated files carrying one temporal variable ``d`` and one static variable ``z``."""
    paths = []
    for label, day in (("a", 1), ("b", 2)):
        ds = MeshDataset.from_arrays(
            node_x=_NODE_X,
            node_y=_NODE_Y,
            face_node_connectivity=_TRIANGLES,
            data={
                "d": np.array([[float(day), float(day) * 2]]),
                "z": np.array([10.0, 20.0]),
            },
            data_locations={"d": "face", "z": "face"},
        )
        ds["d"].dimensions = ("time", "mesh2d_nFaces")
        ds["z"].dimensions = ("mesh2d_nFaces",)
        ds["d"].attributes = {
            **ds["d"].attributes,
            "time_values": [np.datetime64(f"2001-01-{day:02d}")],
            TIME_UNITS_ATTR: "days since 2001-01-01",
            TIME_CALENDAR_ATTR: "standard",
        }
        path = directory / f"run_{label}.nc"
        ds.to_file(str(path))
        paths.append(path)
    return paths


class TestLaziness:
    """The result is a lazy wrapper over dask arrays, not a materialised dataset."""

    @requires_dask
    def test_returns_a_lazy_mesh_dataset(self, tmp_path):
        from pyramids_lazy import LazyMeshDataset
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        lazy = open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        assert isinstance(lazy, LazyMeshDataset)

    @requires_dask
    def test_a_temporal_variable_is_a_dask_array(self, tmp_path):
        import dask.array as da

        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        lazy = open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        assert isinstance(lazy["d"], da.Array)
        # The stacked time axis is three steps long but nothing is read until compute.
        assert lazy["d"].shape[0] == 3

    @requires_dask
    def test_one_chunk_per_file_by_default(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        lazy = open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        # Three single-step files -> three blocks of length one along the time axis.
        assert lazy["d"].chunks[0] == (1, 1, 1)


class TestEagerParity:
    """`.compute()` must equal the eager `MeshDataset.open_mfdataset` byte for byte."""

    @requires_dask
    def test_compute_matches_eager_dated_order(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        eager = MeshDataset.open_mfdataset(pattern)
        lazy = open_mesh_mfdataset(pattern).compute()
        # A dated run comes back in time order (t2, t9, t10), not lexical.
        assert lazy["d"].data[:, 0].tolist() == [2.0, 9.0, 10.0]
        np.testing.assert_array_equal(lazy["d"].data, eager["d"].data)

    @requires_dask
    def test_compute_matches_eager_sort_false(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        eager = MeshDataset.open_mfdataset(pattern, sort=False)
        lazy = open_mesh_mfdataset(pattern, sort=False).compute()
        assert lazy["d"].data[:, 0].tolist() == [10.0, 2.0, 9.0]
        np.testing.assert_array_equal(lazy["d"].data, eager["d"].data)

    @requires_dask
    def test_joined_time_values_match_eager(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        eager = MeshDataset.open_mfdataset(pattern)
        lazy = open_mesh_mfdataset(pattern).compute()
        assert lazy.time_values == eager.time_values
        assert lazy["d"].n_time_steps == 3

    @requires_dask
    def test_an_explicit_sequence_is_stacked_in_that_order(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        paths = _write_run(tmp_path, dated=False)
        ordered = [str(paths[2]), str(paths[0]), str(paths[1])]  # t2, t9, t10
        eager = MeshDataset.open_mfdataset(ordered, sort=False)
        lazy = open_mesh_mfdataset(ordered, sort=False).compute()
        assert lazy["d"].data[:, 0].tolist() == [2.0, 9.0, 10.0]
        np.testing.assert_array_equal(lazy["d"].data, eager["d"].data)

    @requires_dask
    def test_a_single_path_is_accepted(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        paths = _write_run(tmp_path, dated=True)
        lazy = open_mesh_mfdataset(str(paths[0])).compute()
        assert lazy["d"].n_time_steps == 1


class TestStaticVariables:
    """A static variable is carried from the head file unchanged, exactly as concat does."""

    @requires_dask
    def test_static_variable_is_not_stacked_but_survives_compute(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_static_and_temporal(tmp_path)
        pattern = str(tmp_path / "run_*.nc")
        eager = MeshDataset.open_mfdataset(pattern)
        lazy = open_mesh_mfdataset(pattern)
        # `z` is static: it is not one of the lazily-stacked temporal arrays.
        assert "z" not in lazy.temporal_variable_names
        assert "d" in lazy.temporal_variable_names
        with pytest.raises(KeyError, match="static"):
            _ = lazy["z"]
        materialised = lazy.compute()
        np.testing.assert_array_equal(materialised["z"].data, eager["z"].data)
        assert materialised["z"].n_time_steps == 0


class TestRefusals:
    """The lazy path refuses the same malformed runs the eager path refuses."""

    @requires_dask
    def test_a_pattern_matching_nothing_is_refused(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        with pytest.raises(ValueError, match="matches no file"):
            open_mesh_mfdataset(str(tmp_path / "absent_*.nc"))

    @requires_dask
    def test_an_empty_sequence_is_refused(self):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        with pytest.raises(ValueError, match="nothing to open"):
            open_mesh_mfdataset([])

    @requires_dask
    def test_a_file_on_a_different_topology_is_refused(self, tmp_path):
        from pyramids.netcdf.ugrid.dataset import AlignmentError

        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        good = _write_run(tmp_path, dated=True)[0]
        other = MeshDataset.from_arrays(
            node_x=np.array([0.0, 9.0, 9.0]),
            node_y=np.array([0.0, 0.0, 9.0]),
            face_node_connectivity=np.array([[0, 1, 2]]),
            data={"d": np.array([[1.0]])},
            data_locations={"d": "face"},
        )
        other["d"].dimensions = ("time", "mesh2d_nFaces")
        other_path = tmp_path / "other.nc"
        other.to_file(str(other_path))
        with pytest.raises(AlignmentError):
            open_mesh_mfdataset([str(good), str(other_path)], sort=False)


class TestChunks:
    """A non-None `chunks` rechunks each stacked variable."""

    @requires_dask
    def test_chunks_rechunks_the_time_axis(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        lazy = open_mesh_mfdataset(pattern, chunks={0: 3})
        # Rechunked to a single 3-step block along time, and still correct on compute.
        assert lazy["d"].chunks[0] == (3,)
        np.testing.assert_array_equal(
            lazy.compute()["d"].data, MeshDataset.open_mfdataset(pattern)["d"].data
        )
