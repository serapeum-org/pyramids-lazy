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

    @requires_dask
    def test_reads_nothing_at_open_and_one_file_per_block_at_compute(
        self, tmp_path, monkeypatch
    ):
        """Guard the module's reason to exist: open is metadata-only, reads happen at compute.

        Counts calls to the per-block loader. Opening must trigger zero variable reads (the dask
        graph is built from shape/dtype metadata only); computing must read each file exactly once.
        An eager-read regression (a stray `.data`/`.load_array()` during graph build) would make the
        open count non-zero and fail here, where every value-level test would still pass.
        """
        import pyramids_lazy._mesh_mfdataset as mm

        _write_run(tmp_path, dated=True)
        reads = {"n": 0}
        original = mm._load_mesh_variable

        def counting(*args, **kwargs):
            reads["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(mm, "_load_mesh_variable", counting)
        lazy = mm.open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        assert reads["n"] == 0, f"open must read no variable values, read {reads['n']}"
        lazy.compute()
        assert reads["n"] == 3, (
            f"compute must read each of 3 files once, read {reads['n']}"
        )


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


def _write_one(path, *, data, dims, dated_day=None) -> None:
    """Write a single mesh file on the shared topology with arbitrary variables and dimensions.

    Args:
        path: Destination ``.nc`` path.
        data: Mapping of variable name to its array.
        dims: Mapping of variable name to its dimension-name tuple (what makes it temporal/static).
        dated_day: When set, stamps variable ``d`` with a CF time coordinate for that day of Jan 2001.
    """
    ds = MeshDataset.from_arrays(
        node_x=_NODE_X,
        node_y=_NODE_Y,
        face_node_connectivity=_TRIANGLES,
        data={name: np.asarray(array, dtype="float64") for name, array in data.items()},
        data_locations={name: "face" for name in data},
    )
    for name, dim in dims.items():
        ds[name].dimensions = dim
    if dated_day is not None:
        ds["d"].attributes = {
            **ds["d"].attributes,
            "time_values": [np.datetime64(f"2001-01-{dated_day:02d}")],
            TIME_UNITS_ATTR: "days since 2001-01-01",
            TIME_CALENDAR_ATTR: "standard",
        }
    ds.to_file(str(path))


class TestAccessors:
    """The wrapper's inspection surface, before any materialisation."""

    @requires_dask
    def test_metadata_accessors_read_from_the_head_file(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_static_and_temporal(tmp_path)
        head = MeshDataset.open_mfdataset(str(tmp_path / "run_*.nc"))
        lazy = open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        assert lazy.mesh is not None
        assert lazy.crs == head.crs
        assert sorted(lazy.data_variable_names) == ["d", "z"]
        assert set(lazy.variables) == {"d"}
        assert lazy.chunks == {"d": lazy["d"].chunks}
        assert "d" in lazy and "z" in lazy
        assert "absent" not in lazy

    @requires_dask
    def test_getitem_unknown_variable_raises_plain_keyerror(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        lazy = open_mesh_mfdataset(str(tmp_path / "run_*.nc"))
        with pytest.raises(KeyError) as exc_info:
            _ = lazy["nope"]
        assert "static" not in str(exc_info.value), (
            "unknown var must not claim to be static"
        )

    @requires_dask
    def test_repr_names_the_temporal_variables(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        text = repr(open_mesh_mfdataset(str(tmp_path / "run_*.nc")))
        assert "LazyMeshDataset" in text, f"repr should name the class, got: {text}"
        assert "d" in text, f"repr should list the temporal variable, got: {text}"

    @requires_dask
    def test_load_is_an_alias_for_compute(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        lazy = open_mesh_mfdataset(pattern)
        np.testing.assert_array_equal(lazy.load()["d"].data, lazy.compute()["d"].data)


class TestJoinMismatchRefusals:
    """The static/temporal and variable-set mismatches concat refuses, refused here too."""

    @requires_dask
    def test_static_in_head_but_temporal_in_another_is_refused(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        a = tmp_path / "a.nc"
        b = tmp_path / "b.nc"
        _write_one(a, data={"d": [1.0, 2.0]}, dims={"d": ("mesh2d_nFaces",)})
        _write_one(b, data={"d": [[3.0, 4.0]]}, dims={"d": ("time", "mesh2d_nFaces")})
        with pytest.raises(ValueError, match="static in one dataset but temporal"):
            open_mesh_mfdataset([str(a), str(b)], sort=False)

    @requires_dask
    def test_temporal_in_head_but_static_in_another_is_refused(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        a = tmp_path / "a.nc"
        b = tmp_path / "b.nc"
        _write_one(a, data={"d": [[1.0, 2.0]]}, dims={"d": ("time", "mesh2d_nFaces")})
        _write_one(b, data={"d": [3.0, 4.0]}, dims={"d": ("mesh2d_nFaces",)})
        with pytest.raises(ValueError, match="temporal in one dataset but static"):
            open_mesh_mfdataset([str(a), str(b)], sort=False)

    @requires_dask
    def test_mismatched_variable_sets_are_refused(self, tmp_path):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        a = tmp_path / "a.nc"
        b = tmp_path / "b.nc"
        _write_one(a, data={"d": [[1.0, 2.0]]}, dims={"d": ("time", "mesh2d_nFaces")})
        _write_one(
            b,
            data={"d": [[3.0, 4.0]], "e": [[5.0, 6.0]]},
            dims={"d": ("time", "mesh2d_nFaces"), "e": ("time", "mesh2d_nFaces")},
        )
        with pytest.raises(ValueError, match="same variables in every file"):
            open_mesh_mfdataset([str(a), str(b)], sort=False)


class TestBackendForwarding:
    """The additive, duck-typed backend methods core reaches via `getattr`."""

    @requires_dask
    def test_open_mesh_mfdataset_forwards_through_the_singleton(self, tmp_path):
        from pyramids_lazy import LazyMeshDataset, backend

        _write_run(tmp_path, dated=True)
        pattern = str(tmp_path / "run_*.nc")
        lazy = backend.open_mesh_mfdataset(pattern)
        assert isinstance(lazy, LazyMeshDataset)
        np.testing.assert_array_equal(
            lazy.compute()["d"].data, MeshDataset.open_mfdataset(pattern)["d"].data
        )

    def test_lazy_mesh_type_returns_the_wrapper_class(self):
        from pyramids_lazy import LazyMeshDataset, backend

        assert backend.lazy_mesh_type() is LazyMeshDataset


class TestTimeCoordinateJoin:
    """A run missing a time coordinate in some file drops the stale one, as concat does."""

    @requires_dask
    def test_a_mix_of_dated_and_undated_files_drops_the_stale_time_values(
        self, tmp_path
    ):
        from pyramids_lazy._mesh_mfdataset import open_mesh_mfdataset

        a = tmp_path / "a.nc"
        b = tmp_path / "b.nc"
        _write_one(
            a,
            data={"d": [[1.0, 2.0]]},
            dims={"d": ("time", "mesh2d_nFaces")},
            dated_day=1,
        )
        _write_one(b, data={"d": [[3.0, 4.0]]}, dims={"d": ("time", "mesh2d_nFaces")})
        pattern = [str(a), str(b)]
        eager = MeshDataset.open_mfdataset(pattern, sort=False)
        lazy = open_mesh_mfdataset(pattern, sort=False).compute()
        assert "time_values" not in lazy["d"].attributes, (
            "stale coordinate must be dropped"
        )
        assert "time_values" not in eager["d"].attributes
        np.testing.assert_array_equal(lazy["d"].data, eager["d"].data)
